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
from app.services.llm_extract import (
    STATEMENTS,
    extract_structured,
    parse_value,
    select_statement_pages,
)
from app.services.metrics import CATALOG_BY_KEY, kpi_metadata
from app.services.note_crosscheck import find_note_mismatches
from app.services.period_detector import get_column_for_period, period_duration_months
from app.services.reconciliation import canonical_label, reconcile, review_status_for
from app.services.validation import validate_period

ITEM_MODEL = {
    "pnl": PnLItem,
    "balance_sheet": BalanceSheetItem,
    "cash_flow": CashFlowItem,
}
BALANCE_SUBTOTALS = {
    "Total Fixed Assets": ("Goodwill", "Development Costs", "Tangible Assets"),
    "Total Current Assets": ("Debtors", "Cash and cash equivalents"),
}
SUBTOTAL_TOLERANCE = 1.0


class PeriodReplacementRequired(ValueError):
    def __init__(self, labels: List[str]):
        self.labels = labels
        super().__init__(f"Replacing existing periods requires explicit confirmation: {', '.join(labels)}")


def require_replacement_confirmation(
    labels: List[str], replace_existing: bool
) -> None:
    if labels and not replace_existing:
        raise PeriodReplacementRequired(labels)


def read_pages(pdf_path: str) -> List[Tuple[int, str]]:
    if not pdf_path:
        raise ValueError("Document has no stored file")
    pages: List[Tuple[int, str]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for idx, page in enumerate(pdf.pages, start=1):
            pages.append((idx, page.extract_text() or ""))
    return pages


def build_proposal(
    result: Dict[str, Any], llm_trace: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    """The review payload shown to a human (and stored on the document)."""
    proposal: Dict[str, Any] = {statement: [i.to_dict() for i in result[statement]] for statement in STATEMENTS}
    proposal["summary"] = result["summary"]
    if llm_trace is not None:
        proposal["llm_trace"] = llm_trace
    return proposal


def _store_period_proposal(
    raw_json: str | None, label: str, proposal: Dict[str, Any]
) -> str:
    """Keep each extracted period's review proposal instead of overwriting it."""
    reviews: Dict[str, Any] = {}
    call_history: List[Dict[str, Any]] = []
    if raw_json:
        stored = json.loads(raw_json)
        if isinstance(stored, dict):
            if isinstance(stored.get("periods"), dict):
                reviews = stored["periods"]
            if isinstance(stored.get("llm_calls"), list):
                call_history = stored["llm_calls"]
    reviews[label] = proposal
    llm_trace = proposal.get("llm_trace")
    if isinstance(llm_trace, dict):
        call_history.append(llm_trace)
    return json.dumps({"periods": reviews, "llm_calls": call_history})


def _chosen(result: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """label -> value, using each item's chosen value (deterministic preferred)."""
    chosen: Dict[str, Dict[str, float]] = {}
    for statement in STATEMENTS:
        chosen[statement] = {
            item.line_item: float(item.value)
            for item in result[statement]
            if item.value is not None and item.status != "llm_only"
        }
    return chosen


def _expected_llm_coverage(
    result: Dict[str, Any],
) -> Tuple[int, Dict[str, List[str]]]:
    grounded_count = sum(
        item.source == "both"
        for statement in STATEMENTS
        for item in result[statement]
    )
    missing_expected = {
        statement: [
            item.line_item
            for item in result[statement]
            if item.status == "regex_only"
            and not (
                statement == "balance_sheet"
                and item.line_item in BALANCE_SUBTOTALS
            )
        ]
        for statement in STATEMENTS
    }
    return grounded_count, missing_expected


def resolve_unlabelled_subtotals(
    tables: Dict[str, Any],
    tolerance: float = SUBTOTAL_TOLERANCE,
) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """Keep printed unlabeled totals when they agree with the components."""
    balance_sheet = tables.setdefault("balance_sheet", {})
    origins: Dict[str, str] = {}
    warnings: List[Dict[str, Any]] = []
    for total_label, components in BALANCE_SUBTOTALS.items():
        component_values = [
            parse_value(balance_sheet[label])
            for label in components
            if label in balance_sheet
        ]
        if len(component_values) != len(components) or any(
            value is None for value in component_values
        ):
            continue
        derived_total = sum(float(value) for value in component_values if value is not None)
        printed_value = parse_value(balance_sheet.get(total_label))
        if printed_value is not None and abs(float(printed_value) - derived_total) <= tolerance:
            origins[total_label] = "reported (unlabeled)"
            continue

        balance_sheet[total_label] = Decimal(str(round(derived_total, 2)))
        origins[total_label] = "derived"
        if printed_value is not None:
            warnings.append(
                {
                    "line_item": total_label,
                    "printed_value": float(printed_value),
                    "component_sum": derived_total,
                    "message": (
                        f"{total_label} printed as {float(printed_value):,.0f}, but its "
                        f"components sum to {derived_total:,.0f}; the derived sum is retained."
                    ),
                }
            )
    return origins, warnings


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
    return period_duration_months(start, end)


def _metadata(doc: Document, chosen: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "document_title": doc.filename,
        "currency": "EUR",
        "period_months": (
            period_duration_months(doc.period_start, doc.period_end)
            if doc.period_start and doc.period_end
            else None
        ),
        "audited": False,
        "method": "hybrid (regex + llm, reconciled)",
        "review": result["summary"],
    }


def _kpi_rows(
    period_id: int,
    values: Dict[str, float | None],
    cash_flow: Dict[str, float],
    source_notes: Dict[str, str] | None = None,
) -> List[KPI]:
    source_notes = source_notes or {}
    rows = []
    for metric, value in values.items():
        metadata = kpi_metadata(metric, value, cash_flow, source_notes.get(metric))
        rows.append(
            KPI(
                period_id=period_id,
                metric=metric,
                value=Decimal(str(round(float(value), 6))) if value is not None else None,
                formula=metadata["formula"],
                kind=metadata["kind"],
                note=metadata["note"],
            )
        )
    return rows


async def _persist(
    db: AsyncSession,
    doc: Document,
    result: Dict[str, Any],
    proposal: Dict[str, Any],
    page_count: int,
    label: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    replace_existing: bool = False,
    item_origins: Optional[Dict[str, str]] = None,
) -> None:
    use_label = label or doc.period_label
    use_start = start_date or doc.period_start
    use_end = end_date or doc.period_end

    existing = (await db.execute(select(Period).where(Period.label == use_label))).scalars().all()
    require_replacement_confirmation(
        [period.label for period in existing],
        replace_existing,
    )
    for period in existing:
        await db.delete(period)
    await db.flush()

    chosen = _chosen(result)
    kpis = compute_kpis(
        chosen["pnl"],
        chosen["balance_sheet"],
        chosen["cash_flow"],
        period_duration_months(use_start, use_end),
    )
    issues = [
        issue.as_dict()
        for issue in validate_period(chosen["pnl"], chosen["balance_sheet"], chosen["cash_flow"])
    ]

    period = Period(
        label=use_label,
        start_date=use_start,
        end_date=use_end,
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
                    origin=(item_origins or {}).get(
                        item.line_item,
                        "derived" if item.line_item == "EBITDA" else "reported",
                    ),
                    review_status=review_status_for(item.status),
                    confidence=item.confidence,
                    original_value=original,
                )
            )

    db.add_all(_kpi_rows(period.id, kpis, chosen["cash_flow"]))
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
    doc.raw_json = _store_period_proposal(doc.raw_json, use_label, proposal)


async def recompute_derived(db: AsyncSession, period_id: int) -> None:
    """Recompute KPIs and validations after a human confirms/corrects an item."""
    period = await db.get(Period, period_id)
    if period is None:
        raise ValueError(f"Unknown period: {period_id}")

    raw: Dict[str, Dict[str, float]] = {}
    for statement, model in ITEM_MODEL.items():
        rows = (
            await db.execute(select(model).where(model.period_id == period_id))
        ).scalars().all()
        raw[statement] = {row.line_item: float(row.value_eur) for row in rows}

    kpis = compute_kpis(
        raw["pnl"],
        raw["balance_sheet"],
        raw["cash_flow"],
        period_duration_months(period.start_date, period.end_date),
    )
    existing_kpis = (
        await db.execute(select(KPI).where(KPI.period_id == period_id))
    ).scalars().all()
    source_notes: Dict[str, str] = {}
    for row in existing_kpis:
        spec = CATALOG_BY_KEY.get(row.metric)
        if (
            row.value is not None
            and spec is not None
            and spec.get("kind") == "reported"
            and kpis.get(row.metric) is None
        ):
            kpis[row.metric] = float(row.value)
            if row.note:
                source_notes[row.metric] = row.note
    issues = [
        issue.as_dict()
        for issue in validate_period(raw["pnl"], raw["balance_sheet"], raw["cash_flow"])
    ]

    await db.execute(delete(KPI).where(KPI.period_id == period_id))
    await db.execute(delete(Validation).where(Validation.period_id == period_id))
    db.add_all(_kpi_rows(period_id, kpis, raw["cash_flow"], source_notes))
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


async def ingest_document(
    document_id: int,
    db: AsyncSession,
    column: Optional[int] = None,
    period_label: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    replace_existing: bool = False,
) -> Dict[str, Any]:
    """Process an uploaded document end to end. Returns the review proposal.

    If column/period_label/start_date/end_date are provided, uses those values
    (multi-period upload support). Otherwise falls back to the document's stored
    values.
    """
    doc = await db.get(Document, document_id)
    if doc is None:
        raise ValueError(f"Unknown document: {document_id}")

    use_label = period_label or doc.period_label
    if use_label and not replace_existing:
        existing_label = await db.scalar(
            select(Period.label).where(Period.label == use_label)
        )
        require_replacement_confirmation(
            [existing_label] if existing_label else [],
            replace_existing,
        )

    doc.status = lifecycle.advance(doc.status, lifecycle.PROCESSING)
    doc.error = None
    await db.commit()

    try:
        pages = read_pages(doc.stored_path)

        # Determine which period/column to process
        use_start = start_date or doc.period_start
        use_end = end_date or doc.period_end
        use_column = column if column is not None else get_column_for_period(use_start, use_end, pages)
        # Only 2 table columns are supported by the regex parser (0=current, 1=comparative)
        if use_column is None or use_column > 1:
            use_column = 0

        tables = parse_tables(pages, column=use_column)
        item_origins, subtotal_warnings = resolve_unlabelled_subtotals(tables)
        expected_count = sum(
            1
            for statement in STATEMENTS
            for label in (tables.get(statement, {}) or {})
            if not (
                statement == "balance_sheet"
                and label in BALANCE_SUBTOTALS
            )
        )
        llm_pages = select_statement_pages(pages, tables)
        llm_result = await extract_structured(
            llm_pages,
            use_label or "",
            column=use_column,
            expected_count=expected_count,
            document_id=doc.id,
        )
        result = reconcile(tables, llm_result.lines, pages=pages)
        llm_trace = llm_result.trace
        llm_trace["statement_pages"] = [page for page, _text in llm_pages]
        normalized_values = {
            (statement, item.line_item): item.llm_value
            for statement in STATEMENTS
            for item in result[statement]
            if item.llm_value is not None
        }
        for line in llm_trace.get("returned_lines", []):
            normalized = normalized_values.get(
                (line["statement"], canonical_label(line["statement"], line["line_item"]))
            )
            if normalized is not None:
                line["normalized_value"] = normalized
                line["sign_normalized"] = normalized != line["value"]
        llm_trace["expected_count"] = expected_count
        grounded_count, missing_expected = _expected_llm_coverage(result)
        llm_trace["grounded_count"] = grounded_count
        llm_trace["missing_expected"] = missing_expected
        missing_count = sum(len(labels) for labels in llm_trace["missing_expected"].values())
        if llm_trace.get("status") == "truncated" and missing_count:
            llm_trace["warning"] = (
                f"{llm_trace.get('warning', '')} "
                f"{missing_count} partidas esperadas siguen sin cubrir."
            ).strip()
        elif llm_trace.get("status") != "regex_only" and missing_count:
            llm_trace["status"] = "partial"
            llm_trace["warning"] = (
                f"Cobertura de partidas conocidas por regex: "
                f"{llm_trace['grounded_count']} de {expected_count}; "
                f"faltan {missing_count} y quedan para revisión."
            )
        proposal = build_proposal(result, llm_result.trace)
        statement_pages = {
            page
            for pages_by_item in (tables.get("provenance") or {}).values()
            for page in pages_by_item.values()
            if page
        }
        proposal["note_warnings"] = find_note_mismatches(
            pages,
            statement_pages,
            tables,
            period_end=use_end,
        )
        proposal["subtotal_warnings"] = subtotal_warnings
        await _persist(
            db,
            doc,
            result,
            proposal,
            len(pages),
            label=use_label,
            start_date=use_start,
            end_date=use_end,
            replace_existing=replace_existing,
            item_origins=item_origins,
        )
        doc.status = lifecycle.advance(doc.status, lifecycle.EXTRACTED)
        await db.commit()
        return proposal
    except Exception as exc:  # record and surface the failure
        doc.status = lifecycle.ERROR
        doc.error = str(exc)
        await db.commit()
        raise
