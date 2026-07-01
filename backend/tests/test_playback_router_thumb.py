"""TDD tests for GET /api/v1/playback/{nvr_id}/{channel}/thumb?at=<epoch>.

Pattern: minimal FastAPI test app (no lifespan), dep overrides for auth+session,
monkeypatch for grab_frame, in-memory SQLite DB for NVR+Camera rows.

Per-camera RBAC (Contract #1): load Camera by (nvr_id, channel), authorise
with user_can_access_camera — exactly like /index.  Missing NVR or camera, or
operator without a camera grant → 404.
"""

from __future__ import annotations

import uuid as _uuid_mod
import warnings
from unittest.mock import AsyncMock

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
from app.deps import get_current_user
from app.models import Camera, Nvr, Region, Role, User, Vendor

import app.routers.playback as playback_module
from app.routers.playback import router as playback_router
from app.services.playback.snapshot import SnapshotError

# ── In-memory test DB ─────────────────────────────────────────────────────────

_engine = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_SessionMaker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

NVR_ID = "nvr-thumb-test"
NVR_PW = "thumbsecret"
TZ_OFFSET = 0

# Fixed IDs for per-camera RBAC operator tests.
_REGION_UUID = _uuid_mod.UUID("cccccccc-0000-0000-0000-000000000001")
_CAMERA_UUID = _uuid_mod.UUID("cccccccc-0000-0000-0000-000000000002")

# Minimal valid at= (footage epoch) and channel for positive-path tests.
_AT = 1_719_734_400  # 2024-06-30 00:00:00 UTC — positive, non-zero
_CH = 1


# ── App factories ─────────────────────────────────────────────────────────────


def _make_app() -> FastAPI:
    """Minimal test app wired with an admin user — no lifespan."""
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


def _make_operator_app(*, with_camera: bool = False) -> FastAPI:
    """Minimal test app wired with an operator user.

    *with_camera* grants the seeded Camera to the operator (positive RBAC).
    Without it the operator has no camera grants → 404.
    """
    app = FastAPI()
    app.include_router(playback_router, prefix="/api/v1")

    async def _override_auth() -> User:
        u = User(username="testop", password_hash="x", role=Role.operator)
        u.regions = []
        u.cameras = (
            [Camera(id=_CAMERA_UUID, nvr_id=NVR_ID, channel=_CH)]
            if with_camera
            else []
        )
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
    """Create schema and seed Region, NVR, Camera for the entire module."""
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _SessionMaker() as s:
        s.add(Region(id=_REGION_UUID, slug="thumb-region", name="Thumb Region"))
        await s.flush()
        s.add(Nvr(
            id=NVR_ID,
            label="Thumb NVR",
            ip="192.168.1.200",
            port=554,
            rtsp_username="admin",
            rtsp_password_encrypted=encrypt_password(NVR_PW),
            vendor=Vendor.dahua,
            region_id=_REGION_UUID,
        ))
        await s.flush()
        s.add(Camera(id=_CAMERA_UUID, nvr_id=NVR_ID, channel=_CH))
        await s.commit()
    yield
    await _engine.dispose()


@pytest.fixture
def client(monkeypatch):
    """Admin TestClient with grab_frame mocked to return a minimal JPEG stub."""
    monkeypatch.setattr(
        "app.routers.playback.grab_frame",
        AsyncMock(return_value=b"\xff\xd8\xff\xe0stub"),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app()) as c:
            yield c


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_thumb_200_jpeg(client):
    """GET /thumb?at=<epoch> → 200, Content-Type: image/jpeg."""
    resp = client.get(f"/api/v1/playback/{NVR_ID}/{_CH}/thumb?at={_AT}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/jpeg")
    assert resp.content  # non-empty body


def test_thumb_502_on_snapshot_error(monkeypatch):
    """grab_frame raising SnapshotError → 502 Bad Gateway."""
    monkeypatch.setattr(
        "app.routers.playback.grab_frame",
        AsyncMock(side_effect=SnapshotError("ffmpeg produced no output")),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app()) as c:
            resp = c.get(f"/api/v1/playback/{NVR_ID}/{_CH}/thumb?at={_AT}")
    assert resp.status_code == 502


def test_thumb_404_unknown_nvr(monkeypatch):
    """An nvr_id not in the DB → 404 (no SSRF leak)."""
    monkeypatch.setattr(
        "app.routers.playback.grab_frame",
        AsyncMock(return_value=b"\xff\xd8\xff"),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app()) as c:
            resp = c.get(f"/api/v1/playback/NONEXISTENT/1/thumb?at={_AT}")
    assert resp.status_code == 404


def test_thumb_400_at_zero(monkeypatch):
    """at=0 → 400 (validate_footage_epoch rejects non-positive epochs)."""
    monkeypatch.setattr(
        "app.routers.playback.grab_frame",
        AsyncMock(return_value=b"\xff\xd8\xff"),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app()) as c:
            resp = c.get(f"/api/v1/playback/{NVR_ID}/{_CH}/thumb?at=0")
    assert resp.status_code == 400


def test_thumb_400_channel_zero(monkeypatch):
    """channel=0 → 400 (validate_channel rejects zero and negative channels)."""
    monkeypatch.setattr(
        "app.routers.playback.grab_frame",
        AsyncMock(return_value=b"\xff\xd8\xff"),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app()) as c:
            resp = c.get(f"/api/v1/playback/{NVR_ID}/0/thumb?at={_AT}")
    assert resp.status_code == 400


def test_thumb_200_operator_with_camera(monkeypatch):
    """Operator with explicit camera grant → 200 (per-camera RBAC positive path)."""
    monkeypatch.setattr(
        "app.routers.playback.grab_frame",
        AsyncMock(return_value=b"\xff\xd8\xff\xe0operator"),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_operator_app(with_camera=True)) as c:
            resp = c.get(f"/api/v1/playback/{NVR_ID}/{_CH}/thumb?at={_AT}")
    assert resp.status_code == 200


def test_thumb_404_operator_without_camera(monkeypatch):
    """Operator without any camera grant → 404 (per-camera RBAC, Contract #1)."""
    monkeypatch.setattr(
        "app.routers.playback.grab_frame",
        AsyncMock(return_value=b"\xff\xd8\xff"),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_operator_app(with_camera=False)) as c:
            resp = c.get(f"/api/v1/playback/{NVR_ID}/{_CH}/thumb?at={_AT}")
    assert resp.status_code == 404
