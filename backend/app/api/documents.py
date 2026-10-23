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
    ConfirmPeriodsRequest,
    DocumentDetailResponse,
    DocumentResponse,
    DetectedPeriod,
    DocumentUpdate,
)
from app.schemas.financial import PeriodResponse
from app.services import lifecycle
from app.services.ingest import (
    PeriodReplacementRequired,
    ingest_document,
    read_pages,
    require_replacement_confirmation,
)
from app.services import period_detector

router = APIRouter(prefix="/api/documents", tags=["documents"])

UPLOAD_DIR = Path(__file__).resolve().parents[2] / settings.UPLOAD_DIR


def _reload(doc: Document) -> DocumentResponse:
    return _document_response(doc)


def _replacement_confirmation_error(exc: PeriodReplacementRequired) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "period_replacement_confirmation_required",
            "periods": exc.labels,
            "message": (
                "Processing would replace existing periods: "
                f"{', '.join(exc.labels)}. Confirm to continue."
            ),
        },
    )


def _period_state(doc: Document) -> dict:
    if not doc.periods_detected_json:
        return {"periods": [], "statement_periods": {}, "confirmed": False, "validation_errors": []}
    try:
        state = json.loads(doc.periods_detected_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Stored detected periods are invalid.") from exc
    if isinstance(state, list):
        return {"periods": state, "statement_periods": {}, "confirmed": False, "validation_errors": []}
    if not isinstance(state, dict) or not isinstance(state.get("periods"), list):
        raise HTTPException(status_code=500, detail="Stored detected periods are invalid.")
    state.setdefault("statement_periods", {})
    state.setdefault("confirmed", False)
    state.setdefault("validation_errors", [])
    return state


def _document_response(doc: Document) -> DocumentResponse:
    response = DocumentResponse.model_validate(doc)
    state = _period_state(doc)
    response.detected_periods = [
        DetectedPeriod.model_validate(period) for period in state["periods"]
    ]
    response.periods_confirmed = state["confirmed"]
    response.period_validation_errors = state["validation_errors"]
    return response


@router.post("", response_model=DocumentResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
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

    detected_periods = [
        {
            "label": p.label,
            "start_date": p.start_date.isoformat() if p.start_date else None,
            "end_date": p.end_date.isoformat() if p.end_date else None,
            "column": p.column,
            "pages": p.pages,
            "source": p.source,
            "score": p.score,
            "is_primary": p.is_primary,
            "duration_months": period_detector.period_duration_months(p.start_date, p.end_date)
            if p.start_date and p.end_date else None,
        }
        for p in detected
    ]
    statement_periods, _ = period_detector.statement_header_periods(pages)
    stored_statement_periods = {
        statement: [[start.isoformat(), end.isoformat()] for start, end in values]
        for statement, values in statement_periods.items()
    }
    validation_errors = period_detector.validate_detected_periods(
        detected_periods, statement_periods
    )

    # Store detected periods on the document for later confirmation
    doc = Document(
        filename=original,
        stored_path=str(stored),
        doc_type=doc_type,
        status=lifecycle.UPLOADED,
        periods_detected_json=json.dumps(
            {
                "periods": detected_periods,
                "statement_periods": stored_statement_periods,
                "confirmed": False,
                "validation_errors": validation_errors,
            }
        ),
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
            after_json=json.dumps({"filename": original}),
        )
    )
    await db.commit()
    await db.refresh(doc)

    # Build response including detected periods
    return _document_response(doc)


@router.get("", response_model=List[DocumentResponse])
async def list_documents(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(Document).order_by(Document.uploaded_at.desc()))
    return [_document_response(doc) for doc in result.scalars().all()]


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
    period_response = _document_response(doc)
    detail.detected_periods = period_response.detected_periods
    detail.periods_confirmed = period_response.periods_confirmed
    detail.period_validation_errors = period_response.period_validation_errors
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


@router.post("/{document_id}/confirm-periods", response_model=DocumentResponse)
async def confirm_document_periods(
    document_id: int,
    payload: ConfirmPeriodsRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Confirm or manually correct dates before any financial extraction."""
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status not in (lifecycle.UPLOADED, lifecycle.ERROR):
        raise HTTPException(status_code=409, detail="Periods can only be confirmed before processing.")

    state = _period_state(doc)
    detected_periods = state["periods"]
    expected_columns = {period.get("column") for period in detected_periods}
    supplied_columns = [period.column for period in payload.periods]
    if len(set(supplied_columns)) != len(supplied_columns) or set(supplied_columns) != expected_columns:
        raise HTTPException(status_code=422, detail="Provide dates for every detected column exactly once.")

    from app.services.period_detector import derive_label

    previous_by_column = {period.get("column"): period for period in detected_periods}
    updated_periods = []
    manually_changed = []
    for item in payload.periods:
        previous = previous_by_column[item.column]
        label = derive_label(item.start_date, item.end_date)
        updated = {
            **previous,
            "label": label,
            "start_date": item.start_date.isoformat(),
            "end_date": item.end_date.isoformat(),
            "duration_months": period_detector.period_duration_months(item.start_date, item.end_date),
        }
        updated_periods.append(updated)
        old_dates = (previous.get("start_date"), previous.get("end_date"))
        new_dates = (item.start_date.isoformat(), item.end_date.isoformat())
        if old_dates != new_dates:
            manually_changed.append(
                {
                    "column": item.column,
                    "before": {
                        "start_date": old_dates[0],
                        "end_date": old_dates[1],
                        "label": previous.get("label"),
                    },
                    "after": {
                        "start_date": new_dates[0],
                        "end_date": new_dates[1],
                        "label": label,
                    },
                }
            )

    errors = period_detector.validate_detected_periods(
        updated_periods, _parse_statement_periods(state.get("statement_periods", {}))
    )
    state.update(
        periods=updated_periods,
        confirmed=not errors,
        validation_errors=errors,
    )
    doc.periods_detected_json = json.dumps(state)
    doc.period_label = next((period["label"] for period in updated_periods if period.get("column") == 0), None)
    primary = next((period for period in updated_periods if period.get("column") == 0), None)
    if primary:
        doc.period_start = date.fromisoformat(primary["start_date"])
        doc.period_end = date.fromisoformat(primary["end_date"])
    if manually_changed:
        doc.periods_manually_edited = True
        doc.edited_by = user.email
        doc.edited_at = datetime.now(timezone.utc)
        for change in manually_changed:
            db.add(
                AuditLog(
                    user_email=user.email,
                    action="edit_period_dates",
                    entity="document",
                    entity_id=doc.id,
                    before_json=json.dumps(change["before"]),
                    after_json=json.dumps(change["after"]),
                )
            )
    db.add(
        AuditLog(
            user_email=user.email,
            action="confirm_periods" if not errors else "validate_periods",
            entity="document",
            entity_id=doc.id,
            after_json=json.dumps({"confirmed": not errors, "errors": errors}),
        )
    )
    await db.commit()
    await db.refresh(doc)
    return _document_response(doc)


def _parse_statement_periods(state: dict) -> dict:
    parsed = {}
    for statement, values in state.items():
        parsed[statement] = [
            (date.fromisoformat(start), date.fromisoformat(end))
            for start, end in values
        ]
    return parsed


@router.post("/{document_id}/process", response_model=DocumentResponse)
async def process_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
    replace_existing: bool = False,
):
    """Process only periods that passed the explicit confirmation step."""
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    state = _period_state(doc)
    if not state["confirmed"] or state["validation_errors"]:
        raise HTTPException(status_code=409, detail="Confirm all reporting periods before processing.")
    detected = state["periods"]
    if not detected or any(not p.get("start_date") or not p.get("end_date") for p in detected):
        raise HTTPException(status_code=409, detail="Confirm valid dates for every detected column.")

    period_labels = [
        period_detector.derive_label(
            date.fromisoformat(item["start_date"]),
            date.fromisoformat(item["end_date"]),
        )
        for item in detected
    ]
    existing_labels = (
        await db.execute(select(Period.label).where(Period.label.in_(period_labels)))
    ).scalars().all()
    try:
        require_replacement_confirmation(existing_labels, replace_existing)
    except PeriodReplacementRequired as exc:
        raise _replacement_confirmation_error(exc) from exc

    for pd in sorted(detected, key=lambda period: period["column"]):
        start_d = date.fromisoformat(pd["start_date"])
        end_d = date.fromisoformat(pd["end_date"])
        label = period_detector.derive_label(start_d, end_d)
        doc.period_label = label
        doc.period_start = start_d
        doc.period_end = end_d
        try:
            await ingest_document(
                document_id,
                db,
                column=pd["column"],
                period_label=label,
                start_date=start_d,
                end_date=end_d,
                replace_existing=replace_existing,
            )
        except PeriodReplacementRequired as exc:
            raise _replacement_confirmation_error(exc) from exc
    primary = next((period for period in detected if period["column"] == 0), detected[0])
    doc.period_label = period_detector.derive_label(
        date.fromisoformat(primary["start_date"]), date.fromisoformat(primary["end_date"])
    )
    doc.period_start = date.fromisoformat(primary["start_date"])
    doc.period_end = date.fromisoformat(primary["end_date"])

    db.add(
        AuditLog(
            user_email=user.email,
            action="process",
            entity="document",
            entity_id=document_id,
            after_json=json.dumps(
                {
                    "replace_existing": replace_existing,
                    "replaced_periods": existing_labels,
                }
            ),
        )
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
    """Update a document's type. Reporting dates are edited via confirmation."""
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

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
