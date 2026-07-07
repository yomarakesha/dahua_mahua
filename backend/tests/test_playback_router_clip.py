"""TDD tests for GET /api/v1/playback/{nvr_id}/{channel}/clip (MP4 download).

The router validates + bounds the range, RBACs the camera, runs export_clip
into a temp file, and returns a FileResponse attachment.  export_clip is mocked
(no real ffmpeg/NVR); a side-effect writes stub bytes to the out_path so the
download has content.  Auth is resolve_playback_user (?token= OR header) — tests
override that dep.
"""

from __future__ import annotations

import os
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
from app.models import Camera, Nvr, Region, Role, User, Vendor
from app.routers.playback import router as playback_router, resolve_playback_user
from app.services.playback.clip_export import ClipExportError

_engine = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_SessionMaker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

NVR_ID = "nvr-clip-test"
NVR_PW = "clipsecret"
_CH = 1
_START = 1_719_734_400          # 2024-06-30 00:00:00 UTC
_END = _START + 60             # 60-second clip (well under the 600s cap)

_REGION_UUID = _uuid_mod.UUID("eeeeeeee-0000-0000-0000-000000000001")
_CAMERA_UUID = _uuid_mod.UUID("eeeeeeee-0000-0000-0000-000000000002")


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
            [Camera(id=_CAMERA_UUID, nvr_id=NVR_ID, channel=_CH)] if with_camera else []
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
        s.add(Region(id=_REGION_UUID, slug="clip-region", name="Clip Region"))
        await s.flush()
        s.add(Nvr(
            id=NVR_ID,
            label="Clip NVR",
            ip="192.168.1.202",
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


def _stub_export(recorder: dict):
    """An export_clip stand-in that records its kwargs and writes stub MP4 bytes
    to the out_path (simulating a successful ffmpeg pull)."""
    async def _fake(**kwargs):
        recorder.update(kwargs)
        with open(kwargs["out_path"], "wb") as f:
            f.write(b"\x00\x00\x00\x18ftypmp42stubmp4data")
    return _fake


# ── Success path ──────────────────────────────────────────────────────────────

def test_clip_200_attachment_headers(monkeypatch):
    rec: dict = {}
    monkeypatch.setattr("app.routers.playback.export_clip", _stub_export(rec))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app()) as c:
            resp = c.get(
                f"/api/v1/playback/{NVR_ID}/{_CH}/clip?start={_START}&end={_END}"
            )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    cd = resp.headers["content-disposition"]
    assert cd.startswith("attachment")
    assert f'filename="{NVR_ID}_ch{_CH}_{_START}.mp4"' in cd
    assert resp.content  # non-empty body
    # Temp file must be cleaned up by the background task after the response.
    assert not os.path.exists(rec["out_path"])


def test_clip_forces_tcp_transport(monkeypatch):
    rec: dict = {}
    monkeypatch.setattr("app.routers.playback.export_clip", _stub_export(rec))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app()) as c:
            # Even asking for udp, export must force TCP (reliability).
            c.get(
                f"/api/v1/playback/{NVR_ID}/{_CH}/clip"
                f"?start={_START}&end={_END}&transport=udp"
            )
    assert rec["transport"] == "tcp"
    assert rec["rtsp_port"] == 554  # nvr.port is the RTSP port (Contract #9)
    assert rec["start_epoch"] == _START
    assert rec["end_epoch"] == _END


# ── Duration cap ───────────────────────────────────────────────────────────────

def test_clip_400_over_max_duration(monkeypatch):
    called = AsyncMock()
    monkeypatch.setattr("app.routers.playback.export_clip", called)
    over_end = _START + 601  # 601s > default cap 600s
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app()) as c:
            resp = c.get(
                f"/api/v1/playback/{NVR_ID}/{_CH}/clip?start={_START}&end={over_end}"
            )
    assert resp.status_code == 400
    assert "maximum" in resp.json()["detail"].lower()
    called.assert_not_called()  # rejected before any ffmpeg work


def test_clip_at_max_duration_ok(monkeypatch):
    rec: dict = {}
    monkeypatch.setattr("app.routers.playback.export_clip", _stub_export(rec))
    end_at_cap = _START + 600  # exactly at the cap → allowed
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app()) as c:
            resp = c.get(
                f"/api/v1/playback/{NVR_ID}/{_CH}/clip?start={_START}&end={end_at_cap}"
            )
    assert resp.status_code == 200


# ── Bad input ──────────────────────────────────────────────────────────────────

def test_clip_400_end_before_start(monkeypatch):
    monkeypatch.setattr("app.routers.playback.export_clip", AsyncMock())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app()) as c:
            resp = c.get(
                f"/api/v1/playback/{NVR_ID}/{_CH}/clip?start={_END}&end={_START}"
            )
    assert resp.status_code == 400


def test_clip_400_zero_epoch(monkeypatch):
    monkeypatch.setattr("app.routers.playback.export_clip", AsyncMock())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app()) as c:
            resp = c.get(f"/api/v1/playback/{NVR_ID}/{_CH}/clip?start=0&end={_END}")
    assert resp.status_code == 400


# ── RBAC ───────────────────────────────────────────────────────────────────────

def test_clip_404_unknown_nvr(monkeypatch):
    monkeypatch.setattr("app.routers.playback.export_clip", AsyncMock())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app()) as c:
            resp = c.get(f"/api/v1/playback/NONEXISTENT/1/clip?start={_START}&end={_END}")
    assert resp.status_code == 404


def test_clip_404_operator_without_camera(monkeypatch):
    monkeypatch.setattr("app.routers.playback.export_clip", AsyncMock())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_operator_app(with_camera=False)) as c:
            resp = c.get(
                f"/api/v1/playback/{NVR_ID}/{_CH}/clip?start={_START}&end={_END}"
            )
    assert resp.status_code == 404


def test_clip_200_operator_with_camera(monkeypatch):
    rec: dict = {}
    monkeypatch.setattr("app.routers.playback.export_clip", _stub_export(rec))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_operator_app(with_camera=True)) as c:
            resp = c.get(
                f"/api/v1/playback/{NVR_ID}/{_CH}/clip?start={_START}&end={_END}"
            )
    assert resp.status_code == 200


# ── Export failure → 502 + temp cleanup ─────────────────────────────────────────

def test_clip_502_and_temp_cleaned_on_export_error(monkeypatch):
    captured: dict = {}

    async def _boom(**kwargs):
        captured["out_path"] = kwargs["out_path"]
        # Simulate ffmpeg producing nothing (out_path stays empty), then failing.
        raise ClipExportError("no recorded video for the requested range")

    monkeypatch.setattr("app.routers.playback.export_clip", _boom)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app()) as c:
            resp = c.get(
                f"/api/v1/playback/{NVR_ID}/{_CH}/clip?start={_START}&end={_END}"
            )
    assert resp.status_code == 502
    # The reserved temp file must be removed even on the failure path.
    assert not os.path.exists(captured["out_path"])
