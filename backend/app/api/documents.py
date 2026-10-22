import json
import uuid
from datetime import date, datetime
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
            import json as _json
            detected = _json.loads(doc.periods_detected_json)
        except Exception:
            detected = []

    if detected:
        # Process each detected period using its column
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

    if doc.status == "published":
        raise HTTPException(status_code=409, detail="Cannot edit a published document")

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


@router.post("/{document_id}/confirm-periods", response_model=DocumentResponse)
async def confirm_periods(
    document_id: int,
    period_labels: List[str],
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Confirm which detected periods to process for this document.

    Stores the confirmed period labels on the document so that a subsequent
    /process call knows which periods to extract.
    """
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Validate that the labels exist in detected periods
    detected = []
    if doc.periods_detected_json:
        try:
            import json as _json
            detected = _json.loads(doc.periods_detected_json)
        except Exception:
            detected = []

    detected_labels = {p["label"] for p in detected}
    for label in period_labels:
        if label not in detected_labels:
            raise HTTPException(
                status_code=400,
                detail=f"Label '{label}' not in detected periods: {sorted(detected_labels)}",
            )

    doc.period_label = period_labels[0] if period_labels else None
    doc.periods_manually_edited = True
    doc.edited_by = user.email
    doc.edited_at = datetime.now(timezone.utc)

    # Store confirmed labels in meta_json for reference
    import json as _json
    meta = doc.doc_meta or {}
    meta["confirmed_period_labels"] = period_labels
    doc.meta_json = _json.dumps(meta)

    db.add(
        AuditLog(
            user_email=user.email,
            action="confirm_periods",
            entity="document",
            entity_id=doc.id,
            after_json=_json.dumps({"confirmed_period_labels": period_labels}),
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
