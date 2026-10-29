import asyncio
import json

import pytest
from fastapi import HTTPException

from app.api import company
from app.services import company_ai


def _context(monkeypatch):
    async def fake_ctx(db, doc):
        return "CTX"

    async def fake_doc(db, period):
        return object.__new__(type("D", (), {"id": 1}))

    monkeypatch.setattr(company, "_ai_context", fake_ctx)
    monkeypatch.setattr(company, "_require_doc", fake_doc)


def test_build_context_drops_quotes_and_pages():
    ctx = json.loads(
        company_ai.build_context(
            {"strategy": {"name": "S", "quote": "q", "page": 3}, "checks": [{"message": "A within B"}], "document": {}},
            {"income_statement": {"revenue": 1.0}},
        )
    )
    assert ctx["narrative"] == {"strategy": {"name": "S"}}
    assert ctx["consistency_notes"] == ["A within B"]
    assert ctx["financial_statements_eur"]["income_statement"]["revenue"] == 1.0


def test_insights_limited_to_three(monkeypatch):
    async def fake(messages, **kw):
        items = [{"title": f"t{i}", "text": f"x{i}"} for i in range(5)]
        return json.dumps({"insights": items})

    monkeypatch.setattr(company_ai, "_complete", fake)
    company_ai._cache.clear()
    out = asyncio.run(company_ai.generate_insights("k", "ctx", True))
    assert len(out) == 3


def test_insights_unreadable_answer_is_unavailable(monkeypatch):
    async def fake(messages, **kw):
        return "not json"

    monkeypatch.setattr(company_ai, "_complete", fake)
    with pytest.raises(company_ai.AIUnavailable):
        asyncio.run(company_ai.generate_insights("k2", "ctx", True))


def test_chat_trims_history_and_includes_context(monkeypatch):
    seen = {}

    async def fake(messages, **kw):
        seen["messages"] = messages
        return "ok"

    monkeypatch.setattr(company_ai, "_complete", fake)
    history = [{"role": "user", "content": f"m{i}"} for i in range(20)]
    assert asyncio.run(company_ai.chat("CTX", "hola", history)) == "ok"
    assert "CTX" in seen["messages"][0]["content"]
    assert len(seen["messages"]) == 1 + company_ai.MAX_HISTORY + 1


def test_missing_key_is_unavailable(monkeypatch):
    monkeypatch.setattr(company_ai.settings, "GROQ_API_KEY", None)
    with pytest.raises(company_ai.AIUnavailable):
        asyncio.run(company_ai._complete([{"role": "user", "content": "x"}]))


def test_chat_endpoint_maps_unavailable_to_503(monkeypatch):
    _context(monkeypatch)

    async def boom(context, message, history):
        raise company_ai.AIUnavailable("down")

    monkeypatch.setattr(company_ai, "chat", boom)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(company.company_chat(company.CompanyChatRequest(message="hi"), db=None, user=None))
    assert exc.value.status_code == 503
