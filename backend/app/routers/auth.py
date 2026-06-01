"""POST /auth/login, /auth/logout, /auth/me, /auth/change-password.

Stateless JWT — `logout` is a no-op on the server. The client just discards
the token. (A revocation list would mean adding a DB lookup per request and
is overkill for this app.)
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from app import rate_limit
from app.deps import CurrentUser, SessionDep
from app.models import User
from app.schemas import ChangePasswordRequest, LoginRequest, TokenResponse, UserRead
from app.security import (
    hash_password,
    issue_access_token,
    needs_rehash,
    verify_password,
)
from app.settings import get_settings

log = logging.getLogger("dss.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_to_read(user: User) -> UserRead:
    return UserRead(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        region_ids=[r.id for r in user.regions],
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    session: SessionDep,
) -> TokenResponse:
    client_ip = request.client.host if request.client else "?"
    ua = request.headers.get("user-agent", "-")

    allowed, retry_after = rate_limit.check_and_record(client_ip)
    if not allowed:
        log.warning("Login rate-limited ip=%s retry_after=%ds ua=%r", client_ip, retry_after, ua)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many login attempts. Try again in {retry_after}s",
            headers={"Retry-After": str(retry_after)},
        )

    user = (
        await session.execute(select(User).where(User.username == body.username))
    ).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        log.warning("Login FAIL ip=%s user=%r ua=%r", client_ip, body.username, ua)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    # Transparent hash upgrade if Argon2 params have changed since last login.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)

    from datetime import datetime, timezone

    user.last_login_at = datetime.now(timezone.utc)
    await session.commit()

    settings = get_settings()
    token = issue_access_token(subject=str(user.id), role=user.role.value)
    log.info("Login OK ip=%s user=%s role=%s ua=%r", client_ip, user.username, user.role.value, ua)
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_access_ttl_seconds,
        must_change_password=user.must_change_password,
    )


@router.post("/logout")
async def logout(user: CurrentUser) -> dict[str, bool]:
    log.info("Logout user=%s (stateless — client discards token)", user.username)
    return {"ok": True}


@router.get("/me", response_model=UserRead)
async def me(user: CurrentUser) -> UserRead:
    return _user_to_read(user)


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, bool]:
    client_ip = request.client.host if request.client else "?"
    if not verify_password(body.current_password, user.password_hash):
        log.warning("Password change FAIL (wrong current) ip=%s user=%s", client_ip, user.username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    await session.commit()
    log.info("Password changed ip=%s user=%s", client_ip, user.username)
    return {"ok": True}
