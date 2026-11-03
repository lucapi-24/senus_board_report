import time

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import create_access_token, get_current_user, verify_password
from app.models.financial import User
from app.schemas.auth import Token, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])

# In-memory throttle (per account, per process): enough for a demo, not for a
# multi-instance deployment.
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 300
_failed_logins: dict[str, list[float]] = {}


def _recent_failures(username: str) -> list[float]:
    cutoff = time.monotonic() - LOCKOUT_SECONDS
    recent = [t for t in _failed_logins.get(username, []) if t > cutoff]
    _failed_logins[username] = recent
    return recent


@router.post("/login", response_model=Token)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    if len(_recent_failures(form.username)) >= MAX_FAILED_LOGINS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again in a few minutes.",
        )
    user = (
        await db.execute(select(User).where(User.email == form.username))
    ).scalars().first()
    if user is None or not verify_password(form.password, user.hashed_password):
        _failed_logins.setdefault(form.username, []).append(time.monotonic())
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    _failed_logins.pop(form.username, None)
    token = create_access_token(user.email, user.role)
    return Token(access_token=token, role=user.role, email=user.email)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user
