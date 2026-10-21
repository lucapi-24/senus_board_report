"""Hybrid ingestion service.

Runs both independent extractors over an uploaded PDF — the deterministic regex
parser and the LLM extractor — reconciles them, and persists a Period whose
line items carry their review status, confidence, source page and the competing
value whenever the two extractors disagreed.

Nothing is silently corrected: a flagged item keeps the deterministic value and
stores the LLM's value in ``original_value`` so a human can adjudicate.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Tuple

import pdfplumber
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.financial import (
    BalanceSheetItem,
    CashFlowItem,
    Document,
    KPI,
    PnLItem,
    Period,
    Validation,
)
from app.services import lifecycle
from app.services.deterministic import compute_kpis, parse_tables
from app.services.llm_extract import STATEMENTS, extract_structured
from app.services.period_detector import get_column_for_period
from app.services.reconciliation import reconcile, review_status_for
from app.services.validation import validate_period

ITEM_MODEL = {
    "pnl": PnLItem,
    "balance_sheet": BalanceSheetItem,
    "cash_flow": CashFlowItem,
}


def read_pages(pdf_path: str) -> List[Tuple[int, str]]:
    if not pdf_path:
        raise ValueError("Document has no stored file")
    pages: List[Tuple[int, str]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for idx, page in enumerate(pdf.pages, start=1):
            pages.append((idx, page.extract_text() or ""))
    return pages


def build_proposal(result: Dict[str, Any]) -> Dict[str, Any]:
    """The review payload shown to a human (and stored on the document)."""
    proposal: Dict[str, Any] = {statement: [i.to_dict() for i in result[statement]] for statement in STATEMENTS}
    proposal["summary"] = result["summary"]
    return proposal


def _chosen(result: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """label -> value, using each item's chosen value (deterministic preferred)."""
    chosen: Dict[str, Dict[str, float]] = {}
    for statement in STATEMENTS:
        chosen[statement] = {
            item.line_item: float(item.value)
            for item in result[statement]
            if item.value is not None
        }
    return chosen


def _provenance(result: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    provenance: Dict[str, Dict[str, int]] = {}
    for statement in STATEMENTS:
        provenance[statement] = {
            item.line_item: item.page for item in result[statement] if item.page
        }
    return provenance


def _months_between(start, end) -> int:
    if not start or not end:
        return 0
    return max(1, round((end - start).days / 30.44))


def _metadata(doc: Document, chosen: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "document_title": doc.filename,
        "currency": "EUR",
        "period_months": _months_between(doc.period_start, doc.period_end),
        "audited": False,
        "method": "hybrid (regex + llm, reconciled)",
        "review": result["summary"],
    }


async def _persist(
    db: AsyncSession,
    doc: Document,
    result: Dict[str, Any],
    proposal: Dict[str, Any],
    page_count: int,
) -> None:
    label = doc.period_label
    existing = (await db.execute(select(Period).where(Period.label == label))).scalars().all()
    for period in existing:
        await db.delete(period)
    await db.flush()

    chosen = _chosen(result)
    kpis = compute_kpis(chosen["pnl"], chosen["balance_sheet"], chosen["cash_flow"])
    issues = [
        issue.as_dict()
        for issue in validate_period(chosen["pnl"], chosen["balance_sheet"], chosen["cash_flow"])
    ]

    period = Period(
        label=label,
        start_date=doc.period_start,
        end_date=doc.period_end,
        source_doc_id=doc.id,
        meta_json=json.dumps(_metadata(doc, chosen, result)),
        provenance_json=json.dumps(_provenance(result)),
    )
    db.add(period)
    await db.flush()

    for statement in STATEMENTS:
        model = ITEM_MODEL[statement]
        for item in result[statement]:
            if item.value is None:
                continue
            original = None
            if item.status in ("sign_mismatch", "value_mismatch") and item.llm_value is not None:
                original = Decimal(str(round(item.llm_value, 2)))
            db.add(
                model(
                    period_id=period.id,
                    line_item=item.line_item,
                    value_eur=Decimal(str(round(item.value, 2))),
                    page=item.page,
                    review_status=review_status_for(item.status),
                    confidence=item.confidence,
                    original_value=original,
                )
            )

    for metric, value in kpis.items():
        db.add(
            KPI(
                period_id=period.id,
                metric=metric,
                value=Decimal(str(round(float(value), 6))),
                kind="derived",
            )
        )
    for issue in issues:
        db.add(
            Validation(
                period_id=period.id,
                rule=issue["rule"],
                severity=issue["severity"],
                message=issue["message"],
                expected=issue.get("expected"),
                actual=issue.get("actual"),
            )
        )

    doc.page_count = page_count
    doc.extracted_at = datetime.now(timezone.utc)
    doc.raw_json = json.dumps(proposal)


async def recompute_derived(db: AsyncSession, period_id: int) -> None:
    """Recompute KPIs and validations after a human confirms/corrects an item."""
    raw: Dict[str, Dict[str, float]] = {}
    for statement, model in ITEM_MODEL.items():
        rows = (
            await db.execute(select(model).where(model.period_id == period_id))
        ).scalars().all()
        raw[statement] = {row.line_item: float(row.value_eur) for row in rows}

    kpis = compute_kpis(raw["pnl"], raw["balance_sheet"], raw["cash_flow"])
    issues = [
        issue.as_dict()
        for issue in validate_period(raw["pnl"], raw["balance_sheet"], raw["cash_flow"])
    ]

    await db.execute(delete(KPI).where(KPI.period_id == period_id))
    await db.execute(delete(Validation).where(Validation.period_id == period_id))
    for metric, value in kpis.items():
        db.add(
            KPI(
                period_id=period_id,
                metric=metric,
                value=Decimal(str(round(float(value), 6))),
                kind="derived",
            )
        )
    for issue in issues:
        db.add(
            Validation(
                period_id=period_id,
                rule=issue["rule"],
                severity=issue["severity"],
                message=issue["message"],
                expected=issue.get("expected"),
                actual=issue.get("actual"),
            )
        )
    await db.flush()


async def ingest_document(document_id: int, db: AsyncSession) -> Dict[str, Any]:
    """Process an uploaded document end to end. Returns the review proposal."""
    doc = await db.get(Document, document_id)
    if doc is None:
        raise ValueError(f"Unknown document: {document_id}")

    doc.status = lifecycle.advance(doc.status, lifecycle.PROCESSING)
    doc.error = None
    await db.commit()

    try:
        pages = read_pages(doc.stored_path)
        column = get_column_for_period(doc.period_start, doc.period_end, pages)
        # Only 2 table columns are supported by the regex parser (0=current, 1=comparative)
        if column is None or column > 1:
            column = 0
        tables = parse_tables(pages, column=column)
        llm_lines = await extract_structured(pages, doc.period_label or "", column=column)
        result = reconcile(tables, llm_lines)
        proposal = build_proposal(result)
        await _persist(db, doc, result, proposal, len(pages))
        doc.status = lifecycle.advance(doc.status, lifecycle.EXTRACTED)
        await db.commit()
        return proposal
    except Exception as exc:  # record and surface the failure
        doc.status = lifecycle.ERROR
        doc.error = str(exc)
        await db.commit()
        raise
