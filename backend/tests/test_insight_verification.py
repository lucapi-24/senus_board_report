import asyncio
import json
import logging
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

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


def test_insight_prompt_uses_compact_kpis_and_stays_within_free_tier_budget(monkeypatch, caplog):
    source_index = {
        "pnl:Revenue": {
            "source_id": "pnl:Revenue",
            "label": "Revenue",
            "value": 354813.0,
            "origin": "reported",
            "formula": None,
            "pages": [5],
            "source_note": None,
        },
        "kpi:revenue": {
            "source_id": "kpi:revenue",
            "label": "Revenue",
            "value": 354813.0,
            "origin": "reported",
            "formula": "As reported",
            "pages": [5],
            "source_note": None,
        },
    }
    source_index.update(
        {
            f"kpi:metric_{index}": {
                "source_id": f"kpi:metric_{index}",
                "label": f"Metric {index}",
                "value": float(index),
                "origin": "derived",
                "formula": "Deterministic formula",
                "pages": [5],
                "source_note": None,
            }
            for index in range(24)
        }
    )
    pdf_pages = {
        5: "Turnover 354,813 340,931\n" + ("irrelevant full-page text " * 500)
    }
    facts = ai_insights._insight_prompt_facts(source_index)
    excerpts = ai_insights._insight_pdf_excerpts(source_index, pdf_pages)
    prompt = ai_insights._build_insight_prompt("HY2026", 6, facts, excerpts)
    estimated_prompt_tokens = ai_insights._estimate_prompt_tokens(prompt)

    class FakeCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(choices=[])

    monkeypatch.setattr(
        ai_insights,
        "client",
        SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )
    assert len(facts) == 25
    assert "pnl:Revenue" not in prompt
    assert "irrelevant full-page text" not in prompt
    assert len(excerpts[5].splitlines()) <= ai_insights.MAX_PDF_EXCERPT_LINES
    assert estimated_prompt_tokens + ai_insights.INSIGHT_MAX_TOKENS < ai_insights.INSIGHT_TOKEN_BUDGET
    with caplog.at_level(logging.INFO, logger="app.services.ai_insights"):
        asyncio.run(ai_insights._request_insights(prompt, "HY2026"))
    assert str(estimated_prompt_tokens) in caplog.text


def test_groq_413_returns_clear_limit_message(monkeypatch):
    error = Exception("request too large")
    error.status_code = 413
    error.response = SimpleNamespace(status_code=413, headers={})
    period = SimpleNamespace(
        id=88,
        label="HY2026",
        start_date=date(2025, 7, 1),
        end_date=date(2025, 12, 31),
        source_doc_id=9,
        doc_meta=None,
        doc_provenance=None,
        pnl_items=[],
        bs_items=[],
        cf_items=[],
        kpis=[],
    )

    class FakeResult:
        def __init__(self, period_value=None):
            self.period_value = period_value

        def scalar_one_or_none(self):
            return self.period_value

        def scalars(self):
            return SimpleNamespace(all=lambda: [])

    class FakeDatabase:
        def __init__(self):
            self.read_count = 0

        async def execute(self, statement):
            self.read_count += 1
            return FakeResult(period if self.read_count == 1 else None)

        async def get(self, model, primary_key):
            return SimpleNamespace(status="published", stored_path=None)

    class FakeCompletions:
        async def create(self, **kwargs):
            raise error

    monkeypatch.setattr(
        ai_insights,
        "client",
        SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )

    result = asyncio.run(ai_insights.generate_insights("HY2026", FakeDatabase()))

    assert result[0].section == "Groq request limit"
    assert "HTTP 413" in result[0].insight
    assert "retry manually" in result[0].insight.lower()


def test_groq_429_retries_once_after_retry_header(monkeypatch):
    error = Exception("rate limited")
    error.status_code = 429
    error.response = SimpleNamespace(
        status_code=429,
        headers={"retry-after": "2.5"},
    )
    successful_response = SimpleNamespace(choices=[])

    class FakeCompletions:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise error
            return successful_response

    completions = FakeCompletions()
    sleep = AsyncMock()
    monkeypatch.setattr(
        ai_insights,
        "client",
        SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    monkeypatch.setattr(ai_insights.asyncio, "sleep", sleep)

    response = asyncio.run(ai_insights._request_insights("small prompt", "HY2026"))

    assert response is successful_response
    assert completions.calls == 2
    sleep.assert_awaited_once_with(2.5)


def test_saved_insight_is_returned_without_regeneration(monkeypatch):
    saved_row = SimpleNamespace(
        section="Revenue",
        text=json.dumps({"data": "Revenue was €100.", "interpretation": ""}),
        confidence=1.0,
        sources_json=json.dumps(
            {
                "version": 1,
                "claims": [
                    {
                        "source_id": "kpi:revenue",
                        "claim_text": "€100",
                        "label": "Revenue",
                        "value": 100,
                        "origin": "reported",
                        "formula": None,
                        "pages": [5],
                        "source_note": None,
                        "verified": True,
                    }
                ],
                "evidence": [],
            }
        ),
    )
    period = SimpleNamespace(
        id=77,
        label="HY2026",
        start_date=date(2025, 7, 1),
        end_date=date(2025, 12, 31),
        source_doc_id=9,
    )

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

        async def execute(self, statement):
            self.read_count += 1
            if self.read_count == 1:
                return FakeResult(period_value=period)
            return FakeResult(rows=[saved_row])

        async def get(self, model, primary_key):
            return SimpleNamespace(status="published", stored_path=None)

    class UnexpectedCompletions:
        async def create(self, **kwargs):
            raise AssertionError("A saved insight should not trigger a Groq request")

    monkeypatch.setattr(
        ai_insights,
        "client",
        SimpleNamespace(chat=SimpleNamespace(completions=UnexpectedCompletions())),
    )

    result = asyncio.run(ai_insights.generate_insights("HY2026", FakeDatabase()))

    assert len(result) == 1
    assert result[0].data == "Revenue was €100."
