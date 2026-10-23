import asyncio
import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.ai_insights import _validate_candidate
from app.services import ai_insights


SOURCES = {
    "kpi:revenue": {
        "source_id": "kpi:revenue",
        "label": "Revenue",
        "value": 100.0,
        "origin": "reported",
        "formula": "As reported in the income statement",
        "pages": [5],
        "source_note": None,
    }
}
PDF_PAGES = {5: "Revenue was €100. Administrative expenses were reported."}


def test_insight_with_incorrect_numeric_claim_is_discarded():
    candidate = {
        "section": "Revenue",
        "data": "Revenue was €99.",
        "interpretation": "",
        "claims": [
            {"claim_text": "€99", "source_id": "kpi:revenue", "value": 99}
        ],
        "evidence": [],
    }

    insight, reason = _validate_candidate(candidate, SOURCES, PDF_PAGES)

    assert insight is None
    assert "does not match its source" in reason


def test_cause_not_supported_by_pdf_is_discarded():
    candidate = {
        "section": "Revenue",
        "data": "Revenue was €100.",
        "interpretation": "New contracts drove revenue growth.",
        "claims": [
            {"claim_text": "€100", "source_id": "kpi:revenue", "value": 100}
        ],
        "evidence": [{"page": 5, "quote": "Revenue was €100."}],
    }

    insight, reason = _validate_candidate(candidate, SOURCES, PDF_PAGES)

    assert insight is None
    assert "causal statement" in reason


def test_valid_numeric_claim_is_returned_with_verified_source():
    candidate = {
        "section": "Revenue",
        "data": "Revenue was €100.",
        "interpretation": "This is a reported figure.",
        "claims": [
            {"claim_text": "€100", "source_id": "kpi:revenue", "value": 100}
        ],
        "evidence": [],
    }

    insight, reason = _validate_candidate(candidate, SOURCES, PDF_PAGES)

    assert reason == ""
    assert insight is not None
    assert insight.confidence == 1
    assert insight.verified_claims == insight.total_claims == 1
    assert insight.sources[0].source_id == "kpi:revenue"
    assert insight.sources[0].verified is True


def test_validated_insight_is_persisted_with_sources(monkeypatch):
    period = SimpleNamespace(
        id=42,
        label="HY2026",
        start_date=date(2025, 7, 1),
        end_date=date(2025, 12, 31),
        source_doc_id=9,
        doc_meta=None,
        doc_provenance=None,
        pnl_items=[
            SimpleNamespace(
                line_item="Revenue",
                value_eur=Decimal("100"),
                origin="reported",
                page=5,
            )
        ],
        bs_items=[],
        cf_items=[],
        kpis=[],
    )
    candidate = {
        "section": "Revenue",
        "data": "Revenue was €100.",
        "interpretation": "",
        "claims": [
            {"claim_text": "€100", "source_id": "kpi:revenue", "value": 100}
        ],
        "evidence": [],
    }

    class FakeResult:
        def __init__(self, period_value=None, rows=None):
            self.period_value = period_value
            self.rows = rows or []

        def scalar_one_or_none(self):
            return self.period_value

        def scalars(self):
            return SimpleNamespace(all=lambda: self.rows)

    class FakeDatabase:
        def __init__(self):
            self.read_count = 0
            self.saved = []

        async def execute(self, statement):
            self.read_count += 1
            if self.read_count == 1:
                return FakeResult(period_value=period)
            return FakeResult(rows=[])

        async def get(self, model, primary_key):
            assert primary_key == 9
            return SimpleNamespace(status="published", stored_path=None)

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
                            content=json.dumps({"insights": [candidate]})
                        )
                    )
                ]
            )

    monkeypatch.setattr(
        ai_insights,
        "client",
        SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )
    database = FakeDatabase()

    result = asyncio.run(ai_insights.generate_insights("HY2026", database))

    assert len(result) == 1
    assert result[0].sources[0].label == "Revenue"
    assert len(database.saved) == 1
    saved_sources = json.loads(database.saved[0].sources_json)
    assert saved_sources["version"] == 1
    assert saved_sources["claims"][0]["source_id"] == "kpi:revenue"
    assert saved_sources["claims"][0]["verified"] is True
