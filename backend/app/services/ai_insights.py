from typing import List
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.models.financial import Period, PnLItem, BalanceSheetItem, KPI
from app.schemas.financial import AIInsight
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

    # Fetch financial data
    result = await db.execute(
        select(Period)
        .options(
            selectinload(Period.pnl_items),
            selectinload(Period.bs_items),
            selectinload(Period.kpis),
        )
        .where(Period.label == period_label)
    )
    period = result.scalar_one_or_none()
    if not period:
        return [AIInsight(section="Error", insight=f"Period {period_label} not found", confidence=0.0)]

    pnl = {item.line_item: float(item.value_eur) for item in period.pnl_items}
    bs = {item.line_item: float(item.value_eur) for item in period.bs_items}
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
- Loss After Tax: €{pnl.get('Loss for the period', 0):,.0f}

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
- ROE: {kpis.get('roe', 0):.1f}%
- ROCE: {kpis.get('roce', 0):.1f}%
- DSCR: {('%.2fx' % kpis['dscr']) if 'dscr' in kpis else 'not significant (negative operating cash flow)'}

CONTEXT: Senus is an early-stage Natural Capital SaaS company, listed Dec 2025 on Euronext Access. Strategy: 50% CAGR to 2030. Acquired Loamin (geospatial AI) in H1. €1.1m equity raised. Pre-revenue scaling phase.

Return JSON array of insights with: section, insight, confidence (0-1). Focus on: revenue quality, cost discipline, cash runway, strategic milestones, risks."""

    import asyncio

    try:
        response = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await client.chat.completions.create(
                    model=settings.GROQ_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
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

        # The model may return {"insights": [...]}, a bare list, a single
        # insight object, or a dict of section -> text. Normalise all shapes.
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