"""Forced first-login password rotation (portability foundation).

The bootstrap admin is created with must_change_password=True; while that flag is
set, get_current_user 403s every request EXCEPT the recovery set (auth/me,
auth/change-password, auth/logout, branding). The change-password endpoint clears
the flag. These are the server-side backstop for the frontend first-run wizard.
"""

from __future__ import annotations

import uuid
import warnings

import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from app.db import Base, get_session
from app.deps import CurrentUser
from app.models import Role, User
from app.routers import auth as auth_router
from app.routers import branding as branding_router
from app.security import hash_password, issue_access_token

_engine = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_SessionMaker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

_ADMIN_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_CURRENT_PW = "bootstrap-pass"


@pytest_asyncio.fixture
async def seeded_must_change():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with _SessionMaker() as s:
        s.add(
            User(
                id=_ADMIN_ID,
                username="admin",
                password_hash=hash_password(_CURRENT_PW),
                role=Role.admin,
                must_change_password=True,
            )
        )
        await s.commit()
    yield


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/v1")
    app.include_router(branding_router.router, prefix="/api/v1")

    @app.get("/api/v1/protected")
    async def protected(user: CurrentUser):
        return {"user": user.username}

    async def _override_session():
        async with _SessionMaker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    return app


def _token() -> str:
    return issue_access_token(subject=str(_ADMIN_ID), role="admin")


@pytest.mark.usefixtures("seeded_must_change")
def test_must_change_user_is_403_on_normal_route():
    client = TestClient(_make_app())
    r = client.get("/api/v1/protected", headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 403
    assert r.json()["detail"] == "password_change_required"


@pytest.mark.usefixtures("seeded_must_change")
def test_recovery_routes_are_allowed_while_must_change():
    client = TestClient(_make_app())
    hdr = {"Authorization": f"Bearer {_token()}"}
    # /auth/me must work so the frontend can read the flag and drive the wizard.
    assert client.get("/api/v1/auth/me", headers=hdr).status_code == 200
    # /auth/logout must work so the user can bail out.
    assert client.post("/api/v1/auth/logout", headers=hdr).status_code == 200
    # /branding is public anyway, but must never be blocked by the flag.
    assert client.get("/api/v1/branding", headers=hdr).status_code == 200


@pytest.mark.asyncio
async def test_bootstrap_admin_created_with_must_change(monkeypatch):
    """Both bootstrap creators (main lifespan + seed) must force the rotation."""
    import app.main as main_mod
    import app.seed as seed_mod

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # main._ensure_bootstrap_admin uses the module-level SessionLocal.
    monkeypatch.setattr(main_mod, "SessionLocal", _SessionMaker)
    await main_mod._ensure_bootstrap_admin()
    async with _SessionMaker() as s:
        u = (await s.execute(select(User).where(User.role == Role.admin))).scalar_one()
        assert u.must_change_password is True

    # seed._ensure_bootstrap_admin(session) is a no-op now (admin exists), so
    # verify it independently on a fresh DB.
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with _SessionMaker() as s:
        await seed_mod._ensure_bootstrap_admin(s)
        await s.commit()
    async with _SessionMaker() as s:
        u = (await s.execute(select(User).where(User.role == Role.admin))).scalar_one()
        assert u.must_change_password is True


@pytest.mark.usefixtures("seeded_must_change")
def test_change_password_clears_flag_and_unlocks_routes():
    client = TestClient(_make_app())
    hdr = {"Authorization": f"Bearer {_token()}"}

    # change-password is allowed even while the flag is set.
    r = client.post(
        "/api/v1/auth/change-password",
        headers=hdr,
        json={"current_password": _CURRENT_PW, "new_password": "a-new-strong-pass"},
    )
    assert r.status_code == 200

    # /auth/me now reports the flag cleared.
    me = client.get("/api/v1/auth/me", headers=hdr).json()
    assert me["must_change_password"] is False

    # And the previously-blocked route is now reachable.
    assert client.get("/api/v1/protected", headers=hdr).status_code == 200
