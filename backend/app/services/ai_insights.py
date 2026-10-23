from typing import List, Optional
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from fastapi import HTTPException
from app.models.financial import Period, PnLItem, BalanceSheetItem, CashFlowItem, KPI
from app.schemas.financial import AIInsight, PeriodCompareRequest, PeriodCompareResponse
from app.core.config import settings
from app.services.metrics import require_same_period_duration
from app.services.period_detector import period_duration_months
from openai import AsyncOpenAI


client = AsyncOpenAI(
    base_url=settings.GROQ_BASE_URL,
    api_key=settings.GROQ_API_KEY,
) if settings.GROQ_API_KEY else None


def _format_money(value: Optional[float]) -> str:
    return f"€{value:,.0f}" if value is not None else "n/d"


def _format_kpi(value: Optional[float], digits: int, suffix: str) -> str:
    return f"{value:.{digits}f}{suffix}" if value is not None else "n/d"


async def generate_insights(period_label: str, db: AsyncSession) -> List[AIInsight]:
    """Generate AI-powered financial insights for a period"""
    if not client:
        return [AIInsight(
            section="Configuration",
            insight="Groq API key not configured. Set GROQ_API_KEY environment variable.",
            confidence=0.0
        )]

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
        return [AIInsight(section="Error", insight=f"Period {period_label} not found", confidence=0.0)]

    pnl = {item.line_item: float(item.value_eur) for item in period.pnl_items}
    bs = {item.line_item: float(item.value_eur) for item in period.bs_items}
    cf = {item.line_item: float(item.value_eur) for item in period.cf_items}
    kpis = {k.metric: float(k.value) for k in period.kpis if k.value is not None}

    cash = kpis.get("cash")
    if cash is None:
        cash = bs.get("Cash and cash equivalents")
    if cash is None:
        cash = bs.get("Cash and bank balances")
    short_creditors = bs.get("Creditors < 1yr")
    long_creditors = bs.get("Creditors > 1yr")
    total_creditors = (
        abs(short_creditors) + abs(long_creditors)
        if short_creditors is not None and long_creditors is not None
        else None
    )
    contingent = bs.get("Contingent consideration")
    equity = bs.get("Equity")
    if equity is None:
        equity = bs.get("Net Assets")
    goodwill = bs.get("Goodwill")
    development_costs = bs.get("Development Costs")
    intangibles = (
        goodwill + development_costs
        if goodwill is not None and development_costs is not None
        else None
    )
    dscr_kpi = next((kpi for kpi in period.kpis if kpi.metric == "dscr"), None)
    dscr_note = (
        f"n/d ({dscr_kpi.note})"
        if dscr_kpi is not None and dscr_kpi.value is None and dscr_kpi.note
        else "n/d"
    )
    period_months = period_duration_months(period.start_date, period.end_date)

    prompt = f"""You are a CFO-grade financial analyst. Analyze Senus PLC's {period_label} financials and provide 3-5 concise, actionable insights for the Board.

FINANCIAL DATA ({period_label}):
P&L:
- Revenue: {_format_money(pnl.get('Revenue', pnl.get('Turnover')))}
- Cost of Sales: {_format_money(pnl.get('Cost of sales'))}
- Gross Profit: {_format_money(pnl.get('Gross Profit'))}
- Admin Expenses: {_format_money(pnl.get('Administrative expenses'))}
- Operating Loss: {_format_money(pnl.get('Group operating loss', pnl.get('Operating loss')))}
- Loss Before Taxation: {_format_money(pnl.get('Loss before taxation'))}
- Loss for the Period: {_format_money(pnl.get('Loss for the period'))}

Balance Sheet:
- Cash: {_format_money(cash)}
- Total Creditors (short + long term): {_format_money(total_creditors)}
- Contingent Consideration: {_format_money(abs(contingent) if contingent is not None else None)}
- Net Current Assets: {_format_money(bs.get('Net Current Assets'))}
- Equity / Net Assets: {_format_money(equity)}
- Goodwill + Development Costs: {_format_money(intangibles)}

KPIs:
- Gross Margin: {_format_kpi(kpis.get('gross_margin'), 1, '%')}
- EBITDA Margin: {_format_kpi(kpis.get('ebitda_margin'), 1, '%')}
- Net Margin: {_format_kpi(kpis.get('net_margin'), 1, '%')}
- Cash Runway: {_format_kpi(kpis.get('runway_months'), 1, ' months')}
- Current Ratio: {_format_kpi(kpis.get('current_ratio'), 2, 'x')} (ex-contingent {_format_kpi(kpis.get('current_ratio_ex_contingent'), 2, 'x')})
- Equity Ratio: {_format_kpi(kpis.get('equity_ratio'), 1, '%')}
- Intangibles / Total Assets: {_format_kpi(kpis.get('intangibles_ratio'), 1, '%')}
- ROE: {_format_kpi(kpis.get('roe'), 1, '%')}
- ROCE: {_format_kpi(kpis.get('roce'), 1, '%')}
- DSCR: {_format_kpi(kpis.get('dscr'), 2, 'x') if kpis.get('dscr') is not None else dscr_note}

CONTEXT: You are analyzing financials for period {period_label} ({period_months} months).
Do not reference events from other periods unless they are explicitly documented for {period_label}.
Do not state long-term targets (e.g. CAGR) unless explicitly stated in the data for {period_label}.

STRICT RULES:
- Use ONLY the figures and facts in FINANCIAL DATA and CONTEXT above. Never invent events, causes, costs, contracts or forecasts.
- Every numeric claim must match a figure above; do not restate a figure as text without the number.
- Do not claim new contracts, partnerships or funding unless explicitly named above.
- If something material is missing (e.g. no prior-year comparator), say so instead of estimating.
- This is a {period_months}-month period ({period_label}); do not conflate with full-year or other periods.
- Do not contradict computed KPIs (e.g. if DSCR is marked "n/d", never state a DSCR value or imply positive debt service coverage).
- Prefer descriptive statements over speculative recommendations; do not invent causes/costs.

Return JSON array of insights with: section, insight, confidence (0-1). Focus on: revenue quality, cost discipline, cash runway, strategic milestones, risks.

Only use the percentages and ratios listed above. Do not introduce any other calculated ratio or percentage, and do not invent figures."""

    import asyncio

    try:
        response = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await client.chat.completions.create(
                    model=settings.GROQ_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=800,
                    response_format={"type": "json_object"},
                )
                break
            except Exception as exc:  # transient rate limits / network blips
                last_error = exc
                await asyncio.sleep(1.5 * (attempt + 1))
        if response is None:
            raise last_error if last_error else RuntimeError("No response from model")

        import json
        data = json.loads(response.choices[0].message.content)

        if isinstance(data, dict):
            wrapper = next(
                (data[k] for k in ("insights", "sections", "analysis", "items")
                 if k in data and isinstance(data[k], (list, dict))),
                None,
            )
            if wrapper is not None:
                items = wrapper
            elif any(k in data for k in ("section", "insight", "confidence", "title", "analysis")):
                items = [data]
            else:
                items = list(data.items())
        else:
            items = data
        if isinstance(items, dict):
            items = list(items.items())

        insights: List[AIInsight] = []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    insights.append(AIInsight(
                        section=str(item.get("section", item.get("title", "Insight"))),
                        insight=str(item.get("insight", item.get("analysis", item.get("detail", "")))),
                        confidence=float(item.get("confidence", 0.8) or 0.8),
                    ))
                elif isinstance(item, (list, tuple)) and len(item) == 2:
                    section, text = item
                    if isinstance(text, dict):
                        insights.append(AIInsight(
                            section=str(section),
                            insight=str(text.get("insight", text.get("analysis", ""))),
                            confidence=float(text.get("confidence", 0.8) or 0.8),
                        ))
                    else:
                        insights.append(AIInsight(section=str(section), insight=str(text), confidence=0.8))
                elif isinstance(item, str):
                    insights.append(AIInsight(section="Insight", insight=item, confidence=0.8))

        return insights or [AIInsight(section="AI", insight="No insights returned.", confidence=0.0)]
    except Exception as e:
        return [AIInsight(section="AI Error", insight=f"Failed to generate insights: {str(e)}", confidence=0.0)]


async def compare_periods(request: PeriodCompareRequest, db: AsyncSession) -> PeriodCompareResponse:
    """Compare two periods using AI analysis."""
    result_a = await db.execute(
        select(Period)
        .options(
            selectinload(Period.pnl_items),
            selectinload(Period.bs_items),
            selectinload(Period.cf_items),
            selectinload(Period.kpis),
        )
        .where(Period.label == request.period_label_a)
    )
    period_a = result_a.scalar_one_or_none()
    if not period_a:
        return PeriodCompareResponse(
            label_a=request.period_label_a,
            label_b=request.period_label_b,
            insight=f"Period '{request.period_label_a}' not found.",
            confidence=0.0,
        )

    result_b = await db.execute(
        select(Period)
        .options(
            selectinload(Period.pnl_items),
            selectinload(Period.bs_items),
            selectinload(Period.cf_items),
            selectinload(Period.kpis),
        )
        .where(Period.label == request.period_label_b)
    )
    period_b = result_b.scalar_one_or_none()
    if not period_b:
        return PeriodCompareResponse(
            label_a=request.period_label_a,
            label_b=request.period_label_b,
            insight=f"Period '{request.period_label_b}' not found.",
            confidence=0.0,
        )

    try:
        require_same_period_duration(
            period_a.start_date,
            period_a.end_date,
            period_b.start_date,
            period_b.end_date,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    if not client:
        return PeriodCompareResponse(
            label_a=request.period_label_a,
            label_b=request.period_label_b,
            insight="Groq API key not configured. Set GROQ_API_KEY environment variable.",
            confidence=0.0,
        )

    p_a = {item.line_item: float(item.value_eur) for item in period_a.pnl_items}
    b_a = {item.line_item: float(item.value_eur) for item in period_a.bs_items}

    p_b = {item.line_item: float(item.value_eur) for item in period_b.pnl_items}
    b_b = {item.line_item: float(item.value_eur) for item in period_b.bs_items}

    creditors_a = (
        abs(b_a["Creditors < 1yr"]) + abs(b_a["Creditors > 1yr"])
        if "Creditors < 1yr" in b_a and "Creditors > 1yr" in b_a
        else None
    )
    creditors_b = (
        abs(b_b["Creditors < 1yr"]) + abs(b_b["Creditors > 1yr"])
        if "Creditors < 1yr" in b_b and "Creditors > 1yr" in b_b
        else None
    )

    prompt = f"""You are a CFO-grade financial analyst comparing two periods for Senus PLC.

PERIOD A: {request.period_label_a}
P&L:
- Revenue: {_format_money(p_a.get('Revenue', p_a.get('Turnover')))}
- Cost of Sales: {_format_money(p_a.get('Cost of sales'))}
- Gross Profit: {_format_money(p_a.get('Gross Profit'))}
- Admin Expenses: {_format_money(p_a.get('Administrative expenses'))}
- Operating Loss: {_format_money(p_a.get('Group operating loss', p_a.get('Operating loss')))}
- Loss for the Period: {_format_money(p_a.get('Loss for the period'))}

Balance Sheet:
- Cash: {_format_money(b_a.get('Cash and cash equivalents', b_a.get('Cash and bank balances')))}
- Total Creditors: {_format_money(creditors_a)}
- Equity / Net Assets: {_format_money(b_a.get('Equity', b_a.get('Net Assets')))}

PERIOD B: {request.period_label_b}
P&L:
- Revenue: {_format_money(p_b.get('Revenue', p_b.get('Turnover')))}
- Cost of Sales: {_format_money(p_b.get('Cost of sales'))}
- Gross Profit: {_format_money(p_b.get('Gross Profit'))}
- Admin Expenses: {_format_money(p_b.get('Administrative expenses'))}
- Operating Loss: {_format_money(p_b.get('Group operating loss', p_b.get('Operating loss')))}
- Loss for the Period: {_format_money(p_b.get('Loss for the period'))}

Balance Sheet:
- Cash: {_format_money(b_b.get('Cash and cash equivalents', b_b.get('Cash and bank balances')))}
- Total Creditors: {_format_money(creditors_b)}
- Equity / Net Assets: {_format_money(b_b.get('Equity', b_b.get('Net Assets')))}

Write a concise executive summary (2-4 sentences) comparing the two periods. Focus on:
- Revenue trend and margin change
- Cost changes (administrative expenses)
- Operating result trend
- Cash position change
- Key risks or positives for the board

Use ONLY the figures above. Return ONLY JSON:
{{"insight": "your comparison text here", "confidence": 0.95}}"""

    import asyncio

    try:
        response = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await client.chat.completions.create(
                    model=settings.GROQ_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=1000,
                    response_format={"type": "json_object"},
                )
                break
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(1.5 * (attempt + 1))
        if response is None:
            raise last_error if last_error else RuntimeError("No response from model")

        import json
        data = json.loads(response.choices[0].message.content)
        insight = data.get("insight", "No insight generated.")
        confidence = float(data.get("confidence", 0.8) or 0.8)

        return PeriodCompareResponse(
            label_a=request.period_label_a,
            label_b=request.period_label_b,
            insight=insight,
            confidence=confidence,
        )
    except Exception as e:
        return PeriodCompareResponse(
            label_a=request.period_label_a,
            label_b=request.period_label_b,
            insight=f"Failed to generate comparison: {str(e)}",
            confidence=0.0,
        )