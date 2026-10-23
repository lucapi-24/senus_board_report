"""Metrics engine: a single catalogue of the derived KPIs plus pure helpers to
compare a metric across two periods (period-over-period deltas).

The catalogue is descriptive metadata only — the actual values are produced by
``compute_kpis`` in ``scripts/extract_pdf.py`` (the single source of truth,
shared by the extractor and the ingestion pipeline). This module keeps the
human-facing definition of every metric in one place so the API, the dashboard
and the documentation can agree on labels, units and formulas.
"""

from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Optional

# unit: '%' | 'EUR' | 'x' | 'months'   direction: 'up' | 'down' | 'neutral'
CATALOG: List[Dict[str, object]] = [
    # --- Reported data ---
    {
        "key": "revenue",
        "label": "Revenue",
        "unit": "EUR",
        "category": "Reported data",
        "direction": "neutral",
        "formula": "As reported in the income statement",
        "kind": "reported",
    },
    {
        "key": "cash",
        "label": "Cash",
        "unit": "EUR",
        "category": "Reported data",
        "direction": "neutral",
        "formula": "As reported in the balance sheet or identified source",
        "kind": "reported",
    },
    {
        "key": "customer_accounts",
        "label": "Customer accounts",
        "unit": "count",
        "category": "Reported data",
        "direction": "neutral",
        "formula": "As reported in an identified source",
        "kind": "reported",
    },
    # --- Data not provided by the current documents ---
    {
        "key": "revenue_mom",
        "label": "Revenue growth month over month",
        "unit": "%",
        "category": "Unavailable data",
        "direction": "neutral",
        "formula": "",
        "kind": "na",
        "note": "Monthly revenue data is not available.",
    },
    {
        "key": "sales_channels",
        "label": "Revenue by sales channel",
        "unit": "EUR",
        "category": "Unavailable data",
        "direction": "neutral",
        "formula": "",
        "kind": "na",
        "note": "Revenue by sales channel is not available.",
    },
    {
        "key": "bookings",
        "label": "Bookings",
        "unit": "EUR",
        "category": "Unavailable data",
        "direction": "neutral",
        "formula": "",
        "kind": "na",
        "note": "Bookings data is not available.",
    },
    # --- Profitability ---
    {
        "key": "ebitda",
        "label": "EBITDA",
        "unit": "EUR",
        "category": "Profitability",
        "direction": "neutral",
        "formula": "Group operating loss + Depreciation",
    },
    {
        "key": "gross_margin",
        "label": "Gross margin",
        "unit": "%",
        "category": "Profitability",
        "direction": "up",
        "formula": "Gross profit / Revenue × 100",
    },
    {
        "key": "operating_margin",
        "label": "Operating margin",
        "unit": "%",
        "category": "Profitability",
        "direction": "up",
        "formula": "Group operating loss / Revenue × 100",
    },
    {
        "key": "ebitda_margin",
        "label": "EBITDA margin",
        "unit": "%",
        "category": "Profitability",
        "direction": "up",
        "formula": "(Group operating loss + Depreciation) / Revenue × 100",
    },
    {
        "key": "net_margin",
        "label": "Net margin",
        "unit": "%",
        "category": "Profitability",
        "direction": "up",
        "formula": "Loss for the period / Revenue × 100",
    },
    {
        "key": "admin_expense_ratio",
        "label": "Admin expense ratio",
        "unit": "%",
        "category": "Profitability",
        "direction": "down",
        "formula": "Administrative expenses / Revenue × 100",
    },
    # --- Cash & burn ---
    {
        "key": "fcf",
        "label": "Free cash flow",
        "unit": "EUR",
        "category": "Cash & burn",
        "direction": "up",
        "formula": "Net cash from operations + Capex",
    },
    {
        "key": "monthly_burn",
        "label": "Monthly cash burn",
        "unit": "EUR",
        "category": "Cash & burn",
        "direction": "down",
        "formula": "abs(Free cash flow) / period duration in months",
        "note": "Unavailable if free cash flow is not reported or is not negative.",
    },
    {
        "key": "runway_months",
        "label": "Cash runway",
        "unit": "months",
        "category": "Cash & burn",
        "direction": "up",
        "formula": "Cash / Monthly burn",
        "note": "Requires a reported cash balance and negative free cash flow.",
    },
    {
        "key": "net_cash",
        "label": "Net cash (ex long-term debt)",
        "unit": "EUR",
        "category": "Cash & burn",
        "direction": "up",
        "formula": "Cash − abs(Creditors > 1yr)",
    },
    # --- Liquidity ---
    {
        "key": "current_ratio",
        "label": "Current ratio",
        "unit": "x",
        "category": "Liquidity",
        "direction": "up",
        "formula": "Total current assets / (abs(Creditors < 1yr) + abs(Contingent consideration))",
    },
    {
        "key": "current_ratio_ex_contingent",
        "label": "Current ratio (ex-contingent)",
        "unit": "x",
        "category": "Liquidity",
        "direction": "up",
        "formula": "Total current assets / abs(Creditors < 1yr)",
    },
    # --- Solvency & leverage ---
    {
        "key": "total_assets",
        "label": "Total assets",
        "unit": "EUR",
        "category": "Solvency & leverage",
        "direction": "neutral",
        "formula": "Total fixed assets + Total current assets",
    },
    {
        "key": "equity_ratio",
        "label": "Equity ratio",
        "unit": "%",
        "category": "Solvency & leverage",
        "direction": "up",
        "formula": "Equity / Total assets × 100",
    },
    {
        "key": "intangibles_ratio",
        "label": "Intangibles ratio",
        "unit": "%",
        "category": "Solvency & leverage",
        "direction": "down",
        "formula": "(Goodwill + Development costs) / Total assets × 100",
        "note": "High intangible weight reduces the tangible asset backing.",
    },
    {
        "key": "debt_to_equity",
        "label": "Debt / equity",
        "unit": "%",
        "category": "Solvency & leverage",
        "direction": "down",
        "formula": "abs(Creditors > 1yr) / Equity × 100",
    },
    {
        "key": "interest_cover",
        "label": "Interest cover",
        "unit": "x",
        "category": "Solvency & leverage",
        "direction": "up",
        "formula": "EBIT / Interest payable",
        "note": "Negative while the group is loss-making.",
    },
    {
        "key": "dscr",
        "label": "Debt service cover",
        "unit": "x",
        "category": "Solvency & leverage",
        "direction": "up",
        "formula": "Net cash from operations / (Interest + abs(Loan repayments))",
        "note": "Omitted when cash available for debt service is negative.",
    },
    # --- Returns ---
    {
        "key": "roce",
        "label": "Return on capital employed",
        "unit": "%",
        "category": "Returns",
        "direction": "up",
        "formula": "EBIT / (Total assets − current liabilities) × 100",
    },
    {
        "key": "roce_annualised",
        "label": "ROCE (annualised)",
        "unit": "%",
        "category": "Returns",
        "direction": "up",
        "formula": "ROCE × 12 / period duration in months",
    },
    {
        "key": "roce_ex_contingent",
        "label": "ROCE (ex-contingent)",
        "unit": "%",
        "category": "Returns",
        "direction": "up",
        "formula": "EBIT / (Total assets − abs(Creditors < 1yr)) × 100",
    },
    {
        "key": "roe",
        "label": "Return on equity",
        "unit": "%",
        "category": "Returns",
        "direction": "up",
        "formula": "Loss for the period / Equity × 100",
    },
]

CATALOG_BY_KEY: Dict[str, Dict[str, object]] = {m["key"]: m for m in CATALOG}


def kpi_metadata(
    metric: str,
    value: Optional[float],
    cash_flow: Optional[Dict[str, float]] = None,
    source_note: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """Return consistent origin, formula, and unavailable-data details."""
    spec = CATALOG_BY_KEY[metric]
    expected_kind = str(spec.get("kind", "derived"))
    kind = expected_kind if value is not None else "na"
    note = source_note
    if value is None:
        note = note or (str(spec["note"]) if spec.get("note") else None)
        cash_flow_value = None
        capex_value = None
        if cash_flow is not None:
            raw_cash_flow_value = cash_flow.get("Net cash from operations")
            if raw_cash_flow_value is not None:
                cash_flow_value = float(raw_cash_flow_value)
            raw_capex = cash_flow.get("Capex")
            if raw_capex is not None:
                capex_value = float(raw_capex)
        if (
            metric == "dscr"
            and cash_flow_value is not None
            and cash_flow_value < 0
        ):
            note = "n/d: operating cash flow is negative."
        elif metric in ("monthly_burn", "runway_months"):
            if cash_flow_value is None or capex_value is None:
                note = "n/d: required cash flow inputs are not reported."
            elif cash_flow_value + capex_value >= 0:
                note = "n/d: free cash flow is not negative, so cash burn or runway is not applicable."
        elif note is None:
            note = (
                "Not reported for this period."
                if expected_kind == "reported"
                else "Required inputs are not reported or the formula is undefined."
            )
    return {
        "kind": kind,
        "formula": str(spec["formula"]) if spec.get("formula") else None,
        "note": note,
    }


def require_same_period_duration(
    start_a: date, end_a: date, start_b: date, end_b: date
) -> int:
    """Reject comparisons between flows covering different numbers of months."""
    from app.services.period_detector import period_duration_months

    duration_a = period_duration_months(start_a, end_a)
    duration_b = period_duration_months(start_b, end_b)
    if duration_a != duration_b:
        raise ValueError(
            f"Cannot compare periods of different durations "
            f"({duration_a} months vs {duration_b} months)."
        )
    return duration_a


def get_catalog() -> List[Dict[str, object]]:
    """Return the ordered metric catalogue (copies, safe to mutate by callers)."""
    return [dict(spec) for spec in CATALOG]


@dataclass
class Comparison:
    """One metric compared across two periods."""

    metric: str
    current: Optional[float]
    prior: Optional[float]
    change: Optional[float]
    change_pct: Optional[float]

    def as_dict(self) -> Dict[str, Optional[float]]:
        return {
            "metric": self.metric,
            "current": self.current,
            "prior": self.prior,
            "change": self.change,
            "change_pct": self.change_pct,
        }


def pct_change(current: Optional[float], prior: Optional[float]) -> Optional[float]:
    """Percentage change vs the prior value. None when either side is missing or
    the prior value is zero (division by zero is undefined, not infinite)."""
    if current is None or prior is None or prior == 0:
        return None
    return (current - prior) / abs(prior) * 100


def compare(
    current: Dict[str, float],
    prior: Dict[str, float],
    keys: Optional[Iterable[str]] = None,
) -> Dict[str, Comparison]:
    """Compare two metric dictionaries (e.g. KPI maps for two periods).

    Keys default to every catalogue metric present in either period, in
    catalogue order, followed by any extra keys found on the inputs.
    """
    if keys is None:
        ordered = [k for k in CATALOG_BY_KEY if k in current or k in prior]
        ordered += [k for k in {**prior, **current} if k not in CATALOG_BY_KEY]
    else:
        ordered = list(keys)

    result: Dict[str, Comparison] = {}
    for key in ordered:
        cur = current.get(key)
        prev = prior.get(key)
        change = None if (cur is None or prev is None) else cur - prev
        result[key] = Comparison(key, cur, prev, change, pct_change(cur, prev))
    return result
