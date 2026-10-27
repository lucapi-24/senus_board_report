import json
from datetime import date

import pytest

from app.services.ingest import (
    PeriodReplacementRequired,
    _chosen,
    _expected_llm_coverage,
    _months_between,
    _provenance,
    build_proposal,
    resolve_unlabelled_subtotals,
    _store_period_proposal,
    require_replacement_confirmation,
)
from app.services.llm_extract import StatementLine
from app.services.reconciliation import reconcile


def _result():
    deterministic = {"pnl": {"Revenue": "340931", "Group operating loss": "-483753"}}
    llm = [
        StatementLine("pnl", "Turnover", 354813, 5),
        StatementLine("pnl", "Group operating loss", 483753, 5),
    ]
    return reconcile(deterministic, llm)


def test_chosen_keeps_deterministic_value_for_flagged_items():
    chosen = _chosen(_result())
    assert chosen["pnl"]["Revenue"] == 340931.0            # value_mismatch → deterministic
    assert chosen["pnl"]["Group operating loss"] == -483753.0  # sign kept from deterministic


def test_provenance_carries_llm_page():
    provenance = _provenance(_result())
    assert provenance["pnl"]["Revenue"] == 5


def test_proposal_exposes_both_values_and_counts():
    proposal = build_proposal(_result())
    revenue = next(i for i in proposal["pnl"] if i["line_item"] == "Revenue")
    assert revenue["status"] == "value_mismatch"
    assert revenue["regex_value"] == 340931.0
    assert revenue["llm_value"] == 354813.0
    assert proposal["summary"]["flagged"] >= 1


def test_llm_only_item_is_reviewable_but_excluded_from_kpis():
    result = reconcile(
        {},
        [StatementLine("pnl", "Other exceptional charge", 1250, 5)],
        pages=[(5, "Other exceptional charge 1,250")],
    )
    item = result["pnl"][0]

    assert item.status == "llm_only"
    assert _chosen(result)["pnl"] == {}


def test_expected_coverage_excludes_llm_only_items():
    result = reconcile(
        {"pnl": {"Revenue": "100", "Cost of sales": "40"}},
        [
            StatementLine("pnl", "Revenue", 100, 5),
            StatementLine("pnl", "Unexpected row", 3, 5),
        ],
    )

    grounded_count, missing_expected = _expected_llm_coverage(result)

    assert grounded_count == 1
    assert missing_expected["pnl"] == ["Cost of sales"]


def test_expected_coverage_excludes_unlabelled_balance_subtotals():
    result = reconcile(
        {
            "balance_sheet": {
                "Goodwill": "669550",
                "Total Fixed Assets": "951321",
                "Total Current Assets": "923339",
            }
        },
        [StatementLine("balance_sheet", "Goodwill", 669550, 5)],
    )

    grounded_count, missing_expected = _expected_llm_coverage(result)

    assert grounded_count == 1
    assert missing_expected["balance_sheet"] == []


def test_llm_call_history_is_persisted_per_period_without_overwriting():
    first = build_proposal(_result(), {"call_id": "call-1", "period_label": "HY2026"})
    stored = _store_period_proposal(None, "HY2026", first)
    second = build_proposal(_result(), {"call_id": "call-2", "period_label": "HY2026"})

    updated = json.loads(_store_period_proposal(stored, "HY2026", second))

    assert [call["call_id"] for call in updated["llm_calls"]] == ["call-1", "call-2"]
    assert updated["periods"]["HY2026"]["llm_trace"]["call_id"] == "call-2"


def test_months_between():
    assert _months_between(date(2025, 7, 1), date(2025, 12, 31)) == 6
    assert _months_between(date(2024, 7, 1), date(2025, 6, 30)) == 12
    assert _months_between(None, None) == 0


def test_existing_periods_require_explicit_replacement_confirmation():
    with pytest.raises(PeriodReplacementRequired, match="HY2026"):
        require_replacement_confirmation(["HY2026"], replace_existing=False)

    require_replacement_confirmation(["HY2026"], replace_existing=True)
    require_replacement_confirmation([], replace_existing=False)


def test_unlabelled_printed_subtotal_is_reported_when_within_tolerance():
    tables = {
        "balance_sheet": {
            "Debtors": "188149",
            "Cash and cash equivalents": "735189",
            "Total Current Assets": "923339",
        }
    }

    origins, warnings = resolve_unlabelled_subtotals(tables)

    assert tables["balance_sheet"]["Total Current Assets"] == "923339"
    assert origins["Total Current Assets"] == "reported (unlabeled)"
    assert warnings == []


def test_unlabelled_printed_subtotal_is_replaced_by_sum_and_warned():
    tables = {
        "balance_sheet": {
            "Debtors": "188149",
            "Cash and cash equivalents": "735189",
            "Total Current Assets": "923340",
        }
    }

    origins, warnings = resolve_unlabelled_subtotals(tables)

    assert float(tables["balance_sheet"]["Total Current Assets"]) == 923338
    assert origins["Total Current Assets"] == "derived"
    assert warnings == [
        {
            "line_item": "Total Current Assets",
            "printed_value": 923340.0,
            "component_sum": 923338.0,
            "message": (
                "Total Current Assets printed as 923,340, but its components sum "
                "to 923,338; the derived sum is retained."
            ),
        }
    ]
