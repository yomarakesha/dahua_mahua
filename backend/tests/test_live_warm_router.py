"""Tests for POST /api/v1/live/warm.

Pattern mirrors test_playback_router_availability: a minimal FastAPI app (no
lifespan) with dependency overrides for auth + session, an in-memory SQLite DB
seeded with one NVR + cameras, and a FAKE warm pool that records set_desired
calls (no real go2rtc / NVR network).
"""

from __future__ import annotations

import uuid as _uuid_mod
import warnings

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
import app.routers.live as live_module
from app.routers.live import router as live_router

# ── In-memory test DB ─────────────────────────────────────────────────────────

_engine = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_SessionMaker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

NVR_ID = "nvr-warm-test"
NVR_ID_DISABLED = "nvr-warm-disabled"
NVR_PW = "secret789"

_REGION_UUID = _uuid_mod.UUID("cccccccc-0000-0000-0000-000000000001")
CAM1_UUID = _uuid_mod.UUID("cccccccc-0000-0000-0000-000000000002")  # ch1, has_sub
CAM2_UUID = _uuid_mod.UUID("cccccccc-0000-0000-0000-000000000003")  # ch2, has_sub
CAM_NOSUB_UUID = _uuid_mod.UUID("cccccccc-0000-0000-0000-000000000004")  # ch3, no sub
CAM_DISABLED_NVR_UUID = _uuid_mod.UUID("cccccccc-0000-0000-0000-000000000005")


# ── Fake warm pool ────────────────────────────────────────────────────────────


class _FakePool:
    def __init__(self) -> None:
        self.calls: list[list[tuple[str, int]]] = []

    async def set_desired(self, keys):
        ks = list(keys)
        self.calls.append(ks)
        return {"warming": len(ks), "capped": 0}


@pytest.fixture
def fake_pool(monkeypatch):
    p = _FakePool()
    monkeypatch.setattr(live_module, "get_warm_pool", lambda: p)
    return p


def _enable(monkeypatch, enabled: bool):
    from types import SimpleNamespace

    monkeypatch.setattr(
        live_module, "get_settings", lambda: SimpleNamespace(warm_pool_enabled=enabled)
    )


# ── App factories ─────────────────────────────────────────────────────────────


def _make_app(user: User) -> FastAPI:
    app = FastAPI()
    app.include_router(live_router, prefix="/api/v1")

    async def _override_auth() -> User:
        return user

    async def _override_session():
        async with _SessionMaker() as s:
            yield s

    app.dependency_overrides[get_current_user] = _override_auth
    app.dependency_overrides[get_session] = _override_session
    return app


def _admin() -> User:
    u = User(username="warmadmin", password_hash="x", role=Role.admin)
    u.regions = []
    u.cameras = []
    return u


def _operator(camera_ids: list[_uuid_mod.UUID]) -> User:
    u = User(username="warmop", password_hash="x", role=Role.operator)
    u.regions = []
    u.cameras = [Camera(id=cid, nvr_id=NVR_ID, channel=1) for cid in camera_ids]
    return u


def _client(user: User) -> TestClient:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return TestClient(_make_app(user))


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture(scope="module", autouse=True)
async def _setup_db():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _SessionMaker() as s:
        s.add(Region(id=_REGION_UUID, slug="warm-region", name="Warm Test Region"))
        await s.flush()
        s.add(Nvr(
            id=NVR_ID,
            label="Warm Test NVR",
            ip="192.168.1.210",
            port=554,
            rtsp_username="admin",
            rtsp_password_encrypted=encrypt_password(NVR_PW),
            vendor=Vendor.dahua,
            region_id=_REGION_UUID,
            enabled=True,
        ))
        s.add(Nvr(
            id=NVR_ID_DISABLED,
            label="Disabled NVR",
            ip="192.168.1.211",
            port=554,
            rtsp_username="admin",
            rtsp_password_encrypted=encrypt_password(NVR_PW),
            vendor=Vendor.dahua,
            enabled=False,
        ))
        await s.flush()
        s.add(Camera(id=CAM1_UUID, nvr_id=NVR_ID, channel=1, has_sub=True))
        s.add(Camera(id=CAM2_UUID, nvr_id=NVR_ID, channel=2, has_sub=True))
        s.add(Camera(id=CAM_NOSUB_UUID, nvr_id=NVR_ID, channel=3, has_sub=False))
        s.add(Camera(id=CAM_DISABLED_NVR_UUID, nvr_id=NVR_ID_DISABLED, channel=1, has_sub=True))
        await s.commit()
    yield
    await _engine.dispose()


# ── Disabled → no-op ──────────────────────────────────────────────────────────


def test_warm_disabled_is_noop(fake_pool, monkeypatch):
    _enable(monkeypatch, False)
    with _client(_admin()) as c:
        resp = c.post("/api/v1/live/warm", json={"camera_ids": [str(CAM1_UUID)]})
    assert resp.status_code == 202
    assert resp.json() == {"warming": 0, "capped": 0}
    # Nothing recorded, nothing warmed.
    assert fake_pool.calls == []


# ── Enabled → forwards accessible SUB cameras ─────────────────────────────────


def test_warm_admin_forwards_keys(fake_pool, monkeypatch):
    _enable(monkeypatch, True)
    with _client(_admin()) as c:
        resp = c.post(
            "/api/v1/live/warm",
            json={"camera_ids": [str(CAM1_UUID), str(CAM2_UUID)]},
        )
    assert resp.status_code == 202
    assert resp.json() == {"warming": 2, "capped": 0}
    assert fake_pool.calls == [[(NVR_ID, 1), (NVR_ID, 2)]]


def test_warm_preserves_request_order(fake_pool, monkeypatch):
    _enable(monkeypatch, True)
    with _client(_admin()) as c:
        c.post(
            "/api/v1/live/warm",
            json={"camera_ids": [str(CAM2_UUID), str(CAM1_UUID)]},
        )
    assert fake_pool.calls[0] == [(NVR_ID, 2), (NVR_ID, 1)]


def test_warm_filters_camera_without_sub(fake_pool, monkeypatch):
    _enable(monkeypatch, True)
    with _client(_admin()) as c:
        c.post(
            "/api/v1/live/warm",
            json={"camera_ids": [str(CAM1_UUID), str(CAM_NOSUB_UUID)]},
        )
    assert fake_pool.calls[0] == [(NVR_ID, 1)]  # no-sub camera dropped


def test_warm_filters_disabled_nvr(fake_pool, monkeypatch):
    _enable(monkeypatch, True)
    with _client(_admin()) as c:
        c.post(
            "/api/v1/live/warm",
            json={"camera_ids": [str(CAM_DISABLED_NVR_UUID), str(CAM1_UUID)]},
        )
    assert fake_pool.calls[0] == [(NVR_ID, 1)]  # disabled-NVR camera dropped


def test_warm_filters_unknown_camera(fake_pool, monkeypatch):
    _enable(monkeypatch, True)
    with _client(_admin()) as c:
        c.post(
            "/api/v1/live/warm",
            json={"camera_ids": [str(_uuid_mod.uuid4()), str(CAM1_UUID)]},
        )
    assert fake_pool.calls[0] == [(NVR_ID, 1)]


def test_warm_empty_clears_desired(fake_pool, monkeypatch):
    _enable(monkeypatch, True)
    with _client(_admin()) as c:
        resp = c.post("/api/v1/live/warm", json={"camera_ids": []})
    assert resp.status_code == 202
    assert fake_pool.calls == [[]]  # empty desired set forwarded


# ── Per-camera RBAC filtering ─────────────────────────────────────────────────


def test_warm_operator_only_granted_cameras(fake_pool, monkeypatch):
    _enable(monkeypatch, True)
    # Operator granted CAM1 only; requests CAM1 + CAM2 → CAM2 filtered out.
    op = _operator([CAM1_UUID])
    with _client(op) as c:
        c.post(
            "/api/v1/live/warm",
            json={"camera_ids": [str(CAM1_UUID), str(CAM2_UUID)]},
        )
    assert fake_pool.calls[0] == [(NVR_ID, 1)]


def test_warm_operator_without_grants_warms_nothing(fake_pool, monkeypatch):
    _enable(monkeypatch, True)
    op = _operator([])  # no grants
    with _client(op) as c:
        resp = c.post(
            "/api/v1/live/warm",
            json={"camera_ids": [str(CAM1_UUID), str(CAM2_UUID)]},
        )
    assert resp.status_code == 202
    assert fake_pool.calls[0] == []
