"""TDD tests for GET /playback/sessions — admin-only active-sessions endpoint (Task 10).

Pattern: minimal FastAPI app with the playback router + dep overrides for auth.
Fake sessions are injected directly into ``_active_sessions`` in the test fixture;
no DB, no real ffmpeg, no network.
"""

from __future__ import annotations

import time
import uuid
import warnings

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.deps import get_current_user
from app.models import Role, User
from app.routers.playback import router as playback_router
import app.services.playback.session as _session_module
from app.services.playback.session import PlaybackSession, SessionState


# ── App factory ───────────────────────────────────────────────────────────────


def _make_app(role: str = "admin") -> FastAPI:
    """Minimal test app — only the playback router, overridden auth."""
    app = FastAPI()
    app.include_router(playback_router, prefix="/api/v1")

    async def _override_auth() -> User:
        u = User(username="testuser", password_hash="x", role=Role(role))
        u.regions = []
        u.cameras = []
        return u

    app.dependency_overrides[get_current_user] = _override_auth
    return app


# ── Helpers ───────────────────────────────────────────────────────────────────


def _fake_session(**kwargs) -> PlaybackSession:
    """Construct a minimal PlaybackSession for injection into _active_sessions."""
    sid = str(uuid.uuid4())
    sess = PlaybackSession(
        session_id=sid,
        nvr_id=kwargs.get("nvr_id", "nvr1"),
        channel=kwargs.get("channel", 1),
        user_id=kwargs.get("user_id", "uid-fake"),
        username=kwargs.get("username", "alice"),
        client_ip=kwargs.get("client_ip", "10.0.0.1"),
        nvr_label=kwargs.get("nvr_label", "Test NVR"),
    )
    sess._started_at = time.monotonic()
    sess.t0 = 1_700_000_000
    return sess


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_active_sessions():
    """Ensure a clean registry before and after every test."""
    _session_module._active_sessions.clear()
    yield
    _session_module._active_sessions.clear()


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_admin_empty_registry_returns_200_with_zero_total():
    """Admin with an empty registry → 200 {total:0, sessions:[]}."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app("admin")) as c:
            resp = c.get("/api/v1/playback/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["sessions"] == []


def test_operator_user_gets_403():
    """Non-admin (operator) → 403 Forbidden."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app("operator")) as c:
            resp = c.get("/api/v1/playback/sessions")
    assert resp.status_code == 403


def test_two_injected_sessions_returns_total_2():
    """Two sessions injected into _active_sessions → total:2, two entries."""
    s1 = _fake_session(nvr_id="nvr1", username="alice")
    s2 = _fake_session(nvr_id="nvr2", username="bob")
    _session_module._active_sessions[s1.session_id] = s1
    _session_module._active_sessions[s2.session_id] = s2

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app("admin")) as c:
            resp = c.get("/api/v1/playback/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["sessions"]) == 2


def test_session_dict_contains_all_required_fields():
    """Each session dict must carry the full observability field set."""
    s = _fake_session(
        nvr_id="nvr-detail",
        user_id="uid-42",
        username="charlie",
        client_ip="192.168.1.99",
        nvr_label="Detail NVR",
        channel=3,
    )
    s._seek_count = 5
    s._bytes_sent = 81_920
    s._fragments_sent = 12
    _session_module._active_sessions[s.session_id] = s

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app("admin")) as c:
            resp = c.get("/api/v1/playback/sessions")
    assert resp.status_code == 200
    entries = resp.json()["sessions"]
    assert len(entries) == 1
    sd = entries[0]

    # Identity + routing
    assert sd["session_id"] == s.session_id
    assert sd["nvr_id"] == "nvr-detail"
    assert sd["nvr_label"] == "Detail NVR"
    assert sd["channel"] == 3

    # User info
    assert sd["user_id"] == "uid-42"
    assert sd["username"] == "charlie"
    assert sd["client_ip"] == "192.168.1.99"

    # Playback state
    assert "state" in sd
    assert "speed" in sd
    assert "footage_epoch" in sd

    # Counters
    assert sd["seek_count"] == 5
    assert sd["bytes_sent"] == 81_920
    assert sd["fragments_sent"] == 12

    # Timing — just assert key present and non-negative
    assert "uptime_seconds" in sd
    assert sd["uptime_seconds"] >= 0


def test_single_session_metadata_matches_injected_values():
    """Values set on the injected session must round-trip through the endpoint."""
    s = _fake_session(nvr_id="nvr-rt", user_id="uid-rt", username="roundtrip")
    _session_module._active_sessions[s.session_id] = s

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with TestClient(_make_app("admin")) as c:
            resp = c.get("/api/v1/playback/sessions")
    assert resp.status_code == 200
    sd = resp.json()["sessions"][0]
    assert sd["nvr_id"] == "nvr-rt"
    assert sd["user_id"] == "uid-rt"
    assert sd["username"] == "roundtrip"
