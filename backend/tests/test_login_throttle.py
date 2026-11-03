import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import auth


class _EmptyResult:
    def scalars(self):
        return self

    def first(self):
        return None


class _FakeDb:
    async def execute(self, *_args, **_kwargs):
        return _EmptyResult()


def _login(username: str):
    form = SimpleNamespace(username=username, password="wrong")
    return asyncio.run(auth.login(form=form, db=_FakeDb()))


def test_account_is_locked_after_repeated_failures():
    auth._failed_logins.clear()
    for _ in range(auth.MAX_FAILED_LOGINS):
        with pytest.raises(HTTPException) as exc:
            _login("someone@example.com")
        assert exc.value.status_code == 401
    with pytest.raises(HTTPException) as exc:
        _login("someone@example.com")
    assert exc.value.status_code == 429
    auth._failed_logins.clear()
