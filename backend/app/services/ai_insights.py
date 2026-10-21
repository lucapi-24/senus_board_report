from typing import List, Tuple, Optional
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.models.financial import Period, PnLItem, BalanceSheetItem, CashFlowItem, KPI, Document
from app.schemas.financial import AIInsight, ChatRequest, ChatResponse
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
- Intangibles / Total Assets: {kpis.get('intangibles_ratio', 0):.1f}%
- ROE: {kpis.get('roe', 0):.1f}%
- ROCE: {kpis.get('roce', 0):.1f}%
- DSCR: {('%.2fx' % kpis['dscr']) if 'dscr' in kpis else 'not significant (negative operating cash flow)'}

    CONTEXT: You are analyzing financials for period {period_label} (6 months).
             Do not reference events from other periods unless they are explicitly
             documented for {period_label}. Do not state long-term targets
             (e.g. CAGR) unless explicitly stated in the data for {period_label}.

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


async def chat_with_document(
    request: ChatRequest,
    db: AsyncSession,
) -> ChatResponse:
    """Chat with AI about a specific period's financial document.

    Uses the period's financial data + the raw document text as context so the
    CEO can ask specific questions about the figures.
    """
    if not client:
        return ChatResponse(
            message="Groq API key not configured. Set GROQ_API_KEY environment variable.",
            sources=[],
        )

    # Fetch period + document
    result = await db.execute(
        select(Period)
        .options(
            selectinload(Period.pnl_items),
            selectinload(Period.bs_items),
            selectinload(Period.cf_items),
            selectinload(Period.kpis),
            selectinload(Period.source_document),
        )
        .where(Period.label == request.period_label)
    )
    period = result.scalar_one_or_none()
    if not period:
        return ChatResponse(
            message=f"Period '{request.period_label}' not found.",
            sources=[],
        )

    # Build financial context
    pnl = {item.line_item: float(item.value_eur) for item in period.pnl_items}
    bs = {item.line_item: float(item.value_eur) for item in period.bs_items}
    cf = {item.line_item: float(item.value_eur) for item in period.cf_items}
    kpis = {k.metric: float(k.value) for k in period.kpis}

    doc = period.source_document
    doc_text = ""
    if doc and doc.raw_json:
        try:
            import json as _json
            raw = _json.loads(doc.raw_json)
            if isinstance(raw, dict):
                # Extract narrative text from the extracted JSON
                narrative = raw.get("narrative_insights", {})
                if isinstance(narrative, dict):
                    for _k, _v in narrative.items():
                        if isinstance(_v, str):
                            doc_text += f"{_k}: {_v}\n"
        except Exception:
            pass

    # Build conversation history
    messages: list[dict] = [
        {
            "role": "system",
            "content": (
                "Eres un analista financiero senior que responde al CEO de Senus PLC. "
                "Tu trabajo es responder preguntas SOBRE este documento financiero específico. "
                "\n\nREGLAS DE ORO:\n"
                "1. RESPUESTAS CORTAS Y DIRECTAS: Máximo 2-3 párrafos por respuesta. Sin listas largas de números.\n"
                "2. CONTEXTO PRIMERO, NÚMEROS DESPUÉS: Explica qué significa la cifra antes de decirla. "
                "   Mal: 'El revenue es 354,813€'. Bien: 'Los ingresos crecieron un 4% respecto al año anterior, alcanzando 354,813€.'\n"
                "3. COMPARA CUANDO TENGA SENTIDO: Si el usuario pregunta sobre una cifra, compara con el periodo anterior si está disponible.\n"
                "4. CITA PÁGINAS: Si la información viene de una página concreta del documento, añade [p.X] al final de la frase.\n"
                "5. NO INVENTES: Si la información no está en los datos proporcionados, di 'No está disponible en este documento'.\n"
                "6. ENFOQUE EJECUTIVO: Prioriza rentabilidad, caja y riesgos sobre detalle contable menor.\n"
                "7. IDIOMA: Responde siempre en español.\n"
            ),
        },
    ]

    # Add financial summary as context (not as the main answer)
    cash = bs.get("Cash and cash equivalents", bs.get("Cash and bank balances", 0))
    total_creditors = abs(bs.get("Creditors < 1yr", 0)) + abs(bs.get("Creditors > 1yr", 0))
    equity = bs.get("Equity") or bs.get("Net Assets", 0)

    fin_context = f"""DATOS FINANCIEROS DE REFERENCIA ({period.label}):
P&L:
- Ingresos (Revenue/Turnover): €{pnl.get('Revenue', pnl.get('Turnover', 0)):,.0f}
- Coste de ventas: €{pnl.get('Cost of sales', 0):,.0f}
- Bruto: €{pnl.get('Gross Profit', 0):,.0f}
- Gastos administrativos: €{pnl.get('Administrative expenses', 0):,.0f}
- Pérdida operativa: €{pnl.get('Group operating loss', pnl.get('Operating loss', 0)):,.0f}
- Pérdida del periodo: €{pnl.get('Loss for the period', 0):,.0f}

Balance:
- Caja: €{cash:,.0f}
- Créditos totales: €{total_creditors:,.0f}
- Activo corriente neto: €{bs.get('Net Current Assets', 0):,.0f}
- Patrimonio neto: €{equity:,.0f}

KPIs:
- Margen bruto: {kpis.get('gross_margin', 0):.1f}%
- Margen neto: {kpis.get('net_margin', 0):.1f}%
- Runway de caja: {kpis.get('runway_months', 0):.1f} meses
- Ratio de liquidez corriente: {kpis.get('current_ratio', 0):.2f}x
- ROE: {kpis.get('roe', 0):.1f}%"""

    messages.append({"role": "system", "content": fin_context})

    # Add conversation history
    for m in request.history:
        messages.append({"role": m.role, "content": m.content})

    # Add current user message
    messages.append({"role": "user", "content": request.message})

    import asyncio, json

    response = None
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = await client.chat.completions.create(
                model=settings.GROQ_MODEL,
                messages=messages,
                temperature=0.2,
                max_tokens=1000,
            )
            break
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(1.5 * (attempt + 1))
    if response is None:
        raise last_error if last_error else RuntimeError("No response from model")

    content = response.choices[0].message.content or "No response generated."

    # Extract cited pages from content (e.g. [p.5])
    import re
    cited_pages = sorted(set(re.findall(r"\[p\.(\d+)\]", content)))

    return ChatResponse(
        message=content,
        sources=[f"Page {p}" for p in cited_pages],
    )