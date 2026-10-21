import json
import uuid
from datetime import date
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user, require_admin
from app.models.financial import AuditLog, Document, User
from app.schemas.documents import DocumentDetailResponse, DocumentResponse, DocumentUpdate
from app.services import lifecycle
from app.services.ingest import ingest_document, read_pages
from app.services import period_detector

router = APIRouter(prefix="/api/documents", tags=["documents"])

UPLOAD_DIR = Path(__file__).resolve().parents[2] / settings.UPLOAD_DIR


def _reload(doc: Document) -> DocumentResponse:
    return DocumentResponse.model_validate(doc)


@router.post("", response_model=DocumentResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    period_label: str = Form(...),
    start_date: date = Form(...),
    end_date: date = Form(...),
    doc_type: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Store an uploaded PDF and register it as a document awaiting processing."""
    original = Path(file.filename or "upload.pdf").name
    if not original.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored = UPLOAD_DIR / f"{uuid.uuid4().hex}_{original}"
    stored.write_bytes(await file.read())

    # Validate against document content
    try:
        pages = read_pages(str(stored))
        ok, msg, cands = await period_detector.validate_against(period_label, start_date, end_date, pages)
        if not ok:
            try:
                stored.unlink(missing_ok=True)
            except Exception:
                pass
            raise HTTPException(status_code=400, detail=msg)
    except HTTPException:
        raise
    except Exception:
        pass

    doc = Document(
        filename=original,
        stored_path=str(stored),
        doc_type=doc_type,
        period_label=period_label,
        period_start=start_date,
        period_end=end_date,
        status=lifecycle.UPLOADED,
    )
    db.add(doc)
    await db.flush()
    db.add(
        AuditLog(
            user_email=user.email,
            action="upload",
            entity="document",
            entity_id=doc.id,
            after_json=json.dumps({"filename": original, "period_label": period_label}),
        )
    )
    await db.commit()
    await db.refresh(doc)
    return _reload(doc)


@router.get("", response_model=List[DocumentResponse])
async def list_documents(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(Document).order_by(Document.uploaded_at.desc()))
    return list(result.scalars().all())


@router.get("/{document_id}", response_model=DocumentDetailResponse)
async def get_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    detail = DocumentDetailResponse.model_validate(doc)
    if doc.raw_json:
        try:
            detail.review = json.loads(doc.raw_json)
        except json.JSONDecodeError:
            pass
    return detail


@router.post("/{document_id}/process", response_model=DocumentResponse)
async def process_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Run the hybrid extraction and persist the resulting period for review."""
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        pages = read_pages(doc.stored_path)
        ok, msg, cands = await period_detector.validate_against(doc.period_label, doc.period_start, doc.period_end, pages)
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
    except HTTPException:
        raise
    except Exception:
        pass  # don't block if detection fails
    try:
        await ingest_document(document_id, db)
    except lifecycle.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    db.add(
        AuditLog(user_email=user.email, action="process", entity="document", entity_id=document_id)
    )
    await db.commit()
    doc = await db.get(Document, document_id)
    return _reload(doc)


@router.patch("/{document_id}", response_model=DocumentResponse)
async def update_document(
    document_id: int,
    payload: DocumentUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Update a document's period label, dates, or type after upload."""
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.status == "published":
        raise HTTPException(status_code=409, detail="Cannot edit a published document")

    if payload.period_label is not None:
        doc.period_label = payload.period_label
    if payload.start_date is not None:
        doc.period_start = payload.start_date
    if payload.end_date is not None:
        doc.period_end = payload.end_date
    if payload.doc_type is not None:
        doc.doc_type = payload.doc_type

    db.add(
        AuditLog(
            user_email=user.email,
            action="update",
            entity="document",
            entity_id=doc.id,
            after_json=json.dumps(
                {
                    "period_label": doc.period_label,
                    "start_date": str(doc.period_start),
                    "end_date": str(doc.period_end),
                    "doc_type": doc.doc_type,
                }
            ),
        )
    )
    await db.commit()
    await db.refresh(doc)
    return _reload(doc)


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        if doc.stored_path:
            from pathlib import Path

            p = Path(doc.stored_path)
            if p.exists():
                p.unlink()
    except Exception:
        pass
    db.delete(doc)
    db.add(
        AuditLog(user_email=user.email, action="delete", entity="document", entity_id=document_id)
    )
    await db.commit()
    return None


@router.post("/{document_id}/publish", response_model=DocumentResponse)
async def publish_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Mark a processed document (and its period) as published."""
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Unknown document")
    try:
        doc.status = lifecycle.advance(doc.status, lifecycle.PUBLISHED)
    except lifecycle.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.add(
        AuditLog(user_email=user.email, action="publish", entity="document", entity_id=document_id)
    )
    await db.commit()
    await db.refresh(doc)
    return _reload(doc)
