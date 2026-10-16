"""Deterministic checks that run after extraction.

Each rule compares a figure the report *states* against the same figure
*derived from its components*, allowing a small rounding tolerance (±€1 by
default). Failures are persisted to the ``validations`` table and surface in
the review screen.

The rules encode the identities a board reader would expect to hold:

1. Revenue − Cost of sales                    = Gross profit
2. Total assets − Total liabilities           = Equity / Net assets
3. Operating result + Depreciation            = EBITDA
4. Cash at start + Net change in cash         = Cash at end
5. Every stated subtotal = the sum of its line items (within tolerance)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

TOLERANCE = 1.0  # euros of rounding slack

# Cash-flow and balance-sheet subtotals and the line items they must sum from.
# A line item absent from the statement is treated as zero.
SUBTOTAL_RULES: Sequence[Tuple[str, Sequence[str]]] = (
    ("Total Fixed Assets", ("Goodwill", "Development Costs", "Tangible Assets")),
    ("Total Current Assets", ("Debtors", "Cash and cash equivalents")),
    ("Net Current Assets", ("Total Current Assets", "Creditors < 1yr", "Contingent consideration")),
    (
        "Total Assets Less Current Liabilities",
        ("Total Fixed Assets", "Net Current Assets"),
    ),
    ("Net Assets", ("Total Assets Less Current Liabilities", "Creditors > 1yr")),
)


@dataclass
class ValidationIssue:
    rule: str
    severity: str          # error | warning
    message: str
    expected: float
    actual: float

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _num(source: Dict[str, Any], key: str) -> Optional[float]:
    """Coerce a stored figure (string/decimal/number) to float, or None."""
    if key not in source:
        return None
    raw = source[key]
    if raw is None or raw == "":
        return None
    try:
        return float(Decimal(str(raw)))
    except (ValueError, TypeError):
        return None


def _pick(source: Dict[str, Any], *keys: str) -> Optional[float]:
    """First present-and-numeric value among ``keys``."""
    for key in keys:
        value = _num(source, key)
        if value is not None:
            return value
    return None


def _checks(
    pnl: Dict[str, Any], bs: Dict[str, Any], cf: Dict[str, Any]
) -> List[Tuple[str, str, float, float]]:
    """Return every check as (rule, severity, expected, actual).

    Checks whose required inputs are missing are skipped (nothing to assert).
    """
    out: List[Tuple[str, str, float, float]] = []

    # 1. Revenue − Cost of sales = Gross profit
    revenue = _num(pnl, "Revenue") if "Revenue" in pnl else _num(pnl, "Turnover")
    cogs = _num(pnl, "Cost of sales")
    gross_profit = _num(pnl, "Gross Profit")
    if revenue is not None and cogs is not None and gross_profit is not None:
        out.append(("revenue_minus_cogs_eq_gross_profit", "error", revenue - cogs, gross_profit))

    # 2. Total assets − Total liabilities = Equity
    total_fixed = _num(bs, "Total Fixed Assets")
    total_current = _num(bs, "Total Current Assets")
    equity = _pick(bs, "Equity", "Net Assets")
    if total_fixed is not None and total_current is not None and equity is not None:
        liabilities = sum(
            v for v in (_num(bs, k) for k in ("Creditors < 1yr", "Creditors > 1yr", "Contingent consideration"))
            if v is not None
        )
        expected = total_fixed + total_current + liabilities  # liabilities stored negative
        out.append(("assets_minus_liabilities_eq_equity", "error", expected, equity))

    # 3. Operating result + Depreciation = EBITDA
    operating = _pick(pnl, "Group operating loss", "Operating loss")
    depreciation = _num(cf, "Depreciation")
    ebitda = _num(cf, "EBITDA")
    if operating is not None and depreciation is not None and ebitda is not None:
        out.append(("operating_result_plus_depreciation_eq_ebitda", "error", operating + depreciation, ebitda))

    # 4. Cash at start + Net change in cash = Cash at end
    cash_start = _num(cf, "Cash at start")
    net_change = _num(cf, "Net change in cash")
    cash_end = _num(cf, "Cash at end")
    if cash_start is not None and net_change is not None and cash_end is not None:
        out.append(("cash_open_plus_net_change_eq_cash_close", "error", cash_start + net_change, cash_end))

    # 5. Subtotals sum their components (rounding tolerance applies)
    for name, components in SUBTOTAL_RULES:
        stated = _num(bs, name)
        if stated is None:
            continue
        component_sum = sum(v for v in (_num(bs, k) for k in components) if v is not None)
        out.append((f"subtotal:{name}", "warning", component_sum, stated))

    return out


def validate_period(
    pnl: Dict[str, Any],
    bs: Dict[str, Any],
    cf: Dict[str, Any],
    tolerance: float = TOLERANCE,
) -> List[ValidationIssue]:
    """Run every applicable rule and return the failures beyond tolerance."""
    issues: List[ValidationIssue] = []
    for rule, severity, expected, actual in _checks(pnl, bs, cf):
        delta = expected - actual
        if abs(delta) <= tolerance:
            continue
        issues.append(
            ValidationIssue(
                rule=rule,
                severity=severity,
                message=(
                    f"{rule}: expected €{expected:,.0f} but found €{actual:,.0f} "
                    f"(difference €{delta:+,.0f})."
                ),
                expected=round(expected, 2),
                actual=round(actual, 2),
            )
        )
    return issues


def checks_run(pnl: Dict[str, Any], bs: Dict[str, Any], cf: Dict[str, Any]) -> int:
    """Number of rules that had enough data to run (for reporting)."""
    return len(_checks(pnl, bs, cf))
