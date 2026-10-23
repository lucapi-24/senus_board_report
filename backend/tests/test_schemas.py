from types import SimpleNamespace

from app.schemas.financial import ItemUpdate, KPIResponse, LineItemResponse


def test_line_item_serializes_without_id():
    """Items use a composite key (period_id, line_item); they have no id column."""
    obj = SimpleNamespace(line_item="Revenue", value_eur=354813.0)
    item = LineItemResponse.model_validate(obj)
    assert item.line_item == "Revenue"
    assert item.value_eur == 354813.0
    assert item.page is None
    assert item.origin is None


def test_item_update_identifies_by_line_item():
    update = ItemUpdate(statement="pnl", line_item="Revenue", action="confirm")
    assert update.line_item == "Revenue"
    assert update.value is None


def test_unavailable_kpi_serializes_null_value_and_origin_note():
    item = KPIResponse(
        metric="runway_months",
        value=None,
        kind="na",
        note="Cash is not reported for this period.",
    )

    assert item.value is None
    assert item.kind == "na"
    assert item.note
