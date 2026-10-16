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
from app.models.financial import Document
from app.schemas.documents import DocumentDetailResponse, DocumentResponse
from app.services import lifecycle
from app.services.ingest import ingest_document

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
):
    """Store an uploaded PDF and register it as a document awaiting processing."""
    original = Path(file.filename or "upload.pdf").name
    if not original.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored = UPLOAD_DIR / f"{uuid.uuid4().hex}_{original}"
    stored.write_bytes(await file.read())

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
    await db.commit()
    await db.refresh(doc)
    return _reload(doc)


@router.get("", response_model=List[DocumentResponse])
async def list_documents(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).order_by(Document.uploaded_at.desc()))
    return list(result.scalars().all())


@router.get("/{document_id}", response_model=DocumentDetailResponse)
async def get_document(document_id: int, db: AsyncSession = Depends(get_db)):
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
async def process_document(document_id: int, db: AsyncSession = Depends(get_db)):
    """Run the hybrid extraction and persist the resulting period for review."""
    try:
        await ingest_document(document_id, db)
    except lifecycle.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    doc = await db.get(Document, document_id)
    return _reload(doc)


@router.post("/{document_id}/publish", response_model=DocumentResponse)
async def publish_document(document_id: int, db: AsyncSession = Depends(get_db)):
    """Mark a processed document (and its period) as published."""
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Unknown document")
    try:
        doc.status = lifecycle.advance(doc.status, lifecycle.PUBLISHED)
    except lifecycle.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    await db.refresh(doc)
    return _reload(doc)
