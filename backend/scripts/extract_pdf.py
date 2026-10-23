import sys
sys.path.insert(0, '/app')

import pdfplumber
import json
import re
from pathlib import Path
from decimal import Decimal
from typing import Dict, List, Optional, Any
from datetime import date
from openai import OpenAI
from app.core.config import settings


client = OpenAI(
    base_url=settings.GROQ_BASE_URL,
    api_key=settings.GROQ_API_KEY,
) if settings.GROQ_API_KEY else None


def clean_number(text: str) -> Optional[Decimal]:
    """Extract numeric value from European-formatted string (€354,813 or (485,144))"""
    if not text:
        return None
    # Remove currency symbols, spaces, parentheses for negatives
    cleaned = text.replace("€", "").replace(",", "").replace(" ", "").strip()
    is_negative = cleaned.startswith("(") and cleaned.endswith(")")
    if is_negative:
        cleaned = cleaned[1:-1]
    try:
        val = Decimal(cleaned)
        return -val if is_negative else val
    except:
        return None


# Each label pattern ends with a single value group `([\d,\.]+)` or
# `([-\d,\.]+)`. We rewrite it to also capture a second numeric column so the
# same patterns can read the comparative (prior-period) figures side by side.
VALUE_GROUP_RE = re.compile(r"\(\[-?\\d,\\\.\]\+\)")
TWO_VALUE_GROUPS = r"([-\d,\.]+)(?:\s+([-\d,\.]+))?"


def _with_comparative_column(pattern: str) -> str:
    """Add an optional second capture group for the prior-period column."""
    return VALUE_GROUP_RE.sub(lambda _: TWO_VALUE_GROUPS, pattern)


def _match(pattern: str, pages, full_text: str):
    """Search page by page to record provenance; fall back to the whole text."""
    if pages:
        for num, text in pages:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                return m, num
        return None, None
    m = re.search(pattern, full_text, re.IGNORECASE)
    return (m, None) if m else (None, None)


def _months_between(start: date, end: date) -> int:
    """Inclusive number of whole months covered by a reporting period."""
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


def extract_from_text(full_text: str, column: int = 0, pages=None) -> Dict[str, Any]:
    """Extract financial data from PDF text using regex patterns.

    `column` selects the figure column: 0 = current period (as printed first),
    1 = comparative prior period (printed second).

    When `pages` is a list of ``(page_number, text)`` the source page of every
    matched figure is recorded under ``provenance`` so each number can be traced
    back to the statement it came from.
    """
    group_index = column + 1
    result = {
        "pnl": {},
        "balance_sheet": {},
        "cash_flow": {},
        "full_text": full_text,
        "provenance": {"pnl": {}, "balance_sheet": {}, "cash_flow": {}},
    }

    # P&L patterns (from page 5 text)
    pnl_patterns = {
        "Revenue": [r"Turnover\s+([\d,\.]+)", r"Revenue\s+([\d,\.]+)", r"Group Revenue.*?€([\d,\.]+)k"],
        "Cost of sales": [r"Cost of sales\s+([\d,\.]+)"],
        "Gross Profit": [r"Gross Profit\s+([\d,\.]+)"],
        "Distribution costs": [r"Distribution costs\s+([\d,\.]+)"],
        "Administrative expenses": [r"Administrative expenses\s+([\d,\.]+)"],
        "Other operating income": [r"Other operating income\s+([\d,\.]+)"],
        "Group operating loss": [r"Group operating loss\s+([\d,\.]+)"],
        "Other gains and losses": [r"Other gains and losses\s+([\d,\.]+)"],
        "Interest payable": [r"Interest payable.*?([\d,\.]+)"],
        "Loss before taxation": [r"Loss before taxation\s+([\d,\.]+)"],
        "Tax expense": [r"Tax expense\s+([\d,\.]+)"],
        "Loss for the period": [r"Loss for the period\s+([\d,\.]+)"],
    }

    for key, patterns in pnl_patterns.items():
        for pattern in patterns:
            match, page = _match(_with_comparative_column(pattern), pages, full_text)
            if match:
                val = clean_number(match.group(group_index))
                if val is not None:
                    result["pnl"][key] = val
                    if page:
                        result["provenance"]["pnl"][key] = page
                    break

    # Balance Sheet patterns (from page 5-6 text)
    bs_patterns = {
        "Goodwill": [r"Goodwill\s+([\d,\.]+)"],
        "Development Costs": [r"Development Costs\s+([\d,\.]+)"],
        "Tangible Assets": [r"Tangible Assets\s+([\d,\.]+)"],
        # The source prints these subtotals as unlabelled lines right after the
        # last component, so anchor on that component to read the stated total.
        "Total Fixed Assets": [r"Tangible Assets\s+[\d,\.]+\s+[\d,\.]+\s+([\d,\.]+)"],
        "Debtors": [r"Debtors\s+([\d,\.]+)"],
        "Cash and cash equivalents": [r"Cash and cash equivalents\s+([\d,\.]+)", r"Cash and bank balances\s+([\d,\.]+)"],
        "Total Current Assets": [r"Cash and cash equivalents\s+[\d,\.]+\s+[\d,\.]+\s+([\d,\.]+)"],
        "Creditors < 1yr": [r"Creditors: amounts falling due within one year\s+([-\d,\.]+)", r"Creditors within one year\s+([-\d,\.]+)"],
        "Contingent consideration": [r"Contingent consideration\s+([-\d,\.]+)"],
        "Net Current Assets": [r"Net Current Assets\s+([-\d,\.]+)"],
        "Total Assets Less Current Liabilities": [r"Total Assets Less Current Liabilities\s+([\d,\.]+)"],
        "Creditors > 1yr": [r"Creditors: amounts falling due after more than one\s+year\s+([-\d,\.]+)", r"Creditors after one year\s+([-\d,\.]+)"],
        "Net Assets": [r"Net \(liabilities\) / Assets\s+([\d,\.]+)", r"Net Assets\s+([\d,\.]+)"],
        "Share Capital": [r"Called up share capital.*?([\d,\.]+)"],
        "Share Premium": [r"Share Premium\s+([\d,\.]+)"],
        "Retained Earnings": [r"Retained Earnings\s+([-\d,\.]+)"],
        "Equity": [r"Equity attributable[\s\S]*?([\d,\.]+)"],
    }

    for key, patterns in bs_patterns.items():
        for pattern in patterns:
            match, page = _match(_with_comparative_column(pattern), pages, full_text)
            if match:
                val = clean_number(match.group(group_index))
                if val is not None:
                    result["balance_sheet"][key] = val
                    if page:
                        result["provenance"]["balance_sheet"][key] = page
                    break

    # Cash Flow patterns (from page 6-7 text)
    cf_patterns = {
        "Loss for the period": [r"Loss for the 6 month period\s+([-\d,\.]+)"],
        "Interest payable": [r"Interest payable and similar expenses\s+([\d,\.]+)"],
        "Depreciation": [r"Depreciation\s+([\d,\.]+)"],
        "Working capital movements": [r"Movements in working capital\s+([-\d,\.]+)"],
        "Cash used in operations": [r"Cash used in operations\s+([-\d,\.]+)"],
        "Interest paid": [r"Interest Paid\s+([-\d,\.]+)"],
        "Net cash from operations": [r"Net Cash used in operating activities\s+([-\d,\.]+)"],
        "Capex": [r"Payments to acquire.*?([-\d,\.]+)"],
        "Asset disposals": [r"Receipts from sales.*?([\d,\.]+)"],
        "Net cash from investing": [r"Net Cash used in investment activities\s+([-\d,\.]+)"],
        "Loan repayments": [r"Receipt / \(Repayment\) of loans\s+([-\d,\.]+)"],
        "Equity issuance": [r"Issue of new shares\s+([\d,\.]+)"],
        "Net cash from financing": [r"Net Cash inflow from financing activities\s+([\d,\.]+)"],
        "Net change in cash": [r"Net \(decrease\) / increase in cash[\s\S]*?([-\d,\.]+)"],
        "Cash at start": [r"Cash and cash equivalent at beginning of period\s+([\d,\.]+)"],
        "Cash at end": [r"Cash and cash equivalent at end of period\s+([\d,\.]+)"],
    }

    for key, patterns in cf_patterns.items():
        for pattern in patterns:
            match, page = _match(_with_comparative_column(pattern), pages, full_text)
            if match:
                val = clean_number(match.group(group_index))
                if val is not None:
                    result["cash_flow"][key] = val
                    if page:
                        result["provenance"]["cash_flow"][key] = page
                    break

    _normalise_loss_signs(result)
    return result


def _normalise_loss_signs(result: Dict[str, Any]) -> None:
    """Normalise loss line items to negative values as printed in the document."""
    for key in ("Group operating loss", "Loss before taxation", "Loss for the period"):
        if key in result["pnl"] and result["pnl"][key] > 0:
            result["pnl"][key] = -result["pnl"][key]
    # Cash flow also carries Loss for the period; normalise it too so derived
    # subtotals (EBITDA) compute correctly.
    if "Loss for the period" in result["cash_flow"] and result["cash_flow"]["Loss for the period"] > 0:
        result["cash_flow"]["Loss for the period"] = -result["cash_flow"]["Loss for the period"]


async def extract_narrative_insights(full_text: str, period_label: str) -> Dict[str, str]:
    """Extract qualitative insights, grounded strictly in the report text.

    `full_text` is expected to carry page markers ("[p.N]") so every statement
    can be cited back to the page it came from.
    """
    if not client:
        return {"error": "Groq API key not configured"}

    prompt = f"""You are a financial analyst summarising the Senus PLC {period_label} report.
Use ONLY facts that are explicitly written in the TEXT below. Do NOT infer causes,
forecasts, contracts, partnerships or risks that are not literally stated.

Rules:
1. Every sentence must end with a citation to the page it came from, e.g. [p.5].
2. If the text does not cover a topic, write exactly: "Not disclosed in the report."
3. Never invent numbers, names or events. Do not guess revenue drivers or seasonality.

Return JSON with these fields (2-3 sentences each):
- revenue_commentary: only facts stated about revenue/turnover
- cost_commentary: only facts stated about costs and operating expenses
- strategic_milestones: only milestones explicitly stated
- pipeline_outlook: only outlook/guidance explicitly stated
- key_risks: only risks explicitly stated

TEXT (page markers [p.N] indicate the source page):
{full_text[:12000]}"""

    try:
        response = client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=1000,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        return {"error": str(e)}


def compute_kpis(
    pnl: Dict,
    bs: Dict,
    cf: Dict,
    period_months: int = 6,
) -> Dict[str, float | None]:
    """Return reported, derived, and unavailable metrics without zero-filling."""
    from app.services.metrics import CATALOG

    kpis: Dict[str, float | None] = {
        str(spec["key"]): None for spec in CATALOG
    }

    def value(source: Dict, key: str) -> float | None:
        raw = source.get(key)
        return float(raw) if raw is not None else None

    def first_value(source: Dict, *keys: str) -> float | None:
        for key in keys:
            raw = value(source, key)
            if raw is not None:
                return raw
        return None

    def subtotal(source: Dict, total_key: str, *component_keys: str) -> float | None:
        direct = value(source, total_key)
        if direct is not None:
            return direct
        components = [value(source, key) for key in component_keys]
        if any(component is None for component in components):
            return None
        return sum(component for component in components if component is not None)

    revenue = first_value(pnl, "Revenue", "Turnover")
    gross_profit = value(pnl, "Gross Profit")
    admin_expenses = value(pnl, "Administrative expenses")
    net_loss = value(pnl, "Loss for the period")
    operating_loss = first_value(pnl, "Group operating loss", "Operating loss")
    interest = first_value(pnl, "Interest payable", "Interest paid")

    if revenue is not None:
        kpis["revenue"] = revenue

    cash = first_value(bs, "Cash and cash equivalents", "Cash and bank balances")
    if cash is not None:
        kpis["cash"] = cash

    if operating_loss is not None and operating_loss > 0:
        operating_loss = -operating_loss
    if net_loss is not None and net_loss > 0:
        net_loss = -net_loss

    total_fixed_assets = subtotal(
        bs, "Total Fixed Assets", "Goodwill", "Development Costs", "Tangible Assets"
    )
    total_current_assets = subtotal(
        bs, "Total Current Assets", "Debtors", "Cash and cash equivalents"
    )
    total_assets = (
        total_fixed_assets + total_current_assets
        if total_fixed_assets is not None and total_current_assets is not None
        else None
    )
    equity = first_value(bs, "Equity", "Net Assets")
    short_creditors = value(bs, "Creditors < 1yr")
    long_creditors = value(bs, "Creditors > 1yr")
    contingent = value(bs, "Contingent consideration")
    depreciation = value(cf, "Depreciation")
    ebitda = value(cf, "EBITDA")
    if ebitda is None and operating_loss is not None and depreciation is not None:
        ebitda = operating_loss + depreciation
    if ebitda is not None:
        kpis["ebitda"] = ebitda

    if revenue not in (None, 0):
        if gross_profit is not None:
            kpis["gross_margin"] = gross_profit / revenue * 100
        if operating_loss is not None:
            kpis["operating_margin"] = operating_loss / revenue * 100
        if ebitda is not None:
            kpis["ebitda_margin"] = ebitda / revenue * 100
        if net_loss is not None:
            kpis["net_margin"] = net_loss / revenue * 100
        if admin_expenses is not None:
            kpis["admin_expense_ratio"] = admin_expenses / revenue * 100

    net_cash_ops = value(cf, "Net cash from operations")
    capex = value(cf, "Capex")
    fcf = net_cash_ops + capex if net_cash_ops is not None and capex is not None else None
    if fcf is not None:
        kpis["fcf"] = fcf
        if fcf < 0 and period_months > 0:
            monthly_burn = abs(fcf) / period_months
            kpis["monthly_burn"] = monthly_burn
            if cash is not None and monthly_burn > 0:
                kpis["runway_months"] = cash / monthly_burn

    if cash is not None and long_creditors is not None:
        kpis["net_cash"] = cash - abs(long_creditors)

    if total_current_assets is not None and short_creditors is not None:
        current_liabilities = abs(short_creditors)
        if contingent is not None:
            liabilities_including_contingent = current_liabilities + abs(contingent)
            if liabilities_including_contingent > 0:
                kpis["current_ratio"] = total_current_assets / liabilities_including_contingent
        if current_liabilities > 0:
            kpis["current_ratio_ex_contingent"] = total_current_assets / current_liabilities

    if total_assets is not None:
        kpis["total_assets"] = total_assets
        if equity is not None and total_assets != 0:
            kpis["equity_ratio"] = equity / total_assets * 100
        goodwill = value(bs, "Goodwill")
        development_costs = value(bs, "Development Costs")
        if (
            goodwill is not None
            and development_costs is not None
            and total_assets != 0
        ):
            kpis["intangibles_ratio"] = (
                (goodwill + development_costs) / total_assets * 100
            )

    if equity not in (None, 0) and long_creditors is not None:
        kpis["debt_to_equity"] = abs(long_creditors) / equity * 100
    if operating_loss is not None and interest not in (None, 0):
        kpis["interest_cover"] = operating_loss / interest

    loan_repayments = value(cf, "Loan repayments")
    if net_cash_ops is not None and net_cash_ops > 0:
        if interest is not None and loan_repayments is not None:
            debt_service = abs(interest) + abs(loan_repayments)
            if debt_service > 0:
                kpis["dscr"] = net_cash_ops / debt_service

    if total_assets is not None and short_creditors is not None and contingent is not None:
        capital_employed = total_assets - abs(short_creditors) - abs(contingent)
        if capital_employed != 0 and operating_loss is not None:
            roce = operating_loss / capital_employed * 100
            kpis["roce"] = roce
            if period_months > 0:
                kpis["roce_annualised"] = roce * 12 / period_months
    if total_assets is not None and short_creditors is not None:
        capital_employed_ex_contingent = total_assets - abs(short_creditors)
        if capital_employed_ex_contingent != 0 and operating_loss is not None:
            kpis["roce_ex_contingent"] = (
                operating_loss / capital_employed_ex_contingent * 100
            )
    if equity not in (None, 0) and net_loss is not None:
        kpis["roe"] = net_loss / equity * 100

    return kpis


async def process_pdf(
    pdf_path: str,
    period_label: str,
    start_date: date,
    end_date: date,
    column: int = 0,
    with_narrative: bool = True,
) -> Dict[str, Any]:
    """Full pipeline: extract text + parse financials + narrative AI + compute KPIs"""
    print(f"Processing {pdf_path} for {period_label}...", file=sys.stderr)

    # 1. Extract text page by page (keep page numbers for provenance)
    pages = []
    full_text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for idx, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            pages.append((idx, text))
            full_text += text

    # 2. Parse financial data from text
    tables = extract_from_text(full_text, column=column, pages=pages)

    # 3. Extract narrative insights via AI (page-marked text so facts can be cited)
    annotated_text = "\n".join(f"[p.{num}]\n{text}" for num, text in pages)
    narrative = (
        await extract_narrative_insights(annotated_text, period_label) if with_narrative else {}
    )

    # 4. Compute KPIs
    from app.services.period_detector import period_duration_months

    kpis = compute_kpis(
        tables["pnl"],
        tables["balance_sheet"],
        tables["cash_flow"],
        period_duration_months(start_date, end_date),
    )

    from app.services.validation import validate_period

    validations = [
        issue.as_dict()
        for issue in validate_period(
            tables["pnl"], tables["balance_sheet"], tables["cash_flow"]
        )
    ]

    return {
        "period_label": period_label,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "pnl": {k: str(v) for k, v in tables["pnl"].items()},
        "balance_sheet": {k: str(v) for k, v in tables["balance_sheet"].items()},
        "cash_flow": {k: str(v) for k, v in tables["cash_flow"].items()},
        "kpis": kpis,
        "validations": validations,
        "narrative_insights": narrative,
        "source_file": Path(pdf_path).name,
        "metadata": {
            "document_title": Path(pdf_path).stem,
            "currency": "EUR",
            "period_months": period_duration_months(start_date, end_date),
            "audited": False,
        },
        "provenance": tables["provenance"],
    }


if __name__ == "__main__":
    import asyncio
    import sys

    if len(sys.argv) < 2:
        print("Usage: python extract_pdf.py <pdf_path> [period_label] [start_date] [end_date] [column]")
        print("Example: python extract_pdf.py ../Senus_HalfYearResultsDec2025.pdf HY2026 2025-07-01 2025-12-31")
        print("Column: 0 = current period (default), 1 = prior-period comparative column")
        sys.exit(1)

    pdf_path = sys.argv[1]
    period_label = sys.argv[2] if len(sys.argv) > 2 else "UNKNOWN"
    start_date = date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else date(2025, 7, 1)
    end_date = date.fromisoformat(sys.argv[4]) if len(sys.argv) > 4 else date(2025, 12, 31)
    column = int(sys.argv[5]) if len(sys.argv) > 5 else 0

    result = asyncio.run(
        process_pdf(
            pdf_path,
            period_label,
            start_date,
            end_date,
            column=column,
            with_narrative=(column == 0),
        )
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))