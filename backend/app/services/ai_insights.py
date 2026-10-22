from typing import List, Optional
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.models.financial import Period, PnLItem, BalanceSheetItem, CashFlowItem, KPI
from app.schemas.financial import AIInsight, PeriodCompareRequest, PeriodCompareResponse
from app.core.config import settings
from openai import AsyncOpenAI


client = AsyncOpenAI(
    base_url=settings.GROQ_BASE_URL,
    api_key=settings.GROQ_API_KEY,
) if settings.GROQ_API_KEY else None


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
    kpis = {k.metric: float(k.value) for k in period.kpis}

    cash = bs.get("Cash and cash equivalents", bs.get("Cash and bank balances", 0))
    total_creditors = abs(bs.get("Creditors < 1yr", 0)) + abs(bs.get("Creditors > 1yr", 0))
    contingent = abs(bs.get("Contingent consideration", 0))
    equity = bs.get("Equity") or bs.get("Net Assets", 0)
    intangibles = bs.get("Goodwill", 0) + bs.get("Development Costs", 0)

    prompt = f"""You are a CFO-grade financial analyst. Analyze Senus PLC's {period_label} financials and provide 3-5 concise, actionable insights for the Board.

FINANCIAL DATA ({period_label}):
P&L:
- Revenue: €{pnl.get('Revenue', pnl.get('Turnover', 0)):,.0f}
- Cost of Sales: €{pnl.get('Cost of sales', 0):,.0f}
- Gross Profit: €{pnl.get('Gross Profit', 0):,.0f}
- Admin Expenses: €{pnl.get('Administrative expenses', 0):,.0f}
- Operating Loss: €{pnl.get('Group operating loss', pnl.get('Operating loss', 0)):,.0f}
- Loss Before Taxation: €{pnl.get('Loss before taxation', 0):,.0f}
- Loss for the Period: €{pnl.get('Loss for the period', 0):,.0f}

Balance Sheet:
- Cash: €{cash:,.0f}
- Total Creditors (short + long term): €{total_creditors:,.0f}
- Contingent Consideration: €{contingent:,.0f}
- Net Current Assets: €{bs.get('Net Current Assets', 0):,.0f}
- Equity / Net Assets: €{equity:,.0f}
- Goodwill + Development Costs: €{intangibles:,.0f}

KPIs:
- Gross Margin: {kpis.get('gross_margin', 0):.1f}%
- EBITDA Margin: {kpis.get('ebitda_margin', 0):.1f}%
- Net Margin: {kpis.get('net_margin', 0):.1f}%
- Cash Runway: {kpis.get('runway_months', 0):.1f} months
- Current Ratio: {kpis.get('current_ratio', 0):.2f}x (ex-contingent {kpis.get('current_ratio_ex_contingent', 0):.2f}x)
- Equity Ratio: {kpis.get('equity_ratio', 0):.1f}%
- Intangibles / Total Assets: {kpis.get('intangibles_ratio', 0):.1f}%
- ROE: {kpis.get('roe', 0):.1f}%
- ROCE: {kpis.get('roce', 0):.1f}%
- DSCR: {('%.2fx' % kpis['dscr']) if 'dscr' in kpis else 'not significant (negative operating cash flow)'}

CONTEXT: You are analyzing financials for period {period_label} (6 months).
Do not reference events from other periods unless they are explicitly documented for {period_label}.
Do not state long-term targets (e.g. CAGR) unless explicitly stated in the data for {period_label}.

STRICT RULES:
- Use ONLY the figures and facts in FINANCIAL DATA and CONTEXT above. Never invent events, causes, costs, contracts or forecasts.
- Every numeric claim must match a figure above; do not restate a figure as text without the number.
- Do not claim new contracts, partnerships or funding unless explicitly named above.
- If something material is missing (e.g. no prior-year comparator), say so instead of estimating.
- This is a 6-month period ({period_label}); do not conflate with full-year or other periods.
- Do not contradict computed KPIs (e.g. if DSCR is marked "not significant", never state a DSCR value or imply positive debt service coverage).
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
    if not client:
        return PeriodCompareResponse(
            label_a=request.period_label_a,
            label_b=request.period_label_b,
            insight="Groq API key not configured. Set GROQ_API_KEY environment variable.",
            confidence=0.0,
        )

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

    p_a = {item.line_item: float(item.value_eur) for item in period_a.pnl_items}
    b_a = {item.line_item: float(item.value_eur) for item in period_a.bs_items}
    kpis_a = {k.metric: float(k.value) for k in period_a.kpis}

    p_b = {item.line_item: float(item.value_eur) for item in period_b.pnl_items}
    b_b = {item.line_item: float(item.value_eur) for item in period_b.bs_items}
    kpis_b = {k.metric: float(k.value) for k in period_b.kpis}

    def _fmt_money(v: float) -> str:
        return f"€{v:,.0f}"

    def _pct_change(a_val: float, b_val: float) -> str:
        if not a_val:
            return "n/a"
        return f"{((b_val - a_val) / abs(a_val)) * 100:+.1f}%"

    prompt = f"""You are a CFO-grade financial analyst comparing two periods for Senus PLC.

PERIOD A: {request.period_label_a}
P&L:
- Revenue: €{p_a.get('Revenue', p_a.get('Turnover', 0)):,.0f}
- Cost of Sales: €{p_a.get('Cost of sales', 0):,.0f}
- Gross Profit: €{p_a.get('Gross Profit', 0):,.0f}
- Admin Expenses: €{p_a.get('Administrative expenses', 0):,.0f}
- Operating Loss: €{p_a.get('Group operating loss', p_a.get('Operating loss', 0)):,.0f}
- Loss for the Period: €{p_a.get('Loss for the period', 0):,.0f}

Balance Sheet:
- Cash: €{b_a.get('Cash and cash equivalents', b_a.get('Cash and bank balances', 0)):,.0f}
- Total Creditors: €{abs(b_a.get('Creditors < 1yr', 0)) + abs(b_a.get('Creditors > 1yr', 0)):,.0f}
- Equity / Net Assets: €{b_a.get('Equity') or b_a.get('Net Assets', 0):,.0f}

PERIOD B: {request.period_label_b}
P&L:
- Revenue: €{p_b.get('Revenue', p_b.get('Turnover', 0)):,.0f}
- Cost of Sales: €{p_b.get('Cost of sales', 0):,.0f}
- Gross Profit: €{p_b.get('Gross Profit', 0):,.0f}
- Admin Expenses: €{p_b.get('Administrative expenses', 0):,.0f}
- Operating Loss: €{p_b.get('Group operating loss', p_b.get('Operating loss', 0)):,.0f}
- Loss for the Period: €{p_b.get('Loss for the period', 0):,.0f}

Balance Sheet:
- Cash: €{b_b.get('Cash and cash equivalents', b_b.get('Cash and bank balances', 0)):,.0f}
- Total Creditors: €{abs(b_b.get('Creditors < 1yr', 0)) + abs(b_b.get('Creditors > 1yr', 0)):,.0f}
- Equity / Net Assets: €{b_b.get('Equity') or b_b.get('Net Assets', 0):,.0f}

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