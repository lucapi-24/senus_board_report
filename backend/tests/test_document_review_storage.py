import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.api.documents import delete_document
from app.services.ingest import _store_period_proposal


def test_period_proposals_are_preserved_separately():
    first = _store_period_proposal(None, "HY2026", {"balance_sheet": ["current"]})
    stored = _store_period_proposal(first, "HY2025", {"balance_sheet": ["comparative"]})
    assert json.loads(stored) == {
        "periods": {
            "HY2026": {"balance_sheet": ["current"]},
            "HY2025": {"balance_sheet": ["comparative"]},
        },
        "llm_calls": [],
    }


def test_delete_document_awaits_database_delete():
    document = SimpleNamespace(id=7, stored_path=None)
    user = SimpleNamespace(email="admin@example.com")
    db = SimpleNamespace(
        get=AsyncMock(return_value=document),
        delete=AsyncMock(),
        add=Mock(),
        commit=AsyncMock(),
    )

    asyncio.run(delete_document(7, db, user))

    db.delete.assert_awaited_once_with(document)
    db.commit.assert_awaited_once()
