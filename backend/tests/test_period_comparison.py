import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.schemas.financial import PeriodCompareRequest
from app.services import ai_insights


def test_compare_endpoint_rejects_different_period_durations(monkeypatch):
    period_a = SimpleNamespace(
        start_date=date(2025, 7, 1),
        end_date=date(2025, 12, 31),
    )
    period_b = SimpleNamespace(
        start_date=date(2024, 7, 1),
        end_date=date(2025, 6, 30),
    )
    results = [
        SimpleNamespace(scalar_one_or_none=lambda: period_a),
        SimpleNamespace(scalar_one_or_none=lambda: period_b),
    ]
    db = SimpleNamespace(execute=AsyncMock(side_effect=results))
    monkeypatch.setattr(ai_insights, "client", object())
    request = PeriodCompareRequest(period_label_a="HY2026", period_label_b="FY2025")

    with pytest.raises(HTTPException) as error:
        asyncio.run(ai_insights.compare_periods(request, db))

    assert error.value.status_code == 422
    assert "different durations" in error.value.detail
