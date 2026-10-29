from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import List
from decimal import Decimal
import json
import calendar

from app.core.database import get_db
from app.core.security import get_current_user, require_admin
from app.models.financial import (
    Period,
    PnLItem,
    BalanceSheetItem,
    CashFlowItem,
    KPI,
    Document,
    User,
    Validation,
    AuditLog,
)
from app.schemas.financial import (
    PeriodResponse,
    PeriodDetailResponse,
    PnLItemResponse,
    BalanceSheetItemResponse,
    CashFlowItemResponse,
    LineItemResponse,
    ItemUpdate,
    KPIResponse,
    FinancialSummary,
    ValidationResponse,
)
from app.services import lifecycle
from app.services.deterministic import compute_kpis
from app.services.ingest import recompute_derived
from app.services.metrics import CATALOG_BY_KEY, kpi_metadata
from app.services.period_detector import period_duration_months

router = APIRouter(prefix="/api", tags=["financial"])

ITEM_MODELS = {"pnl": PnLItem, "balance_sheet": BalanceSheetItem, "cash_flow": CashFlowItem}


def _period_kpis(period: Period) -> dict[str, float | None]:
    pnl = {item.line_item: float(item.value_eur) for item in period.pnl_items}
    balance_sheet = {item.line_item: float(item.value_eur) for item in period.bs_items}
    cash_flow = {item.line_item: float(item.value_eur) for item in period.cf_items}
    values = compute_kpis(
        pnl,
        balance_sheet,
        cash_flow,
        period_duration_months(period.start_date, period.end_date),
    )
    for row in period.kpis:
        spec = CATALOG_BY_KEY.get(row.metric)
        if (
            row.value is not None
            and spec is not None
            and spec.get("kind") == "reported"
            and values.get(row.metric) is None
        ):
            values[row.metric] = float(row.value)
    return values


def _period_kpi_responses(period: Period) -> list[KPIResponse]:
    cash_flow = {item.line_item: float(item.value_eur) for item in period.cf_items}
    sources = {
        row.metric: row.note
        for row in period.kpis
        if row.note and CATALOG_BY_KEY.get(row.metric, {}).get("kind") == "reported"
    }
    values = _period_kpis(period)
    return [
        KPIResponse(
            metric=metric,
            value=value,
            **kpi_metadata(metric, value, cash_flow, sources.get(metric)),
        )
        for metric, value in values.items()
    ]


@router.get("/periods", response_model=List[PeriodResponse])
async def list_periods(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(Period).order_by(Period.end_date.desc()))
    return result.scalars().all()


@router.get("/periods/{period_label}", response_model=PeriodDetailResponse)
async def get_period_detail(
    period_label: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Period)
        .options(
            selectinload(Period.pnl_items),
            selectinload(Period.bs_items),
            selectinload(Period.cf_items),
            selectinload(Period.kpis),
        )
        .where(Period.label == period_label)
    )
    period = result.scalar_one_or_none()
    if not period:
        raise HTTPException(status_code=404, detail="Period not found")
    response = PeriodDetailResponse.model_validate(period)
    response.kpis = _period_kpi_responses(period)
    return response


@router.get("/summary", response_model=List[FinancialSummary])
async def get_financial_summary(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get summary metrics for all periods for dashboard overview"""
    result = await db.execute(
        select(Period)
        .options(
            selectinload(Period.pnl_items),
            selectinload(Period.bs_items),
            selectinload(Period.cf_items),
            selectinload(Period.kpis),
        )
        .order_by(Period.end_date.desc())
    )
    periods = result.scalars().all()

    summaries = []
    for p in periods:
        pnl_dict = {item.line_item: float(item.value_eur) for item in p.pnl_items}
        bs_dict = {item.line_item: float(item.value_eur) for item in p.bs_items}
        kpi_dict = _period_kpis(p)

        revenue = pnl_dict.get("Revenue")
        if revenue is None:
            revenue = pnl_dict.get("Turnover")
        gross_profit = pnl_dict.get("Gross Profit")
        gross_margin = (
            kpi_dict.get("gross_margin")
            if "gross_margin" in kpi_dict
            else (
                gross_profit / revenue * 100
                if revenue not in (None, 0) and gross_profit is not None
                else None
            )
        )
        operating_loss = pnl_dict.get("Group operating loss")
        if operating_loss is None:
            operating_loss = pnl_dict.get("Operating loss")
        operating_margin = kpi_dict.get("operating_margin")
        if (
            operating_margin is None
            and revenue not in (None, 0)
            and operating_loss is not None
        ):
            operating_margin = operating_loss / revenue * 100

        cash = kpi_dict.get("cash")
        if cash is None:
            cash = bs_dict.get("Cash and cash equivalents")
        if cash is None:
            cash = bs_dict.get("Cash and bank balances")
        short_debt = bs_dict.get("Creditors < 1yr")
        long_debt = bs_dict.get("Creditors > 1yr")
        debt = (
            abs(short_debt) + abs(long_debt)
            if short_debt is not None and long_debt is not None
            else None
        )

        runway = kpi_dict.get("runway_months")

        summaries.append(FinancialSummary(
            period=PeriodResponse.model_validate(p),
            duration_months=period_duration_months(p.start_date, p.end_date),
            revenue=revenue,
            revenue_origin="reported" if revenue is not None else "n/d",
            gross_profit=gross_profit,
            gross_profit_origin="reported" if gross_profit is not None else "n/d",
            gross_margin=gross_margin,
            gross_margin_origin="derived" if gross_margin is not None else "n/d",
            operating_loss=operating_loss,
            operating_loss_origin="reported" if operating_loss is not None else "n/d",
            operating_margin=operating_margin,
            operating_margin_origin="derived" if operating_margin is not None else "n/d",
            cash=cash,
            cash_origin="reported" if cash is not None else "n/d",
            debt=debt,
            debt_origin="derived" if debt is not None else "n/d",
            runway_months=runway,
            runway_origin="derived" if runway is not None else "n/d",
        ))

    for summary in summaries:
        start = summary.period.start_date
        prior_year = start.year - 1
        prior_start = start.replace(
            year=prior_year,
            day=min(start.day, calendar.monthrange(prior_year, start.month)[1]),
        )
        prior = next(
            (
                candidate
                for candidate in summaries
                if candidate.period.start_date == prior_start
                and candidate.duration_months == summary.duration_months
            ),
            None,
        )
        if prior is None:
            continue
        summary.revenue_yoy_period_label = prior.period.label
        if (
            summary.revenue is not None
            and prior.revenue not in (None, 0)
        ):
            summary.revenue_yoy_change_pct = (
                (summary.revenue - prior.revenue) / abs(prior.revenue) * 100
            )

    return summaries


@router.get("/kpis/{period_label}", response_model=List[KPIResponse])
async def get_kpis(
    period_label: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Period)
        .options(
            selectinload(Period.pnl_items),
            selectinload(Period.bs_items),
            selectinload(Period.cf_items),
            selectinload(Period.kpis),
        )
        .where(Period.label == period_label)
    )
    period = result.scalar_one_or_none()
    if period is None:
        raise HTTPException(status_code=404, detail=f"Unknown period: {period_label}")
    return _period_kpi_responses(period)


@router.get("/validations/{period_label}", response_model=List[ValidationResponse])
async def get_validations(
    period_label: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get the automated validation issues recorded for a period."""
    period = (
        await db.execute(select(Period).where(Period.label == period_label))
    ).scalars().first()
    if period is None:
        raise HTTPException(status_code=404, detail=f"Unknown period: {period_label}")
    rows = (
        (await db.execute(select(Validation).where(Validation.period_id == period.id)))
        .scalars()
        .all()
    )
    return list(rows)


def _item_snapshot(item) -> dict:
    return {
        "line_item": item.line_item,
        "value_eur": float(item.value_eur),
        "review_status": item.review_status,
        "original_value": float(item.original_value) if item.original_value is not None else None,
    }


@router.patch("/periods/{period_label}/items", response_model=LineItemResponse)
async def update_item(
    period_label: str,
    payload: ItemUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Confirm/correct a figure, or add a missing line with a manual value."""
    model = ITEM_MODELS.get(payload.statement)
    if model is None:
        raise HTTPException(status_code=400, detail=f"Unknown statement: {payload.statement}")

    period = (
        await db.execute(select(Period).where(Period.label == period_label))
    ).scalars().first()
    if period is None:
        raise HTTPException(status_code=404, detail=f"Unknown period: {period_label}")

    item = (
        await db.execute(
            select(model).where(
                model.period_id == period.id, model.line_item == payload.line_item
            )
        )
    ).scalars().first()
    if item is None:
        if payload.action == "correct" and payload.value is None:
            raise HTTPException(status_code=400, detail="A value is required to correct an item")
        value = Decimal(str(payload.value)) if payload.value is not None else Decimal("0")
        item = model(
            period_id=period.id,
            line_item=payload.line_item,
            value_eur=value,
            review_status="confirmed" if payload.action == "confirm" else "corrected",
            confidence=1.0,
        )
        db.add(item)
        before = None
    else:
        before = _item_snapshot(item)
        if payload.action == "confirm":
            item.review_status = "confirmed"
        else:
            if payload.value is None:
                raise HTTPException(status_code=400, detail="A value is required to correct an item")
            item.value_eur = Decimal(str(payload.value))
            item.review_status = "corrected"
        item.confidence = 1.0
    await db.flush()

    db.add(
        AuditLog(
            user_email=user.email,
            action=payload.action,
            entity=payload.statement,
            entity_id=None,
            before_json=json.dumps(before),
            after_json=json.dumps(_item_snapshot(item)),
        )
    )
    await recompute_derived(db, period.id)
    await db.commit()
    await db.refresh(item)
    return item


@router.post("/periods/{period_label}/publish", response_model=PeriodResponse)
async def publish_period(
    period_label: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Publish a reviewed period by moving its source document to 'published'."""
    period = (
        await db.execute(select(Period).where(Period.label == period_label))
    ).scalars().first()
    if period is None:
        raise HTTPException(status_code=404, detail=f"Unknown period: {period_label}")

    doc = await db.get(Document, period.source_doc_id) if period.source_doc_id else None
    if doc is not None and doc.status != lifecycle.PUBLISHED:
        doc.status = lifecycle.advance(doc.status, lifecycle.PUBLISHED)
    db.add(
        AuditLog(
            user_email=user.email,
            action="publish",
            entity="period",
            entity_id=period.id,
        )
    )
    await db.commit()
    return period
