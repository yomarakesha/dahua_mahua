"""TDD tests for GET /api/v1/playback/{nvr_id}/{channel}/availability.

Pattern: minimal FastAPI test app (no lifespan), dependency overrides for auth
and session, monkeypatch for find_clips, in-memory SQLite for the NVR row.
Mirrors test_playback_router_index.py exactly.
"""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

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
from app.routers.playback import router as playback_router, clips_to_day_strings

# ── In-memory test DB ─────────────────────────────────────────────────────────

_engine = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_SessionMaker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

NVR_ID = "nvr-avail-test"
NVR_PW = "secret456"
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
            label="Avail Test NVR",
            ip="192.168.1.200",
            port=554,  # RTSP port — NOT the HTTP CGI port
            rtsp_username="admin",
            rtsp_password_encrypted=encrypt_password(NVR_PW),
            vendor=Vendor.dahua,
        ))
        await s.commit()
    yield
    await _engine.dispose()


@pytest.fixture(autouse=True)
def _clear_cache():
    """Wipe the in-process cache around every test."""
    playback_module._cache.clear()
    yield
    playback_module._cache.clear()


@pytest.fixture
def client():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app()) as c:
            yield c


# ── Shared test clips ─────────────────────────────────────────────────────────

EARLY_CLIP = Clip(
    start=datetime(2026, 1, 1, 6, 0, 0),
    end=datetime(2026, 1, 1, 10, 0, 0),
    type="Timing",
    stream="dav",
)

DAYTIME_CLIP = Clip(
    start=datetime(2026, 1, 15, 8, 0, 0),
    end=datetime(2026, 1, 15, 12, 0, 0),
    type="Event",
    stream="dav",
)

# Crosses midnight Jan 31 → Feb 1; must contribute BOTH day strings.
MIDNIGHT_CROSSING_CLIP = Clip(
    start=datetime(2026, 1, 31, 23, 30, 0),
    end=datetime(2026, 2, 1, 0, 30, 0),
    type="Timing",
    stream="dav",
)


def _epoch(dt_naive: datetime, tz_offset_minutes: int) -> int:
    """Expected UTC epoch: subtract the NVR's UTC offset."""
    utc = dt_naive - timedelta(minutes=tz_offset_minutes)
    return int(utc.replace(tzinfo=timezone.utc).timestamp())


# ── Pure helper unit tests ────────────────────────────────────────────────────

def test_helper_midnight_crossing_yields_both_days():
    """(a) A clip crossing midnight contributes both the start and end day."""
    days = clips_to_day_strings([MIDNIGHT_CROSSING_CLIP])
    assert "2026-01-31" in days
    assert "2026-02-01" in days
    assert len(days) == 2


def test_helper_same_day_clip():
    """A clip fully within one day yields only that day."""
    days = clips_to_day_strings([DAYTIME_CLIP])
    assert days == ["2026-01-15"]


def test_helper_empty_clips():
    """Empty clip list produces empty day list."""
    assert clips_to_day_strings([]) == []


def test_helper_deduplicates_same_day():
    """Two clips on the same day yield only one entry."""
    clip_a = Clip(
        start=datetime(2026, 1, 15, 8, 0),
        end=datetime(2026, 1, 15, 10, 0),
        type="T",
        stream="dav",
    )
    clip_b = Clip(
        start=datetime(2026, 1, 15, 14, 0),
        end=datetime(2026, 1, 15, 16, 0),
        type="T",
        stream="dav",
    )
    days = clips_to_day_strings([clip_a, clip_b])
    assert days == ["2026-01-15"]


def test_helper_output_is_sorted():
    """Output must be sorted in ascending calendar order."""
    days = clips_to_day_strings([DAYTIME_CLIP, EARLY_CLIP])
    assert days == sorted(days)


# ── Endpoint integration tests ────────────────────────────────────────────────

def test_availability_days_and_oldest_epoch(client, monkeypatch):
    """(a+b) Correct day set (incl. midnight-crossing clip) and oldest_epoch."""
    mock_settings = MagicMock()
    mock_settings.playback_tz_offset_minutes = TZ_OFFSET
    monkeypatch.setattr("app.routers.playback.get_settings", lambda: mock_settings)

    clips = [EARLY_CLIP, DAYTIME_CLIP, MIDNIGHT_CROSSING_CLIP]
    mock_find = AsyncMock(return_value=clips)
    monkeypatch.setattr("app.routers.playback.find_clips", mock_find)

    resp = client.get(f"/api/v1/playback/{NVR_ID}/1/availability?month=2026-01")
    assert resp.status_code == 200
    data = resp.json()

    # Expected days: Jan 1, Jan 15, Jan 31 (midnight cross) + Feb 1 (other side)
    assert set(data["days_with_recordings"]) == {
        "2026-01-01",
        "2026-01-15",
        "2026-01-31",
        "2026-02-01",
    }
    # Must be sorted
    assert data["days_with_recordings"] == sorted(data["days_with_recordings"])

    # oldest_epoch = epoch of earliest clip start (EARLY_CLIP: 2026-01-01 06:00)
    expected_oldest = _epoch(EARLY_CLIP.start, TZ_OFFSET)
    assert data["oldest_epoch"] == expected_oldest


def test_availability_oldest_epoch_null_when_no_clips(client, monkeypatch):
    """(b) oldest_epoch must be null when the month has no recordings."""
    mock_settings = MagicMock()
    mock_settings.playback_tz_offset_minutes = 0
    monkeypatch.setattr("app.routers.playback.get_settings", lambda: mock_settings)

    mock_find = AsyncMock(return_value=[])
    monkeypatch.setattr("app.routers.playback.find_clips", mock_find)

    resp = client.get(f"/api/v1/playback/{NVR_ID}/1/availability?month=2026-02")
    assert resp.status_code == 200
    data = resp.json()
    assert data["days_with_recordings"] == []
    assert data["oldest_epoch"] is None


def test_availability_cache_prevents_second_find_clips_call(client, monkeypatch):
    """(c) A second call within TTL must not re-invoke find_clips (cache hit)."""
    mock_settings = MagicMock()
    mock_settings.playback_tz_offset_minutes = 0
    monkeypatch.setattr("app.routers.playback.get_settings", lambda: mock_settings)

    mock_find = AsyncMock(return_value=[DAYTIME_CLIP])
    monkeypatch.setattr("app.routers.playback.find_clips", mock_find)

    resp1 = client.get(f"/api/v1/playback/{NVR_ID}/1/availability?month=2026-03")
    resp2 = client.get(f"/api/v1/playback/{NVR_ID}/1/availability?month=2026-03")

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert mock_find.call_count == 1  # cache served the second request


def test_availability_400_bad_month_format(client):
    """(d) A month string not matching YYYY-MM must return 400."""
    resp = client.get(f"/api/v1/playback/{NVR_ID}/1/availability?month=not-a-month")
    assert resp.status_code == 400


def test_availability_400_invalid_month_value(client):
    """(d) A value resembling YYYY-MM but with month=13 must return 400."""
    resp = client.get(f"/api/v1/playback/{NVR_ID}/1/availability?month=2026-13")
    assert resp.status_code == 400


def test_availability_404_unknown_nvr(client):
    """(e) A non-existent nvr_id must return 404."""
    resp = client.get("/api/v1/playback/NONEXISTENT-NVR/1/availability?month=2026-01")
    assert resp.status_code == 404


def test_availability_http_port_80(client, monkeypatch):
    """Regression: find_clips must be called with HTTP port 80, NOT nvr.port (554)."""
    mock_settings = MagicMock()
    mock_settings.playback_tz_offset_minutes = 0
    monkeypatch.setattr("app.routers.playback.get_settings", lambda: mock_settings)

    mock_find = AsyncMock(return_value=[])
    monkeypatch.setattr("app.routers.playback.find_clips", mock_find)

    client.get(f"/api/v1/playback/{NVR_ID}/1/availability?month=2026-04")

    _args, _kwargs = mock_find.call_args
    assert _args[1] == 80, (
        f"find_clips called with port {_args[1]}; expected HTTP port 80"
    )


def test_availability_end_is_last_second_of_month(client, monkeypatch):
    """Regression: end passed to find_clips must be the last second of the month."""
    mock_settings = MagicMock()
    mock_settings.playback_tz_offset_minutes = 0
    monkeypatch.setattr("app.routers.playback.get_settings", lambda: mock_settings)

    mock_find = AsyncMock(return_value=[])
    monkeypatch.setattr("app.routers.playback.find_clips", mock_find)

    client.get(f"/api/v1/playback/{NVR_ID}/1/availability?month=2026-01")

    _args, _kwargs = mock_find.call_args
    # Last second of January 2026
    expected_end = datetime(2026, 2, 1, 0, 0, 0) - timedelta(seconds=1)
    assert _kwargs.get("end") == expected_end, (
        f"find_clips end={_kwargs.get('end')}; expected {expected_end}"
    )


def test_availability_december_month_boundary(client, monkeypatch):
    """Month boundary for December must wrap to Jan of next year."""
    mock_settings = MagicMock()
    mock_settings.playback_tz_offset_minutes = 0
    monkeypatch.setattr("app.routers.playback.get_settings", lambda: mock_settings)

    mock_find = AsyncMock(return_value=[])
    monkeypatch.setattr("app.routers.playback.find_clips", mock_find)

    client.get(f"/api/v1/playback/{NVR_ID}/1/availability?month=2026-12")

    _args, _kwargs = mock_find.call_args
    expected_start = datetime(2026, 12, 1, 0, 0, 0)
    expected_end = datetime(2027, 1, 1, 0, 0, 0) - timedelta(seconds=1)
    assert _args[2] == expected_start or _kwargs.get("start") == expected_start
    assert _kwargs.get("end") == expected_end
