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
                assert line_item in provenance.get(statement, {}), (
                    f"{path.name}: {statement}/{line_item} has no source page"
                )


def test_fy2025_is_revenue_only():
    """The brief only supports FY2025 revenue (page 9); nothing else may appear."""
    data = load(EXTRACTED / "fy2025_extracted.json")
    assert set(data["pnl"]) == {"Revenue"}
    assert data["pnl"]["Revenue"] == "836991"
    assert data["balance_sheet"] == {}
    assert data["cash_flow"] == {}
    assert data["kpis"] == {}
