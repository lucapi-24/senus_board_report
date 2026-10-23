import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user, require_admin
from app.models.financial import AuditLog, Document, Period, User
from app.schemas.documents import (
    DocumentDetailResponse,
    DocumentResponse,
    DocumentUpdate,
)
from app.schemas.financial import PeriodResponse
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
    period_label: Optional[str] = Form(None),
    start_date: Optional[date] = Form(None),
    end_date: Optional[date] = Form(None),
    doc_type: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Store an uploaded PDF and register it as a document awaiting processing.

    period_label / start_date / end_date are optional. If omitted, the system
    auto-detects periods from the document's table headers and returns them so
    the user can confirm or correct in the review UI.
    """
    original = Path(file.filename or "upload.pdf").name
    if not original.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored = UPLOAD_DIR / f"{uuid.uuid4().hex}_{original}"
    stored.write_bytes(await file.read())

    # Read pages and detect periods from document headers
    pages = read_pages(str(stored))
    detected = period_detector.detect_all_periods(pages)

    # Build detected_periods for the response
    detected_periods = [
        {
            "label": p.label,
            "start_date": p.start_date.isoformat(),
            "end_date": p.end_date.isoformat(),
            "column": p.column,
            "pages": p.pages,
            "source": p.source,
            "score": p.score,
            "is_primary": p.is_primary,
        }
        for p in detected
    ]

    # Store detected periods on the document for later confirmation
    doc = Document(
        filename=original,
        stored_path=str(stored),
        doc_type=doc_type,
        period_label=period_label,
        period_start=start_date,
        period_end=end_date,
        status=lifecycle.UPLOADED,
        periods_detected_json=json.dumps(detected_periods),
        periods_manually_edited=False,
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

    # Build response including detected periods
    response = DocumentResponse.model_validate(doc)
    response.detected_periods = detected_periods
    return response


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
            stored_review = json.loads(doc.raw_json)
        except json.JSONDecodeError:
            stored_review = None
        if isinstance(stored_review, dict) and isinstance(stored_review.get("periods"), dict):
            detail.reviews_by_period = stored_review["periods"]
        elif stored_review is not None:
            detail.review = stored_review
    return detail


@router.post("/{document_id}/process", response_model=DocumentResponse)
async def process_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Run the hybrid extraction and persist the resulting period(s) for review.

    Processes each detected period in the document using its corresponding
    table column. If no periods were detected, falls back to the document's
    stored period_label/dates.
    """
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    pages = read_pages(doc.stored_path)

    # Determine which period(s) to process
    detected = []
    if doc.periods_detected_json:
        try:
            detected = json.loads(doc.periods_detected_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail="Stored detected periods are invalid.") from exc
        if not isinstance(detected, list) or any(not isinstance(period, dict) for period in detected):
            raise HTTPException(status_code=500, detail="Stored detected periods are invalid.")

    if detected:
        # The PDF's detected columns are processed together without a manual
        # label-confirmation step.
        for pd in detected:
            column = pd.get("column", 0)
            start = pd.get("start_date")
            end = pd.get("end_date")
            label = pd.get("label")
            if not (start and end and label):
                continue
            start_d = date.fromisoformat(start)
            end_d = date.fromisoformat(end)
            # Temporarily set doc fields so ingest_document uses them
            doc.period_label = label
            doc.period_start = start_d
            doc.period_end = end_d
            await ingest_document(document_id, db, column=column, period_label=label, start_date=start_d, end_date=end_d)
    else:
        # Fallback: use stored document dates
        if not (doc.period_label and doc.period_start and doc.period_end):
            raise HTTPException(
                status_code=400,
                detail="No periods detected in document and no period label/dates provided.",
            )
        await ingest_document(document_id, db)

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
    """Update a document's period label, dates, or type after upload.

    If start_date/end_date change, the label is recalculated from the dates
    (fiscal year ends 30 June). Marks the document as manually edited.
    """
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.status == "published" and any(
        value is not None
        for value in (payload.period_label, payload.start_date, payload.end_date)
    ):
        raise HTTPException(
            status_code=409,
            detail="Cannot change the reporting period of a published document",
        )

    dates_changed = False
    if payload.start_date is not None:
        doc.period_start = payload.start_date
        dates_changed = True
    if payload.end_date is not None:
        doc.period_end = payload.end_date
        dates_changed = True

    # Recalculate label from dates if dates changed
    if dates_changed and doc.period_start and doc.period_end:
        from app.services.period_detector import derive_label
        doc.period_label = derive_label(doc.period_start, doc.period_end)

    if payload.period_label is not None:
        doc.period_label = payload.period_label
    if payload.doc_type is not None:
        doc.doc_type = payload.doc_type

    if dates_changed or payload.period_label is not None:
        doc.periods_manually_edited = True
        doc.edited_by = user.email
        doc.edited_at = datetime.now(timezone.utc)

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


@router.get("/{document_id}/periods", response_model=List[PeriodResponse])
async def list_document_periods(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all periods associated with a document."""
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    result = await db.execute(select(Period).where(Period.source_doc_id == document_id))
    return list(result.scalars().all())


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.stored_path:
        path = Path(doc.stored_path)
        if path.exists():
            path.unlink()
    await db.delete(doc)
    db.add(
        AuditLog(user_email=user.email, action="delete", entity="document", entity_id=document_id)
    )
    await db.commit()
    return None
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


@router.get("/{document_id}/file")
async def download_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Serve the original uploaded PDF file."""
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc.stored_path:
        raise HTTPException(status_code=404, detail="Document has no stored file")
    from pathlib import Path

    p = Path(doc.stored_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Document file not found on server")
    from fastapi.responses import FileResponse

    return FileResponse(path=p, media_type="application/pdf", filename=doc.filename)
