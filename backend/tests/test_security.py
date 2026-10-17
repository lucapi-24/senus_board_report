import asyncio

import pytest
from fastapi import HTTPException

from app.core.security import (
    create_access_token,
    decode_token,
    hash_password,
    require_admin,
    verify_password,
)
from app.models.financial import User


def test_password_hash_roundtrip():
    hashed = hash_password("senus123")
    assert hashed != "senus123"
    assert verify_password("senus123", hashed) is True
    assert verify_password("wrong", hashed) is False
    assert verify_password("senus123", None) is False


def test_access_token_roundtrip():
    token = create_access_token("board@senus.com", "admin")
    payload = decode_token(token)
    assert payload["sub"] == "board@senus.com"
    assert payload["role"] == "admin"
    assert "exp" in payload


def test_require_admin_allows_admin():
    user = User(email="board@senus.com", role="admin")
    assert asyncio.run(require_admin(user)) is user


def test_require_admin_rejects_viewer():
    user = User(email="viewer@senus.com", role="viewer")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_admin(user))
    assert exc.value.status_code == 403
