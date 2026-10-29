import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import company
from app.services.narrative import _consistency_checks


class FakeDb:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


def _doc(**overrides):
    base = dict(
        id=1,
        filename="report.pdf",
        status="published",
        period_label="HY2026",
        narrative_json=None,
        stored_path=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _call(doc, monkeypatch):
    async def fake_document_for(db, period):
        return doc

    monkeypatch.setattr(company, "_document_for", fake_document_for)
    return asyncio.run(company.get_company(period=None, db=FakeDb(), user=None))


def test_returns_stored_narrative_with_document_metadata(monkeypatch):
    doc = _doc(narrative_json=json.dumps({"company": {"x": 1}, "checks": []}))
    result = _call(doc, monkeypatch)
    assert result["company"] == {"x": 1}
    assert result["document"] == {
        "id": 1,
        "filename": "report.pdf",
        "status": "published",
        "period_label": "HY2026",
    }


def test_missing_narrative_without_pdf_is_404(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        _call(_doc(), monkeypatch)
    assert exc.value.status_code == 404


def test_no_document_is_404(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        _call(None, monkeypatch)
    assert exc.value.status_code == 404


def test_recent_deals_within_closed_pipeline_is_ok():
    checks = _consistency_checks(
        {
            "closed_pipeline_eur": {"value": 700000, "page": 1},
            "recent_deals_value_eur": {"value": 425000, "page": 2},
        }
    )
    assert [c["status"] for c in checks] == ["ok"]


def test_recent_deals_exceeding_closed_pipeline_warns():
    checks = _consistency_checks(
        {
            "closed_pipeline_eur": {"value": 400000, "page": 1},
            "recent_deals_value_eur": {"value": 425000, "page": 2},
        }
    )
    assert [c["status"] for c in checks] == ["warning"]


def test_missing_aggregates_produce_no_checks():
    assert _consistency_checks({}) == []
