"""Golden-value tests for compute_kpis on the HY2026 (6m to 31-Dec-2025) dataset."""

from decimal import Decimal

import pytest

from extract_pdf import compute_kpis

PNL = {
    "Revenue": Decimal("354813"),
    "Cost of sales": Decimal("64861"),
    "Gross Profit": Decimal("289952"),
    "Distribution costs": Decimal("0"),
    "Administrative expenses": Decimal("781975"),
    "Other operating income": Decimal("8269"),
    "Group operating loss": Decimal("-483753"),
    "Other gains and losses": Decimal("0"),
    "Interest payable": Decimal("1391"),
    "Loss before taxation": Decimal("-485144"),
    "Tax expense": Decimal("0"),
    "Loss for the period": Decimal("-485144"),
}

BS = {
    "Goodwill": Decimal("669550"),
    "Development Costs": Decimal("239765"),
    "Tangible Assets": Decimal("42006"),
    "Debtors": Decimal("188149"),
    "Cash and cash equivalents": Decimal("735189"),
    "Creditors < 1yr": Decimal("-387105"),
    "Contingent consideration": Decimal("-850000"),
    "Net Current Assets": Decimal("-313766"),
    "Total Assets Less Current Liabilities": Decimal("637554"),
    "Creditors > 1yr": Decimal("-76474"),
    "Net Assets": Decimal("561081"),
    "Share Capital": Decimal("25000"),
    "Share Premium": Decimal("300000"),
    "Retained Earnings": Decimal("236081"),
    "Equity": Decimal("561081"),
    "Total Fixed Assets": Decimal("951321"),
    "Total Current Assets": Decimal("923339"),
}

CF = {
    "Loss for the period": Decimal("-485144"),
    "Interest payable": Decimal("1391"),
    "Depreciation": Decimal("10014"),
    "Net cash from operations": Decimal("-410291"),
    "Capex": Decimal("-8500"),
    "Loan repayments": Decimal("-124837"),
    "EBITDA": Decimal("-473739"),
}


@pytest.fixture(scope="module")
def kpis():
    return compute_kpis(PNL, BS, CF)


@pytest.mark.parametrize(
    "metric,expected",
    [
        ("gross_margin", 81.7197),
        ("operating_margin", -136.3403),
        ("ebitda_margin", -133.5179),
        ("net_margin", -136.7323),
        ("admin_expense_ratio", 220.3907),
        ("fcf", -418791.0),
        ("monthly_burn", 69798.5),
        ("runway_months", 10.5330),
        ("net_cash", 658715.0),
        ("current_ratio", 0.7464),
        ("current_ratio_ex_contingent", 2.3852),
        ("total_assets", 1874660.0),
        ("equity_ratio", 29.9297),
        ("intangibles_ratio", 48.5056),
        ("debt_to_equity", 13.6298),
        ("interest_cover", -347.7735),
        ("roce", -75.8764),
        ("roce_annualised", -151.7528),
        ("roce_ex_contingent", -32.5200),
        ("roe", -86.4659),
    ],
)
def test_kpi_value(kpis, metric, expected):
    assert kpis[metric] == pytest.approx(expected, abs=1e-3)


def test_dscr_is_omitted_when_cfads_negative(kpis):
    assert "dscr" not in kpis
