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


def extract_from_text(full_text: str, column: int = 0) -> Dict[str, Any]:
    """Extract financial data from PDF text using regex patterns.

    `column` selects the figure column: 0 = current period (as printed first),
    1 = comparative prior period (printed second).
    """
    group_index = column + 1
    result = {
        "pnl": {},
        "balance_sheet": {},
        "cash_flow": {},
        "full_text": full_text
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
            match = re.search(_with_comparative_column(pattern), full_text, re.IGNORECASE)
            if match:
                val = clean_number(match.group(group_index))
                if val is not None:
                    result["pnl"][key] = val
                    break

    # Balance Sheet patterns (from page 5-6 text)
    bs_patterns = {
        "Goodwill": [r"Goodwill\s+([\d,\.]+)"],
        "Development Costs": [r"Development Costs\s+([\d,\.]+)"],
        "Tangible Assets": [r"Tangible Assets\s+([\d,\.]+)"],
        "Debtors": [r"Debtors\s+([\d,\.]+)"],
        "Cash and cash equivalents": [r"Cash and cash equivalents\s+([\d,\.]+)", r"Cash and bank balances\s+([\d,\.]+)"],
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
            match = re.search(_with_comparative_column(pattern), full_text, re.IGNORECASE)
            if match:
                val = clean_number(match.group(group_index))
                if val is not None:
                    result["balance_sheet"][key] = val
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
            match = re.search(_with_comparative_column(pattern), full_text, re.IGNORECASE)
            if match:
                val = clean_number(match.group(group_index))
                if val is not None:
                    result["cash_flow"][key] = val
                    break

    # Derived subtotals: the source tables print these as unlabelled totals
    bs = result["balance_sheet"]
    zero = Decimal("0")
    if "Goodwill" in bs or "Tangible Assets" in bs:
        bs["Total Fixed Assets"] = (
            bs.get("Goodwill", zero)
            + bs.get("Development Costs", zero)
            + bs.get("Tangible Assets", zero)
        )
    if "Debtors" in bs or "Cash and cash equivalents" in bs:
        bs["Total Current Assets"] = bs.get("Debtors", zero) + bs.get("Cash and cash equivalents", zero)

    cf = result["cash_flow"]
    if "Loss for the period" in cf:
        cf["EBITDA"] = (
            cf.get("Loss for the period", zero)
            + cf.get("Interest payable", zero)
            + cf.get("Depreciation", zero)
        )

    # Normalise loss line items to negative (source presents costs/losses as positive)
    for key in ("Group operating loss", "Loss before taxation", "Loss for the period"):
        if key in result["pnl"] and result["pnl"][key] > 0:
            result["pnl"][key] = -result["pnl"][key]

    return result


async def extract_narrative_insights(full_text: str, period_label: str) -> Dict[str, str]:
    """Use Groq LLM to extract qualitative insights from narrative sections"""
    if not client:
        return {"error": "Groq API key not configured"}

    prompt = f"""Extract key qualitative insights from this Senus PLC {period_label} report text.

FOCUS ON:
1. Revenue drivers and headwinds (new contracts, channels, seasonality)
2. Cost structure changes (Loamin integration, hiring, R&D)
3. Strategic milestones (listing, acquisition, funding, partnerships)
4. Pipeline and outlook signals
5. Risk factors mentioned

Return JSON with these fields:
- revenue_commentary: 2-3 sentences
- cost_commentary: 2-3 sentences
- strategic_milestones: 2-3 sentences
- pipeline_outlook: 2-3 sentences
- key_risks: 2-3 sentences

TEXT:
{full_text[:8000]}"""

    try:
        response = client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1000,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        return {"error": str(e)}


def compute_kpis(pnl: Dict, bs: Dict, cf: Dict) -> Dict[str, float]:
    """Compute derived KPIs using the reference-document methodology."""
    kpis: Dict[str, float] = {}

    def f(source: Dict, key: str) -> float:
        return float(source.get(key, 0) or 0)

    revenue = f(pnl, "Revenue")
    gross_profit = f(pnl, "Gross Profit")
    admin_exp = f(pnl, "Administrative expenses")
    net_loss = f(pnl, "Loss for the period")
    operating_loss = f(pnl, "Group operating loss")  # stored negative
    interest = f(pnl, "Interest payable")

    cash = f(bs, "Cash and cash equivalents")
    creditors_short = f(bs, "Creditors < 1yr")            # negative
    creditors_long = f(bs, "Creditors > 1yr")             # negative
    contingent = f(bs, "Contingent consideration")        # negative
    equity = f(bs, "Equity") or f(bs, "Net Assets")
    total_fixed_assets = f(bs, "Total Fixed Assets")
    total_current_assets = f(bs, "Total Current Assets")

    depreciation = f(cf, "Depreciation")
    ebitda = operating_loss + depreciation                # add back depreciation
    ebit = operating_loss                                 # operating loss used as EBIT proxy
    net_cash_ops = f(cf, "Net cash from operations")
    capex = f(cf, "Capex")
    loan_repayments = f(cf, "Loan repayments")

    # --- Profitability ---
    if revenue:
        kpis["gross_margin"] = gross_profit / revenue * 100
        kpis["operating_margin"] = operating_loss / revenue * 100
        kpis["ebitda_margin"] = ebitda / revenue * 100
        kpis["net_margin"] = net_loss / revenue * 100
        kpis["admin_expense_ratio"] = admin_exp / revenue * 100

    # --- Cash & burn ---
    fcf = net_cash_ops + capex                            # -418,791
    monthly_burn = abs(fcf) / 6
    if monthly_burn > 0:
        kpis["fcf"] = fcf
        kpis["monthly_burn"] = monthly_burn
        kpis["runway_months"] = cash / monthly_burn
    kpis["net_cash"] = cash - abs(creditors_long)

    # --- Liquidity ---
    current_liabilities = abs(creditors_short) + abs(contingent)
    if total_current_assets:
        if current_liabilities:
            kpis["current_ratio"] = total_current_assets / current_liabilities
        if abs(creditors_short):
            kpis["current_ratio_ex_contingent"] = total_current_assets / abs(creditors_short)

    # --- Solvency & leverage ---
    total_assets = total_fixed_assets + total_current_assets
    if total_assets:
        kpis["total_assets"] = total_assets
        kpis["equity_ratio"] = equity / total_assets * 100
        intangibles = f(bs, "Goodwill") + f(bs, "Development Costs")
        kpis["intangibles_ratio"] = intangibles / total_assets * 100
    if equity and abs(creditors_long):
        kpis["debt_to_equity"] = abs(creditors_long) / equity * 100
    if interest:
        kpis["interest_cover"] = ebit / interest

    # DSCR is only meaningful with positive cash available for debt service
    debt_service = interest + abs(loan_repayments)
    if net_cash_ops > 0 and debt_service:
        kpis["dscr"] = net_cash_ops / debt_service

    # --- Returns ---
    capital_employed = total_assets - current_liabilities
    if capital_employed:
        kpis["roce"] = ebit / capital_employed * 100
        kpis["roce_annualised"] = kpis["roce"] * 2
    if equity:
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

    # 1. Extract full text
    full_text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            full_text += page.extract_text() or ""

    # 2. Parse financial data from text
    tables = extract_from_text(full_text, column=column)

    # 3. Extract narrative insights via AI
    narrative = await extract_narrative_insights(full_text, period_label) if with_narrative else {}

    # 4. Compute KPIs
    kpis = compute_kpis(tables["pnl"], tables["balance_sheet"], tables["cash_flow"])

    return {
        "period_label": period_label,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "pnl": {k: str(v) for k, v in tables["pnl"].items()},
        "balance_sheet": {k: str(v) for k, v in tables["balance_sheet"].items()},
        "cash_flow": {k: str(v) for k, v in tables["cash_flow"].items()},
        "kpis": kpis,
        "narrative_insights": narrative,
        "source_file": Path(pdf_path).name,
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