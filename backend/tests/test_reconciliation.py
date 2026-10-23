from app.services.llm_extract import StatementLine
from app.services.reconciliation import (
    canonical_label,
    reconcile,
    review_status_for,
)


def _by_label(result, statement):
    return {item.line_item: item for item in result[statement]}


def test_matching_is_alias_and_tolerance_aware():
    det = {"pnl": {"Revenue": "354813", "Cost of sales": "64861"}}
    llm = [
        StatementLine("pnl", "Turnover", 354813, 5),
        StatementLine("pnl", "Cost of sales", 64861, 5),
    ]
    pages = [(5, "Turnover 354,813; Cost of sales 64,861")]
    items = _by_label(reconcile(det, llm, pages=pages), "pnl")
    assert items["Revenue"].status == "matched"
    assert items["Revenue"].confidence == 1.0
    assert items["Revenue"].confidence_factors == {
        "extractors_agree": True,
        "value_found_on_source_page": True,
        "accounting_identities_consistent": None,
    }
    assert items["Revenue"].source == "both"
    assert items["Cost of sales"].status == "matched"


def test_confidence_penalises_broken_accounting_identity():
    deterministic = {
        "pnl": {
            "Revenue": "100",
            "Cost of sales": "40",
            "Gross Profit": "70",
        }
    }
    llm = [
        StatementLine("pnl", label, value, 5)
        for label, value in (
            ("Revenue", 100),
            ("Cost of sales", 40),
            ("Gross Profit", 70),
        )
    ]
    pages = [(5, "Revenue 100; Cost of sales 40; Gross Profit 70")]

    revenue = _by_label(reconcile(deterministic, llm, pages=pages), "pnl")["Revenue"]

    assert revenue.confidence_factors["extractors_agree"] is True
    assert revenue.confidence_factors["value_found_on_source_page"] is True
    assert revenue.confidence_factors["accounting_identities_consistent"] is False
    assert revenue.confidence == 2 / 3


def test_confidence_credits_a_balanced_accounting_identity():
    deterministic = {
        "pnl": {"Revenue": "100", "Cost of sales": "40", "Gross Profit": "60"}
    }
    llm = [
        StatementLine("pnl", label, value, 5)
        for label, value in (
            ("Revenue", 100),
            ("Cost of sales", 40),
            ("Gross Profit", 60),
        )
    ]
    pages = [(5, "Revenue 100; Cost of sales 40; Gross Profit 60")]

    revenue = _by_label(reconcile(deterministic, llm, pages=pages), "pnl")["Revenue"]

    assert revenue.confidence_factors["accounting_identities_consistent"] is True
    assert revenue.confidence == 1.0


def test_single_extractor_and_missing_identity_get_equal_applicable_score():
    deterministic = {
        "pnl": {"Administrative expenses": "10"},
        "provenance": {"pnl": {"Administrative expenses": 5}},
    }
    pages = [(5, "Administrative expenses 10")]

    item = _by_label(reconcile(deterministic, [], pages=pages), "pnl")[
        "Administrative expenses"
    ]

    assert item.confidence_factors == {
        "extractors_agree": False,
        "value_found_on_source_page": True,
        "accounting_identities_consistent": None,
    }
    assert item.confidence == 0.5


def test_page_text_check_does_not_accept_a_number_substring():
    deterministic = {
        "pnl": {"Administrative expenses": "10"},
        "provenance": {"pnl": {"Administrative expenses": 5}},
    }
    pages = [(5, "Revenue 100")]

    item = _by_label(reconcile(deterministic, [], pages=pages), "pnl")[
        "Administrative expenses"
    ]

    assert item.confidence_factors["value_found_on_source_page"] is False
    assert item.confidence == 0


def test_value_and_sign_mismatches_expose_both_values():
    det = {"pnl": {"Revenue": "340931", "Interest payable": "1391"}}
    llm = [
        StatementLine("pnl", "Turnover", 354813, 5),
        StatementLine("pnl", "Interest payable", -1391, 5),
    ]
    items = _by_label(reconcile(det, llm), "pnl")
    revenue = items["Revenue"]
    assert revenue.status == "value_mismatch"
    assert revenue.regex_value == 340931.0
    assert revenue.llm_value == 354813.0
    assert items["Interest payable"].status == "sign_mismatch"


def test_single_source_labels_are_flagged():
    det = {"cash_flow": {"EBITDA": "-473739"}}
    llm = [StatementLine("cash_flow", "Net cash used in operating activities", -410291, 6)]
    result = reconcile(det, llm)
    items = _by_label(result, "cash_flow")
    assert items["EBITDA"].status == "regex_only"
    assert items["Net cash from operations"].status == "llm_only"
    assert result["summary"]["flagged"] == 2


def test_creditors_direction_is_not_confused():
    det = {"balance_sheet": {"Creditors < 1yr": "-387105", "Creditors > 1yr": "-76474"}}
    llm = [
        StatementLine("balance_sheet", "Creditors: amounts falling due within one year", -387105, 5),
        StatementLine(
            "balance_sheet", "Creditors: amounts falling due after more than one year", -76474, 6
        ),
    ]
    items = _by_label(reconcile(det, llm), "balance_sheet")
    assert items["Creditors < 1yr"].status == "matched"
    assert items["Creditors > 1yr"].status == "matched"
    assert len(items) == 2


def test_canonical_label_and_review_status():
    assert canonical_label("pnl", "Turnover") == "Revenue"
    assert canonical_label("balance_sheet", "Net (liabilities) / Assets") == "Net Assets"
    assert review_status_for("matched") == "reported"
    assert review_status_for("value_mismatch") == "flagged"
