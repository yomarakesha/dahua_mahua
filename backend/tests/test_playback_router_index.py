"""TDD tests for GET /api/v1/playback/{nvr_id}/{channel}/index.

Pattern: minimal FastAPI test app (no lifespan), dependency overrides for auth
and session, monkeypatch for find_clips, in-memory SQLite for the NVR row.
"""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# Suppress starlette/httpx deprecation warning that fires on TestClient import.
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from app.crypto import encrypt_password
from app.db import Base, get_session
from app.deps import get_current_user
from app.models import Nvr, Role, User, Vendor
from app.services.playback.index_parser import Clip
import app.routers.playback as playback_module
from app.routers.playback import router as playback_router

# ── In-memory test DB ─────────────────────────────────────────────────────────

_engine = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_SessionMaker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

NVR_ID = "nvr-test"
NVR_PW = "secret123"
TZ_OFFSET = 60  # UTC+1


# ── App factory ───────────────────────────────────────────────────────────────

def _make_app() -> FastAPI:
    """Minimal test app — no lifespan, only the playback router."""
    app = FastAPI()
    app.include_router(playback_router, prefix="/api/v1")

    async def _override_auth() -> User:
        u = User(username="testadmin", password_hash="x", role=Role.admin)
        u.regions = []
        u.cameras = []
        return u

    async def _override_session():
        async with _SessionMaker() as s:
            yield s

    app.dependency_overrides[get_current_user] = _override_auth
    app.dependency_overrides[get_session] = _override_session
    return app


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="module", autouse=True)
async def _setup_db():
    """Create schema and seed one NVR row for the entire module."""
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _SessionMaker() as s:
        s.add(Nvr(
            id=NVR_ID,
            label="Test NVR",
            ip="192.168.1.100",
            port=554,  # realistic RTSP port — NOT the HTTP CGI port
            rtsp_username="admin",
            rtsp_password_encrypted=encrypt_password(NVR_PW),
            vendor=Vendor.dahua,
        ))
        await s.commit()
    yield
    await _engine.dispose()


@pytest.fixture(autouse=True)
def _clear_cache():
    """Wipe the in-process recording-index cache around every test."""
    playback_module._cache.clear()
    yield
    playback_module._cache.clear()


@pytest.fixture
def client():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app()) as c:
            yield c


# ── Shared test data ──────────────────────────────────────────────────────────

TWO_CLIPS = [
    Clip(
        start=datetime(2026, 1, 1, 8, 0, 0),
        end=datetime(2026, 1, 1, 12, 0, 0),
        type="Timing",
        stream="Main",
    ),
    Clip(
        start=datetime(2026, 1, 1, 14, 0, 0),
        end=datetime(2026, 1, 1, 20, 0, 0),
        type="Event",
        stream="Main",
    ),
]


def _epoch(dt_naive: datetime, tz_offset_minutes: int) -> int:
    """Expected UTC epoch: subtract the NVR's UTC offset to get UTC."""
    utc = dt_naive - timedelta(minutes=tz_offset_minutes)
    return int(utc.replace(tzinfo=timezone.utc).timestamp())


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_index_json_shape_and_epoch(client, monkeypatch):
    """(a) Correct JSON shape and epoch math for a non-zero tz offset."""
    from unittest.mock import MagicMock

    mock_settings = MagicMock()
    mock_settings.playback_tz_offset_minutes = TZ_OFFSET
    monkeypatch.setattr("app.routers.playback.get_settings", lambda: mock_settings)

    mock_find = AsyncMock(return_value=TWO_CLIPS)
    monkeypatch.setattr("app.routers.playback.find_clips", mock_find)

    resp = client.get(f"/api/v1/playback/{NVR_ID}/1/index?date=2026-01-01")

    assert resp.status_code == 200
    data = resp.json()

    day_start = datetime(2026, 1, 1, 0, 0, 0)
    day_end = datetime(2026, 1, 2, 0, 0, 0)

    assert data["tz_offset_minutes"] == TZ_OFFSET
    assert data["day_start_epoch"] == _epoch(day_start, TZ_OFFSET)
    assert data["day_end_epoch"] == _epoch(day_end, TZ_OFFSET)
    assert len(data["clips"]) == 2

    c0 = data["clips"][0]
    assert c0["start_epoch"] == _epoch(TWO_CLIPS[0].start, TZ_OFFSET)
    assert c0["end_epoch"] == _epoch(TWO_CLIPS[0].end, TZ_OFFSET)
    assert c0["type"] == "Timing"
    assert c0["stream"] == "Main"

    c1 = data["clips"][1]
    assert c1["start_epoch"] == _epoch(TWO_CLIPS[1].start, TZ_OFFSET)
    assert c1["end_epoch"] == _epoch(TWO_CLIPS[1].end, TZ_OFFSET)
    assert c1["type"] == "Event"
    assert c1["stream"] == "Main"

    # Regression guard: find_clips must receive the HTTP port (80), NOT nvr.port
    # (RTSP, seeded as 554).  A regression to nvr.port would hit :554 over HTTP.
    _args, _kwargs = mock_find.call_args
    assert _args[1] == 80, (
        f"find_clips called with port {_args[1]}; expected HTTP port 80"
    )

    # Regression guard: end passed to find_clips must be 1 s before next midnight
    # so that a recording starting exactly at 00:00:00 next day does not leak in.
    from datetime import timedelta as _td
    expected_end = datetime(2026, 1, 2, 0, 0, 0) - _td(seconds=1)
    assert _kwargs.get("end") == expected_end, (
        f"find_clips end={_kwargs.get('end')}; expected {expected_end}"
    )


def test_cache_prevents_second_find_clips_call(client, monkeypatch):
    """(b) A second call within TTL must not re-invoke find_clips (cache hit)."""
    from unittest.mock import MagicMock

    mock_settings = MagicMock()
    mock_settings.playback_tz_offset_minutes = 0
    monkeypatch.setattr("app.routers.playback.get_settings", lambda: mock_settings)

    mock_find = AsyncMock(return_value=TWO_CLIPS)
    monkeypatch.setattr("app.routers.playback.find_clips", mock_find)

    resp1 = client.get(f"/api/v1/playback/{NVR_ID}/1/index?date=2026-01-02")
    resp2 = client.get(f"/api/v1/playback/{NVR_ID}/1/index?date=2026-01-02")

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert mock_find.call_count == 1  # cache served the second request


def test_404_for_unknown_nvr(client):
    """(c) A non-existent nvr_id must return 404."""
    resp = client.get("/api/v1/playback/NONEXISTENT-NVR/1/index?date=2026-01-01")
    assert resp.status_code == 404


def test_400_for_malformed_date(client):
    """(d) A date that does not match YYYY-MM-DD must return 400."""
    resp = client.get(f"/api/v1/playback/{NVR_ID}/1/index?date=not-a-date")
    assert resp.status_code == 400
