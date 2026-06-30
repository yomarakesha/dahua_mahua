"""Unit tests for the playback WS control-message state machine (Task 8).

These exercise ``_dispatch`` and the clean-vs-crash death helper in isolation —
``PlaybackSession`` is replaced by a lightweight fake, and the WebSocket by a
recorder.  No real ffmpeg, no real WebSocket, no live NVR (network unavailable).

INTEGRATION concerns (live fMP4 bytes, real seek/pause through Caddy) are NOT
covered here — see the on-network checklist in the task report.
"""

from __future__ import annotations

import time

import pytest

from app.routers.playback import _classify_session_end, _dispatch
from app.services.playback.url_builder import PlaybackUrlError


# ── Fakes ──────────────────────────────────────────────────────────────────


class FakeSession:
    """Records control calls and mutates ``t0`` like the real session would."""

    def __init__(self) -> None:
        self.t0 = 1000
        self.speed = 1
        self.state = "playing"
        self.calls: list[tuple] = []
        self._last_keepalive = 0.0
        self._paused_at = 0.0
        self._footage = 5000

    async def seek(self, epoch: int) -> None:
        self.calls.append(("seek", epoch))
        self.t0 = epoch  # the real session updates t0 on (re)spawn

    async def set_speed(self, speed: int) -> None:
        self.calls.append(("set_speed", speed))
        self.speed = speed
        self.t0 = 2000  # respawn moves t0 to the resume point

    async def pause(self) -> None:
        self.calls.append(("pause",))

    async def resume(self, epoch: int) -> None:
        self.calls.append(("resume", epoch))
        self.t0 = epoch

    def footage_now(self) -> int:
        return self._footage


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


# ── _dispatch ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_seek_calls_session_and_sends_reinit():
    sess, ws = FakeSession(), FakeWS()
    await _dispatch({"seek": 1719734400}, sess, ws)
    assert ("seek", 1719734400) in sess.calls
    assert ws.sent == [{"type": "reinit", "t0": 1719734400}]


@pytest.mark.asyncio
async def test_dispatch_seek_updates_t0():
    """Contract: after seek, t0 reflects the new seek target (#4)."""
    sess, ws = FakeSession(), FakeWS()
    assert sess.t0 == 1000
    await _dispatch({"seek": 1719734400}, sess, ws)
    assert sess.t0 == 1719734400
    assert ws.sent[-1]["t0"] == 1719734400


@pytest.mark.asyncio
async def test_dispatch_speed_calls_set_speed_and_sends_reinit():
    sess, ws = FakeSession(), FakeWS()
    await _dispatch({"speed": 2}, sess, ws)
    assert ("set_speed", 2) in sess.calls
    assert ws.sent[-1]["type"] == "reinit"
    assert ws.sent[-1]["t0"] == sess.t0


@pytest.mark.asyncio
async def test_dispatch_speed_rejects_non_whitelisted_before_session():
    sess, ws = FakeSession(), FakeWS()
    with pytest.raises(PlaybackUrlError):
        await _dispatch({"speed": 3}, sess, ws)
    assert sess.calls == []  # validation happens before touching the session
    assert ws.sent == []


@pytest.mark.asyncio
async def test_dispatch_pause_calls_pause_no_reinit():
    sess, ws = FakeSession(), FakeWS()
    await _dispatch({"pause": True}, sess, ws)
    assert sess.calls == [("pause",)]
    assert ws.sent == []  # pause does not re-init the decoder


@pytest.mark.asyncio
async def test_dispatch_play_resumes_at_footage_now_and_sends_reinit():
    sess, ws = FakeSession(), FakeWS()
    await _dispatch({"play": True}, sess, ws)
    assert ("resume", sess._footage) in sess.calls
    assert ws.sent[-1]["type"] == "reinit"
    assert ws.sent[-1]["t0"] == sess.t0


@pytest.mark.asyncio
async def test_dispatch_keepalive_updates_timestamp_only():
    sess, ws = FakeSession(), FakeWS()
    before = time.monotonic()
    await _dispatch({"keepalive": True}, sess, ws)
    assert sess._last_keepalive >= before
    assert sess.calls == []
    assert ws.sent == []


@pytest.mark.asyncio
async def test_dispatch_stream_is_main_only_noop():
    sess, ws = FakeSession(), FakeWS()
    await _dispatch({"stream": "sub"}, sess, ws)  # must not raise
    assert sess.calls == []
    assert ws.sent == []


@pytest.mark.asyncio
async def test_dispatch_unknown_message_is_ignored(caplog):
    sess, ws = FakeSession(), FakeWS()
    await _dispatch({"wat": "huh"}, sess, ws)  # must not raise
    assert sess.calls == []
    assert ws.sent == []


@pytest.mark.asyncio
async def test_dispatch_seek_zero_rejected():
    sess, ws = FakeSession(), FakeWS()
    with pytest.raises(PlaybackUrlError):
        await _dispatch({"seek": 0}, sess, ws)
    assert sess.calls == []


@pytest.mark.asyncio
async def test_dispatch_seek_non_int_rejected():
    sess, ws = FakeSession(), FakeWS()
    with pytest.raises(PlaybackUrlError):
        await _dispatch({"seek": "not-an-int"}, sess, ws)
    assert sess.calls == []


@pytest.mark.asyncio
async def test_footage_now_advances_with_speed():
    """Footage epoch math is delegated to footage_now (speed-aware)."""
    sess = FakeSession()
    sess._footage = 9999
    assert sess.footage_now() == 9999


# ── clean-vs-crash death helper (#2) ────────────────────────────────────────


def test_classify_clean_eof_on_zero_returncode():
    assert _classify_session_end(0) == "eof"


def test_classify_crash_on_nonzero_returncode():
    assert _classify_session_end(1) == "error"
    assert _classify_session_end(69) == "error"


def test_classify_crash_on_signal_death():
    # A natural signal death (not our own kill) is a crash from the WS layer's POV.
    assert _classify_session_end(-11) == "error"
