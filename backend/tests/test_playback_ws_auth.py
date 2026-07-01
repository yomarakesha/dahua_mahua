"""WS auth-rejection tests for /playback/{nvr_id}/{channel}/stream (Task 8).

Pattern mirrors test_playback_router_index.py: minimal FastAPI app, in-memory
SQLite seeded with a User/Nvr/Camera, dependency override for the DB session,
and monkeypatch for decode_token + get_budget.  No real WebSocket to an NVR and
no ffmpeg — every case is rejected (or rejected at budget) BEFORE accept().
"""

from __future__ import annotations

import uuid as _uuid_mod
import warnings
from contextlib import asynccontextmanager

import jwt
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

import app.routers.playback as playback_module
from app.crypto import encrypt_password
from app.db import Base, get_session
from app.models import Camera, Nvr, Role, User, Vendor
from app.routers.playback import router as playback_router
from app.services.playback.nvr_budget import BudgetExhausted

# ── In-memory test DB ─────────────────────────────────────────────────────────

_engine = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_SessionMaker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

NVR_ID = "nvr-ws"
NVR_PW = "pa@ss*word"
T_PARAM = 1719734400  # a valid footage epoch

ADMIN_ID = _uuid_mod.UUID("bbbbbbbb-0000-0000-0000-000000000001")
OPERATOR_ID = _uuid_mod.UUID("bbbbbbbb-0000-0000-0000-000000000002")
CAMERA_ID = _uuid_mod.UUID("bbbbbbbb-0000-0000-0000-000000000003")


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(playback_router, prefix="/api/v1")

    async def _override_session():
        async with _SessionMaker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    return app


@pytest_asyncio.fixture(scope="module", autouse=True)
async def _setup_db():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _SessionMaker() as s:
        s.add(User(id=ADMIN_ID, username="wsadmin", password_hash="x", role=Role.admin))
        s.add(User(id=OPERATOR_ID, username="wsop", password_hash="x", role=Role.operator))
        s.add(Nvr(
            id=NVR_ID, label="WS NVR", ip="192.168.9.9", port=554,
            rtsp_username="admin", rtsp_password_encrypted=encrypt_password(NVR_PW),
            vendor=Vendor.dahua, region_id=None,
        ))
        await s.flush()
        s.add(Camera(id=CAMERA_ID, nvr_id=NVR_ID, channel=1))
        await s.commit()
    yield
    await _engine.dispose()


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    playback_module._rate_limits.clear()
    yield
    playback_module._rate_limits.clear()


@pytest.fixture
def client():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app()) as c:
            yield c


def _expect_close(client, url: str) -> int:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(url):
            pass
    return exc.value.code


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_no_token_closes_4001(client):
    code = _expect_close(client, f"/api/v1/playback/{NVR_ID}/1/stream?t={T_PARAM}")
    assert code == 4001


def test_bad_token_closes_4001(client, monkeypatch):
    def _raise(_tok):
        raise jwt.InvalidTokenError("nope")

    monkeypatch.setattr(playback_module, "decode_token", _raise)
    code = _expect_close(
        client, f"/api/v1/playback/{NVR_ID}/1/stream?t={T_PARAM}&token=garbage"
    )
    assert code == 4001


def test_operator_without_camera_closes_4003(client, monkeypatch):
    monkeypatch.setattr(
        playback_module, "decode_token", lambda _t: {"sub": str(OPERATOR_ID)}
    )
    code = _expect_close(
        client, f"/api/v1/playback/{NVR_ID}/1/stream?t={T_PARAM}&token=op"
    )
    assert code == 4003


def test_unknown_nvr_closes_4004(client, monkeypatch):
    monkeypatch.setattr(
        playback_module, "decode_token", lambda _t: {"sub": str(ADMIN_ID)}
    )
    code = _expect_close(
        client, f"/api/v1/playback/NOPE-NVR/1/stream?t={T_PARAM}&token=adm"
    )
    assert code == 4004


def test_unknown_camera_channel_closes_4004(client, monkeypatch):
    monkeypatch.setattr(
        playback_module, "decode_token", lambda _t: {"sub": str(ADMIN_ID)}
    )
    code = _expect_close(
        client, f"/api/v1/playback/{NVR_ID}/99/stream?t={T_PARAM}&token=adm"
    )
    assert code == 4004


def test_budget_exhausted_closes_4429(client, monkeypatch):
    monkeypatch.setattr(
        playback_module, "decode_token", lambda _t: {"sub": str(ADMIN_ID)}
    )

    class _FullBudget:
        def session(self, _nvr_id):
            @asynccontextmanager
            async def _cm():
                raise BudgetExhausted("full")
                yield  # pragma: no cover

            return _cm()

    monkeypatch.setattr(playback_module, "get_budget", lambda: _FullBudget())
    code = _expect_close(
        client, f"/api/v1/playback/{NVR_ID}/1/stream?t={T_PARAM}&token=adm"
    )
    assert code == 4429


# ── Transport toggle (?transport=udp|tcp) ─────────────────────────────────────
#
# The transport query param must reach the PlaybackSession constructor
# unchanged when it's "udp"/"tcp", and fall back to "udp" for anything else
# (missing, empty, garbage).  A fake PlaybackSession captures its constructor
# kwargs and fails fast in open() so the test never spawns real ffmpeg.


class _CapturingSession:
    """Stand-in for PlaybackSession: records constructor kwargs, fails fast."""

    captured: list[dict] = []

    def __init__(self, **kwargs):
        _CapturingSession.captured.append(kwargs)
        self.session_id = "capturing-session"

    async def open(self, start_epoch):  # noqa: ARG002
        raise RuntimeError("stop before any real ffmpeg spawn")

    async def close(self):
        pass


@pytest.fixture(autouse=True)
def _reset_captured_sessions():
    _CapturingSession.captured.clear()
    yield
    _CapturingSession.captured.clear()


class _OpenBudget:
    """A budget that always grants (unlike _FullBudget) — for tests that need
    to get PAST the budget gate and into PlaybackSession construction."""

    def session(self, _nvr_id):
        @asynccontextmanager
        async def _cm():
            yield

        return _cm()


def _connect_and_drain(client, url: str) -> None:
    """Connect, read whatever the server sends (the error json), then let the
    server-initiated close play out — mirrors the fail-fast-in-open() path."""
    try:
        with client.websocket_connect(url) as ws:
            ws.receive_json()
    except WebSocketDisconnect:
        pass


def test_transport_tcp_query_param_reaches_session(client, monkeypatch):
    monkeypatch.setattr(
        playback_module, "decode_token", lambda _t: {"sub": str(ADMIN_ID)}
    )
    monkeypatch.setattr(playback_module, "get_budget", lambda: _OpenBudget())
    monkeypatch.setattr(playback_module, "PlaybackSession", _CapturingSession)
    _connect_and_drain(
        client, f"/api/v1/playback/{NVR_ID}/1/stream?t={T_PARAM}&token=adm&transport=tcp"
    )
    assert _CapturingSession.captured[0]["transport"] == "tcp"


def test_transport_defaults_to_udp_when_omitted(client, monkeypatch):
    monkeypatch.setattr(
        playback_module, "decode_token", lambda _t: {"sub": str(ADMIN_ID)}
    )
    monkeypatch.setattr(playback_module, "get_budget", lambda: _OpenBudget())
    monkeypatch.setattr(playback_module, "PlaybackSession", _CapturingSession)
    _connect_and_drain(
        client, f"/api/v1/playback/{NVR_ID}/1/stream?t={T_PARAM}&token=adm"
    )
    assert _CapturingSession.captured[0]["transport"] == "udp"


def test_transport_invalid_value_falls_back_to_udp(client, monkeypatch):
    monkeypatch.setattr(
        playback_module, "decode_token", lambda _t: {"sub": str(ADMIN_ID)}
    )
    monkeypatch.setattr(playback_module, "get_budget", lambda: _OpenBudget())
    monkeypatch.setattr(playback_module, "PlaybackSession", _CapturingSession)
    _connect_and_drain(
        client,
        f"/api/v1/playback/{NVR_ID}/1/stream?t={T_PARAM}&token=adm&transport=bogus",
    )
    assert _CapturingSession.captured[0]["transport"] == "udp"
