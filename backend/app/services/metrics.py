"""Metrics engine: a single catalogue of the derived KPIs plus pure helpers to
compare a metric across two periods (period-over-period deltas).

The catalogue is descriptive metadata only — the actual values are produced by
``compute_kpis`` in ``scripts/extract_pdf.py`` (the single source of truth,
shared by the extractor and the ingestion pipeline). This module keeps the
human-facing definition of every metric in one place so the API, the dashboard
and the documentation can agree on labels, units and formulas.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

# unit: '%' | 'EUR' | 'x' | 'months'   direction: 'up' | 'down' | 'neutral'
CATALOG: List[Dict[str, object]] = [
    # --- Profitability ---
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
        "formula": "abs(Free cash flow) / 6",
        "note": "Assumes a six-month interim period (divisor is hard-coded).",
    },
    {
        "key": "runway_months",
        "label": "Cash runway",
        "unit": "months",
        "category": "Cash & burn",
        "direction": "up",
        "formula": "Cash / Monthly burn",
        "note": "Only meaningful while the business is burning cash.",
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
        "formula": "ROCE × 2 (interim period annualised)",
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
