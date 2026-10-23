from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.financial import _period_kpi_responses
from app.services.ai_insights import _format_kpi
from app.services.metrics import kpi_metadata, require_same_period_duration


def test_kpi_metadata_reports_origin_and_formula():
    reported = kpi_metadata("revenue", 836991)
    derived = kpi_metadata("gross_margin", 81.7)
    unavailable = kpi_metadata("revenue_mom", None)
    negative_dscr = kpi_metadata(
        "dscr", None, {"Net cash from operations": -410291}
    )

    assert reported["kind"] == "reported"
    assert derived["kind"] == "derived"
    assert derived["formula"] == "Gross profit / Revenue × 100"
    assert unavailable["kind"] == "na"
    assert "Monthly revenue" in unavailable["note"]
    assert negative_dscr["kind"] == "na"
    assert "operating cash flow is negative" in negative_dscr["note"]
    assert _format_kpi(None, 1, "%") == "n/d"
    assert _format_kpi(0, 1, "%") == "0.0%"


def test_comparison_rejects_half_year_against_full_year():
    with pytest.raises(ValueError, match="different durations \\(6 months vs 12 months\\)"):
        require_same_period_duration(
            date(2025, 7, 1),
            date(2025, 12, 31),
            date(2024, 7, 1),
            date(2025, 6, 30),
        )


def test_comparison_accepts_equal_durations():
    assert require_same_period_duration(
        date(2025, 7, 1),
        date(2025, 12, 31),
        date(2024, 7, 1),
        date(2024, 12, 31),
    ) == 6


def test_period_kpis_keep_reported_sources_and_missing_values_null():
    period = SimpleNamespace(
        start_date=date(2024, 7, 1),
        end_date=date(2025, 6, 30),
        pnl_items=[
            SimpleNamespace(line_item="Revenue", value_eur=Decimal("836991"))
        ],
        bs_items=[],
        cf_items=[],
        kpis=[
            SimpleNamespace(
                metric="cash",
                value=Decimal("140135"),
                note="HY2026 cash flow opening balance",
            ),
            SimpleNamespace(
                metric="customer_accounts",
                value=Decimal("138"),
                note="Assignment brief",
            ),
        ],
    )

    kpis = {item.metric: item for item in _period_kpi_responses(period)}

    assert kpis["revenue"].value == 836991
    assert kpis["revenue"].kind == "reported"
    assert kpis["cash"].value == 140135
    assert kpis["cash"].note == "HY2026 cash flow opening balance"
    assert kpis["customer_accounts"].value == 138
    assert kpis["customer_accounts"].note == "Assignment brief"
    assert kpis["gross_margin"].value is None
    assert kpis["gross_margin"].kind == "na"
    assert kpis["runway_months"].value is None
