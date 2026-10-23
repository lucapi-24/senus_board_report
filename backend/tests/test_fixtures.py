import json
from pathlib import Path

EXTRACTED = Path(__file__).resolve().parents[1] / "scripts" / "extracted"

# Line items the pipeline derives rather than reads from a printed line.
DERIVED_LINE_ITEMS = {"EBITDA"}

STATEMENTS = ("pnl", "balance_sheet", "cash_flow")


def fixtures():
    return sorted(EXTRACTED.glob("*_extracted.json"))


def load(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_every_line_item_is_traceable_or_derived():
    """No committed figure may exist without a source page (or be a known derivation)."""
    for path in fixtures():
        data = load(path)
        provenance = data.get("provenance", {})
        for statement in STATEMENTS:
            for line_item in data.get(statement, {}):
                if line_item in DERIVED_LINE_ITEMS:
                    continue
                field_source = data.get("field_sources", {}).get(statement, {})
                assert line_item in provenance.get(statement, {}) or field_source.get(line_item), (
                    f"{path.name}: {statement}/{line_item} has no source page or identified source"
                )


def test_fy2025_contains_only_supported_partial_data():
    """FY2025 facts stay attributed to the brief and HY2026 cash flow, not a fake PDF."""
    data = load(EXTRACTED / "fy2025_extracted.json")
    assert set(data["pnl"]) == {"Revenue"}
    assert data["pnl"]["Revenue"] == "836991"
    assert data["balance_sheet"] == {}
    assert data["cash_flow"] == {}
    assert data["kpis"] == {"cash": "140135", "customer_accounts": "138"}
    assert data["source_file"] is None
    assert data["kpi_sources"]["cash"] == "HY2026 cash flow opening balance"
    assert data["kpi_sources"]["customer_accounts"] == "Assignment brief"
