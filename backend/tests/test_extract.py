"""Tests for the PDF text parser.

The source report is a two-column layout where several labels wrap onto the
next line (e.g. "Creditors: ... after more than one\n year") and some subtotals
appear as unlabelled values. These tests pin the parsing behaviour for the
tricky cases using a representative text fixture.
"""

from decimal import Decimal

from extract_pdf import extract_from_text, compute_kpis

SAMPLE_TEXT = """
                                    Unaudited   Unaudited
Turnover 354,813 340,931
Cost of sales 64,861 69,600
Gross Profit 289,952 272,331
Distribution costs 0 0
Administrative expenses 781,975 677,908
Other operating income 8,269 0
Group operating loss 483,753 405,577
Other gains and losses 0 0
Interest payable and similar expenses 1,391 1,036
Loss before taxation 485,144 406,613
Tax expense 0 0
Loss for the period 485,144 406,613
Fixed Assets
Goodwill 669,550 0
Development Costs 239,765 0
Tangible Assets 42,006 65,363
951,321 65,363
Current Assets
Debtors 188,149 211,150
Cash and cash equivalents 735,189 72,382
923,339 283,533
Creditors: amounts falling due within one year -387,105 -90,111
Contingent consideration -850,000
Net Current Assets -313,766 193,421
Total Assets Less Current Liabilities 637,554 258,784
Creditors: amounts falling due after more than one
year -76,474 -85,468
Net (liabilities) / Assets 561,081 173,316
Called up share capital presented as equity 25,000 144
Share Premium 300,000 849,963
Retained Earnings 236,081 -676,790
Equity attributable to owners of the company
561,081 173,316
Loss for the 6 month period -485,144 -406,613
Interest payable and similar expenses 1,391 1,036
Depreciation 10,014 10,016
Net Cash used in operating activities -410,291 -450,181
Payments to acquire (in)tangible assets -8,500 0
Net (decrease) / increase in cash and cash equivalents
595,055 -352,257
"""


def setup_module(module):
    module.result = extract_from_text(SAMPLE_TEXT)
    module.comparative = extract_from_text(SAMPLE_TEXT, column=1)


def test_pnl_interest_payable_skips_suffix():
    assert result["pnl"]["Interest payable"] == Decimal("1391")


def test_pnl_loss_items_are_normalised_negative():
    assert result["pnl"]["Group operating loss"] == Decimal("-483753")
    assert result["pnl"]["Loss before taxation"] == Decimal("-485144")
    assert result["pnl"]["Loss for the period"] == Decimal("-485144")


def test_balance_sheet_stated_subtotals():
    # Prefer the subtotal printed in the statement over a re-sum of components.
    assert result["balance_sheet"]["Total Fixed Assets"] == Decimal("951321")
    assert result["balance_sheet"]["Total Current Assets"] == Decimal("923339")


def test_balance_sheet_wrapped_and_suffixed_labels():
    assert result["balance_sheet"]["Creditors > 1yr"] == Decimal("-76474")
    assert result["balance_sheet"]["Contingent consideration"] == Decimal("-850000")
    assert result["balance_sheet"]["Share Capital"] == Decimal("25000")
    assert result["balance_sheet"]["Equity"] == Decimal("561081")


def test_cash_flow_wrapped_label_and_ebitda_subtotal():
    assert result["cash_flow"]["Net change in cash"] == Decimal("595055")
    # EBITDA is not printed in the document; it is computed only in compute_kpis
    assert "EBITDA" not in result["cash_flow"]


def test_comparative_column_reads_prior_period_figures():
    """column=1 reads the prior-period column (HY2025 comparatives)."""
    assert comparative["pnl"]["Revenue"] == Decimal("340931")
    assert comparative["pnl"]["Group operating loss"] == Decimal("-405577")
    assert comparative["pnl"]["Loss for the period"] == Decimal("-406613")


def test_comparative_column_subtotals_and_cash():
    assert comparative["balance_sheet"]["Total Fixed Assets"] == Decimal("65363")
    assert comparative["balance_sheet"]["Total Current Assets"] == Decimal("283533")
    assert comparative["cash_flow"]["Depreciation"] == Decimal("10016")
    # EBITDA is not printed in the document; it is computed only in compute_kpis
    assert "EBITDA" not in comparative["cash_flow"]


def test_comparative_column_omits_values_absent_from_prior_year():
    # Contingent consideration existed only at 31-Dec-25, not at 31-Dec-24.
    assert "Contingent consideration" not in comparative["balance_sheet"]


def test_compute_kpis_from_fixture():
    kpis = compute_kpis(result["pnl"], result["balance_sheet"], result["cash_flow"])
    assert round(kpis["ebitda_margin"], 2) == -133.52
    assert round(kpis["roce"], 2) == -75.88
    assert round(kpis["runway_months"], 2) == 10.53
    assert round(kpis["current_ratio"], 2) == 0.75
    # DSCR is meaningless with negative operating cash flow.
    assert "dscr" not in kpis
