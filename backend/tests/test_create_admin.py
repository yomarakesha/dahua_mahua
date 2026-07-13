"""`python -m app.create_admin` — inventory-free admin bootstrap for firms
installing without the vendor. Idempotent; always must_change_password=True."""

from __future__ import annotations

import warnings

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.create_admin as ca
from app.db import Base
from app.models import Role, User
from app.security import verify_password

_engine = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_SessionMaker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def _clean_db(monkeypatch):
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    # Point the CLI's SessionLocal + schema helper at the in-memory DB, and
    # neutralise its create_all-against-sqlite step (already created above).
    monkeypatch.setattr(ca, "SessionLocal", _SessionMaker)

    async def _noop_schema():
        return None

    monkeypatch.setattr(ca, "_ensure_schema_sqlite", _noop_schema)
    yield


async def _get(username: str) -> User | None:
    async with _SessionMaker() as s:
        return (
            await s.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()


@pytest.mark.asyncio
async def test_creates_admin_with_must_change():
    result = await ca.create_or_reset_admin("ops", "s3cret-pass")
    assert result == "created"
    u = await _get("ops")
    assert u is not None and u.role == Role.admin
    assert u.must_change_password is True
    assert verify_password("s3cret-pass", u.password_hash)


@pytest.mark.asyncio
async def test_idempotent_second_run_leaves_password():
    await ca.create_or_reset_admin("ops", "first-pass")
    result = await ca.create_or_reset_admin("ops", "different-pass")
    assert result == "exists"
    u = await _get("ops")
    assert verify_password("first-pass", u.password_hash)  # untouched


@pytest.mark.asyncio
async def test_reset_password_updates_and_reforces_change():
    await ca.create_or_reset_admin("ops", "first-pass")
    # Simulate the admin having rotated + cleared the flag.
    async with _SessionMaker() as s:
        u = (await s.execute(select(User).where(User.username == "ops"))).scalar_one()
        u.must_change_password = False
        await s.commit()

    result = await ca.create_or_reset_admin("ops", "brand-new", reset_password=True)
    assert result == "password-reset"
    u = await _get("ops")
    assert verify_password("brand-new", u.password_hash)
    assert u.must_change_password is True
