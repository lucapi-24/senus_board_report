import json
from copy import deepcopy
from pathlib import Path

from app.services.validation import TOLERANCE, checks_run, validate_period

EXTRACTED = Path(__file__).resolve().parents[1] / "scripts" / "extracted"


def load(label: str) -> dict:
    with open(EXTRACTED / f"{label}_extracted.json", encoding="utf-8") as fh:
        return json.load(fh)


def rules(payload: dict):
    return {issue.rule for issue in validate_period(payload["pnl"], payload["balance_sheet"], payload["cash_flow"])}


def test_hy2026_passes_all_checks():
    # The only raw discrepancies in HY2026 are ±1 € rounding gaps, inside tolerance.
    payload = load("hy2026")
    assert validate_period(payload["pnl"], payload["balance_sheet"], payload["cash_flow"]) == []
    assert checks_run(payload["pnl"], payload["balance_sheet"], payload["cash_flow"]) >= 8


def test_hy2025_gross_profit_break_is_flagged():
    # Real case: HY2025 states gross profit of 272,331 but revenue − COGS = 271,331.
    payload = load("hy2025")
    issues = validate_period(payload["pnl"], payload["balance_sheet"], payload["cash_flow"])

    gross = [i for i in issues if i.rule == "revenue_minus_cogs_eq_gross_profit"]
    assert len(gross) == 1
    assert gross[0].expected == 271331
    assert gross[0].actual == 272331
    assert gross[0].severity == "error"


def test_hy2025_only_the_gross_profit_rule_breaks():
    assert rules(load("hy2025")) == {"revenue_minus_cogs_eq_gross_profit"}


def test_subtotal_off_by_one_is_within_tolerance():
    # HY2026 total current assets 923,339 vs components 923,338 (€1 rounding).
    assert TOLERANCE == 1.0
    assert "subtotal:Total Current Assets" not in rules(load("hy2026"))


def test_goodwill_discrepancy_breaks_the_fixed_asset_subtotal():
    # Real case: balance sheet shows goodwill 669,550 (sums exactly) while the
    # note value is 669,500 — a €50 break in Total Fixed Assets.
    payload = deepcopy(load("hy2026"))
    payload["balance_sheet"]["Goodwill"] = "669500"

    issues = validate_period(payload["pnl"], payload["balance_sheet"], payload["cash_flow"])
    broke = [i for i in issues if i.rule == "subtotal:Total Fixed Assets"]
    assert len(broke) == 1
    assert broke[0].severity == "warning"
    assert broke[0].actual == 951321
    assert broke[0].expected == 951271
