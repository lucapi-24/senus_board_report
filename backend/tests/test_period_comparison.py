import asyncio
import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.schemas.financial import PeriodCompareRequest
from app.services import ai_insights


def _period(label: str, period_id: int, value: int, start_year: int):
    return SimpleNamespace(
        id=period_id,
        label=label,
        start_date=date(start_year, 7, 1),
        end_date=date(start_year, 12, 31),
        source_doc_id=None,
        doc_meta=None,
        doc_provenance=None,
        pnl_items=[
            SimpleNamespace(
                line_item="Revenue",
                value_eur=Decimal(value),
                origin="reported",
                page=5,
                review_status="confirmed",
            )
        ],
        bs_items=[],
        cf_items=[],
        kpis=[],
    )


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


def test_compare_endpoint_rejects_unconfirmed_periods(monkeypatch):
    period_a = _period("HY2026", 1, 100, 2025)
    period_b = _period("HY2025", 2, 90, 2024)
    period_a.pnl_items[0].review_status = "flagged"

    class FakeResult:
        def __init__(self, period):
            self.period = period

        def scalar_one_or_none(self):
            return self.period

    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[FakeResult(period_a), FakeResult(period_b)]
        ),
        get=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(ai_insights, "client", object())
    request = PeriodCompareRequest(period_label_a="HY2026", period_label_b="HY2025")

    with pytest.raises(HTTPException) as error:
        asyncio.run(ai_insights.compare_periods(request, db))

    assert error.value.status_code == 409
    assert "published periods" in error.value.detail


def test_comparison_verifies_claims_and_persists_cited_sources(monkeypatch):
    period_a = _period("HY2026", 1, 100, 2025)
    period_b = _period("HY2025", 2, 90, 2024)
    candidate = {
        "section": "Revenue",
        "data": "Revenue was €100 in HY2026 and €90 in HY2025.",
        "interpretation": "The reported amount is higher in HY2026.",
        "claims": [
            {
                "claim_text": "€100",
                "source_id": "HY2026:kpi:revenue",
                "value": 100,
            },
            {
                "claim_text": "€90",
                "source_id": "HY2025:kpi:revenue",
                "value": 90,
            },
        ],
        "evidence": [],
    }

    class FakeResult:
        def __init__(self, period):
            self.period = period

        def scalar_one_or_none(self):
            return self.period

    class FakeDatabase:
        def __init__(self):
            self.results = [FakeResult(period_a), FakeResult(period_b)]
            self.saved = []

        async def execute(self, statement):
            return self.results.pop(0) if self.results else None

        async def get(self, model, primary_key):
            raise AssertionError("Fixture periods have no source documents")

        def add_all(self, rows):
            self.saved.extend(rows)

        async def commit(self):
            pass

    class FakeCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps({"comparison": candidate})
                        )
                    )
                ]
            )

    monkeypatch.setattr(
        ai_insights,
        "client",
        SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )
    db = FakeDatabase()
    request = PeriodCompareRequest(period_label_a="HY2026", period_label_b="HY2025")

    result = asyncio.run(ai_insights.compare_periods(request, db))

    assert result.confidence == 1
    assert result.verified_claims == result.total_claims == 2
    assert {source.period_label for source in result.sources} == {"HY2026", "HY2025"}
    assert len(db.saved) == 2
    saved = json.loads(db.saved[0].sources_json)
    assert saved["version"] == 1
    assert {claim["period_label"] for claim in saved["claims"]} == {"HY2026", "HY2025"}


def test_comparison_rejects_number_that_does_not_match_source():
    sources = {
        "HY2026:kpi:revenue": {
            "source_id": "HY2026:kpi:revenue",
            "label": "Revenue",
            "value": 100.0,
            "origin": "reported",
            "period_label": "HY2026",
            "pages": [],
            "source_note": None,
        }
    }
    candidate = {
        "section": "Revenue",
        "data": "Revenue was €99 in HY2026.",
        "interpretation": "",
        "claims": [
            {
                "claim_text": "€99",
                "source_id": "HY2026:kpi:revenue",
                "value": 99,
            }
        ],
        "evidence": [],
    }

    insight, reason = ai_insights._validate_candidate(candidate, sources, {})

    assert insight is None
    assert "does not match its source" in reason
