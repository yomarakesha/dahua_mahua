"""TDD tests for GET /api/v1/playback/{nvr_id}/{channel}/days?month=YYYY-MM.

Returns the 1-based day numbers of a month that have recordings (calendar
helper).  Mirrors test_playback_router_availability.py, but the endpoint auths
via resolve_playback_user (?token= OR header), so tests override THAT dep.
"""

from __future__ import annotations

import uuid as _uuid_mod
import warnings
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from app.crypto import encrypt_password
from app.db import Base, get_session
from app.models import Camera, Nvr, Region, Role, User, Vendor
from app.services.playback.index_parser import Clip
import app.routers.playback as playback_module
from app.routers.playback import (
    router as playback_router,
    clips_to_day_numbers,
    resolve_playback_user,
)

# ── In-memory test DB ─────────────────────────────────────────────────────────

_engine = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_SessionMaker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

NVR_ID = "nvr-days-test"
NVR_PW = "secretdays"
TZ_OFFSET = 300  # UTC+5 (production value) — days are NVR-local, so unaffected

_REGION_UUID = _uuid_mod.UUID("dddddddd-0000-0000-0000-000000000001")
_CAMERA_UUID = _uuid_mod.UUID("dddddddd-0000-0000-0000-000000000002")


def _make_app() -> FastAPI:
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

    app.dependency_overrides[resolve_playback_user] = _override_auth
    app.dependency_overrides[get_session] = _override_session
    return app


def _make_operator_app(*, with_camera: bool = False) -> FastAPI:
    app = FastAPI()
    app.include_router(playback_router, prefix="/api/v1")

    async def _override_auth() -> User:
        u = User(username="testop", password_hash="x", role=Role.operator)
        u.regions = []
        u.cameras = (
            [Camera(id=_CAMERA_UUID, nvr_id=NVR_ID, channel=1)] if with_camera else []
        )
        return u

    async def _override_session():
        async with _SessionMaker() as s:
            yield s

    app.dependency_overrides[resolve_playback_user] = _override_auth
    app.dependency_overrides[get_session] = _override_session
    return app


@pytest_asyncio.fixture(scope="module", autouse=True)
async def _setup_db():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _SessionMaker() as s:
        s.add(Region(id=_REGION_UUID, slug="days-region", name="Days Region"))
        await s.flush()
        s.add(Nvr(
            id=NVR_ID,
            label="Days NVR",
            ip="192.168.1.201",
            port=554,
            rtsp_username="admin",
            rtsp_password_encrypted=encrypt_password(NVR_PW),
            vendor=Vendor.dahua,
            region_id=_REGION_UUID,
        ))
        await s.flush()
        s.add(Camera(id=_CAMERA_UUID, nvr_id=NVR_ID, channel=1))
        await s.commit()
    yield
    await _engine.dispose()


@pytest.fixture(autouse=True)
def _clear_cache():
    playback_module._cache.clear()
    yield
    playback_module._cache.clear()


@pytest.fixture
def client():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app()) as c:
            yield c


# ── Shared clips ──────────────────────────────────────────────────────────────

EARLY_CLIP = Clip(start=datetime(2026, 1, 1, 6, 0), end=datetime(2026, 1, 1, 10, 0),
                  type="Timing", stream="Main")
DAYTIME_CLIP = Clip(start=datetime(2026, 1, 15, 8, 0), end=datetime(2026, 1, 15, 12, 0),
                    type="Event", stream="Main")
# Crosses Jan 31 → Feb 1.
MIDNIGHT_CROSSING_CLIP = Clip(start=datetime(2026, 1, 31, 23, 30),
                              end=datetime(2026, 2, 1, 0, 30),
                              type="Timing", stream="Main")


# ── Pure helper unit tests ────────────────────────────────────────────────────

def test_helper_returns_day_ints_for_month():
    days = clips_to_day_numbers([EARLY_CLIP, DAYTIME_CLIP], "2026-01")
    assert days == [1, 15]


def test_helper_midnight_crossing_only_in_month_days():
    """A Jan 31 → Feb 1 clip contributes day 31 to Jan and day 1 to Feb, and
    NOTHING outside the queried month."""
    assert clips_to_day_numbers([MIDNIGHT_CROSSING_CLIP], "2026-01") == [31]
    assert clips_to_day_numbers([MIDNIGHT_CROSSING_CLIP], "2026-02") == [1]


def test_helper_empty_clips():
    assert clips_to_day_numbers([], "2026-01") == []


def test_helper_deduplicates_and_sorts():
    a = Clip(start=datetime(2026, 1, 15, 8, 0), end=datetime(2026, 1, 15, 9, 0),
             type="T", stream="Main")
    b = Clip(start=datetime(2026, 1, 15, 14, 0), end=datetime(2026, 1, 15, 15, 0),
             type="T", stream="Main")
    assert clips_to_day_numbers([DAYTIME_CLIP, EARLY_CLIP, a, b], "2026-01") == [1, 15]


# ── Endpoint integration tests ────────────────────────────────────────────────

def test_days_returns_distinct_days(client, monkeypatch):
    mock_settings = MagicMock()
    mock_settings.playback_tz_offset_minutes = TZ_OFFSET
    monkeypatch.setattr("app.routers.playback.get_settings", lambda: mock_settings)
    clips = [EARLY_CLIP, DAYTIME_CLIP, MIDNIGHT_CROSSING_CLIP]
    monkeypatch.setattr("app.routers.playback.find_clips", AsyncMock(return_value=clips))

    resp = client.get(f"/api/v1/playback/{NVR_ID}/1/days?month=2026-01")
    assert resp.status_code == 200
    data = resp.json()
    assert data["month"] == "2026-01"
    # Jan 1, Jan 15, Jan 31 (Feb 1 is outside the queried month → excluded)
    assert data["days"] == [1, 15, 31]


def test_days_empty_month(client, monkeypatch):
    mock_settings = MagicMock()
    mock_settings.playback_tz_offset_minutes = 0
    monkeypatch.setattr("app.routers.playback.get_settings", lambda: mock_settings)
    monkeypatch.setattr("app.routers.playback.find_clips", AsyncMock(return_value=[]))

    resp = client.get(f"/api/v1/playback/{NVR_ID}/1/days?month=2026-05")
    assert resp.status_code == 200
    assert resp.json() == {"month": "2026-05", "days": []}


def test_days_cache_prevents_second_find(client, monkeypatch):
    mock_settings = MagicMock()
    mock_settings.playback_tz_offset_minutes = 0
    monkeypatch.setattr("app.routers.playback.get_settings", lambda: mock_settings)
    mock_find = AsyncMock(return_value=[DAYTIME_CLIP])
    monkeypatch.setattr("app.routers.playback.find_clips", mock_find)

    client.get(f"/api/v1/playback/{NVR_ID}/1/days?month=2026-06")
    client.get(f"/api/v1/playback/{NVR_ID}/1/days?month=2026-06")
    assert mock_find.call_count == 1


def test_days_http_port_80_and_month_bounds(client, monkeypatch):
    mock_settings = MagicMock()
    mock_settings.playback_tz_offset_minutes = 0
    monkeypatch.setattr("app.routers.playback.get_settings", lambda: mock_settings)
    mock_find = AsyncMock(return_value=[])
    monkeypatch.setattr("app.routers.playback.find_clips", mock_find)

    client.get(f"/api/v1/playback/{NVR_ID}/1/days?month=2026-12")
    _args, _kwargs = mock_find.call_args
    assert _args[1] == 80  # HTTP CGI port, not RTSP 554
    # December wraps to the last second before Jan next year.
    assert _kwargs["start"] == datetime(2026, 12, 1, 0, 0, 0)
    assert _kwargs["end"] == datetime(2027, 1, 1, 0, 0, 0) - timedelta(seconds=1)


def test_days_400_bad_month(client):
    assert client.get(f"/api/v1/playback/{NVR_ID}/1/days?month=nope").status_code == 400
    assert client.get(f"/api/v1/playback/{NVR_ID}/1/days?month=2026-13").status_code == 400


def test_days_404_unknown_nvr(client):
    resp = client.get("/api/v1/playback/NONEXISTENT/1/days?month=2026-01")
    assert resp.status_code == 404


def test_days_200_operator_with_camera(monkeypatch):
    mock_settings = MagicMock()
    mock_settings.playback_tz_offset_minutes = 0
    monkeypatch.setattr("app.routers.playback.get_settings", lambda: mock_settings)
    monkeypatch.setattr("app.routers.playback.find_clips", AsyncMock(return_value=[]))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_operator_app(with_camera=True)) as c:
            resp = c.get(f"/api/v1/playback/{NVR_ID}/1/days?month=2026-01")
    assert resp.status_code == 200


def test_days_404_operator_without_camera(monkeypatch):
    monkeypatch.setattr("app.routers.playback.find_clips", AsyncMock(return_value=[]))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_operator_app(with_camera=False)) as c:
            resp = c.get(f"/api/v1/playback/{NVR_ID}/1/days?month=2026-01")
    assert resp.status_code == 404
