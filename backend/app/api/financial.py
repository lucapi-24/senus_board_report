from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import List, Optional
from decimal import Decimal
import json

from app.core.database import get_db
from app.models.financial import (
    Period,
    PnLItem,
    BalanceSheetItem,
    CashFlowItem,
    KPI,
    Document,
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
    AIInsight,
    ValidationResponse,
)
from app.services import lifecycle
from app.services.ingest import recompute_derived

router = APIRouter(prefix="/api", tags=["financial"])

ITEM_MODELS = {"pnl": PnLItem, "balance_sheet": BalanceSheetItem, "cash_flow": CashFlowItem}


@router.get("/periods", response_model=List[PeriodResponse])
async def list_periods(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Period).order_by(Period.end_date.desc()))
    return result.scalars().all()


@router.get("/periods/{period_label}", response_model=PeriodDetailResponse)
async def get_period_detail(period_label: str, db: AsyncSession = Depends(get_db)):
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
    return period


@router.get("/summary", response_model=List[FinancialSummary])
async def get_financial_summary(db: AsyncSession = Depends(get_db)):
    """Get summary metrics for all periods for dashboard overview"""
    result = await db.execute(
        select(Period)
        .options(
            selectinload(Period.pnl_items),
            selectinload(Period.bs_items),
            selectinload(Period.kpis),
        )
        .order_by(Period.end_date.desc())
    )
    periods = result.scalars().all()

    summaries = []
    for p in periods:
        pnl_dict = {item.line_item: item.value_eur for item in p.pnl_items}
        bs_dict = {item.line_item: item.value_eur for item in p.bs_items}
        kpi_dict = {k.metric: float(k.value) for k in p.kpis}

        revenue = pnl_dict.get("Revenue") or pnl_dict.get("Turnover")
        gross_profit = pnl_dict.get("Gross Profit")
        gross_margin = float(gross_profit / revenue * 100) if revenue and gross_profit and revenue != 0 else None
        operating_loss = pnl_dict.get("Group operating loss") or pnl_dict.get("Operating loss")

        cash = bs_dict.get("Cash and cash equivalents") or bs_dict.get("Cash and bank balances")
        debt = abs(bs_dict.get("Creditors < 1yr") or 0) + abs(bs_dict.get("Creditors > 1yr") or 0)

        runway = kpi_dict.get("runway_months")

        summaries.append(FinancialSummary(
            period=PeriodResponse.model_validate(p),
            revenue=revenue,
            gross_profit=gross_profit,
            gross_margin=gross_margin,
            operating_loss=operating_loss,
            cash=cash,
            debt=debt,
            runway_months=runway,
        ))

    return summaries


@router.get("/kpis/{period_label}", response_model=List[KPIResponse])
async def get_kpis(period_label: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(KPI)
        .join(Period)
        .where(Period.label == period_label)
    )
    return result.scalars().all()


@router.get("/insights/{period_label}", response_model=List[AIInsight])
async def get_ai_insights(period_label: str, db: AsyncSession = Depends(get_db)):
    """Get AI-generated insights for a period"""
    from app.services.ai_insights import generate_insights
    return await generate_insights(period_label, db)


@router.get("/validations/{period_label}", response_model=List[ValidationResponse])
async def get_validations(period_label: str, db: AsyncSession = Depends(get_db)):
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
    x_user_email: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Confirm a flagged figure as-is, or correct it to a human-entered value."""
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
        raise HTTPException(status_code=404, detail="Item not found in this period")

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
            user_email=x_user_email or "local@senus",
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
async def publish_period(period_label: str, db: AsyncSession = Depends(get_db)):
    """Publish a reviewed period by moving its source document to 'published'."""
    period = (
        await db.execute(select(Period).where(Period.label == period_label))
    ).scalars().first()
    if period is None:
        raise HTTPException(status_code=404, detail=f"Unknown period: {period_label}")

    doc = await db.get(Document, period.source_doc_id) if period.source_doc_id else None
    if doc is not None and doc.status != lifecycle.PUBLISHED:
        doc.status = lifecycle.advance(doc.status, lifecycle.PUBLISHED)
        await db.commit()
    return period
