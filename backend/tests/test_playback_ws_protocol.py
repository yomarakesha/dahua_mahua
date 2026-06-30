"""Unit tests for the playback WS control-message state machine (Task 8).

These exercise ``_dispatch`` and the clean-vs-crash death helper in isolation —
``PlaybackSession`` is replaced by a lightweight fake, and the WebSocket by a
recorder.  No real ffmpeg, no real WebSocket, no live NVR (network unavailable).

INTEGRATION concerns (live fMP4 bytes, real seek/pause through Caddy) are NOT
covered here — see the on-network checklist in the task report.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.routers.playback import (
    _INIT_CODEC,
    _classify_session_end,
    _dispatch,
    _egress_loop,
    _EGRESS_STOP,
    _fragment_producer,
)
from app.services.playback.session import SessionState


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


def _drain(q: asyncio.Queue) -> list:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# ── _dispatch ───────────────────────────────────────────────────────────────
# Under the single-egress design, _dispatch only mutates the session (and on bad
# input enqueues a sanitised error); it NEVER emits reinit (the fragment producer
# owns that) and NEVER raises.


@pytest.mark.asyncio
async def test_dispatch_seek_calls_session_no_reinit():
    sess, out = FakeSession(), asyncio.Queue()
    await _dispatch({"seek": 1719734400}, sess, out)
    assert ("seek", 1719734400) in sess.calls
    # reinit is emitted by the producer on the new spawn-gen, not by _dispatch.
    assert _drain(out) == []


@pytest.mark.asyncio
async def test_dispatch_seek_updates_t0():
    """Contract: after seek, t0 reflects the new seek target (#4)."""
    sess, out = FakeSession(), asyncio.Queue()
    assert sess.t0 == 1000
    await _dispatch({"seek": 1719734400}, sess, out)
    assert sess.t0 == 1719734400


@pytest.mark.asyncio
async def test_dispatch_speed_calls_set_speed_no_reinit():
    sess, out = FakeSession(), asyncio.Queue()
    await _dispatch({"speed": 2}, sess, out)
    assert ("set_speed", 2) in sess.calls
    assert _drain(out) == []


@pytest.mark.asyncio
async def test_dispatch_speed_rejects_non_whitelisted_gracefully():
    """Out-of-whitelist speed → sanitised error, session untouched, no raise."""
    sess, out = FakeSession(), asyncio.Queue()
    await _dispatch({"speed": 3}, sess, out)  # must NOT raise
    assert sess.calls == []  # validation happens before touching the session
    items = _drain(out)
    assert len(items) == 1 and items[0]["type"] == "error"


@pytest.mark.asyncio
async def test_dispatch_speed_non_int_rejected_gracefully():
    """`int("fast")` ValueError must NOT tear down the session (review #3)."""
    sess, out = FakeSession(), asyncio.Queue()
    await _dispatch({"speed": "fast"}, sess, out)  # must NOT raise
    assert sess.calls == []
    items = _drain(out)
    assert len(items) == 1 and items[0]["type"] == "error"


@pytest.mark.asyncio
async def test_dispatch_pause_calls_pause_no_reinit():
    sess, out = FakeSession(), asyncio.Queue()
    await _dispatch({"pause": True}, sess, out)
    assert sess.calls == [("pause",)]
    assert _drain(out) == []  # pause does not re-init the decoder


@pytest.mark.asyncio
async def test_dispatch_play_resumes_at_footage_now_no_reinit():
    sess, out = FakeSession(), asyncio.Queue()
    await _dispatch({"play": True}, sess, out)
    assert ("resume", sess._footage) in sess.calls
    assert _drain(out) == []


@pytest.mark.asyncio
async def test_dispatch_keepalive_updates_timestamp_only():
    sess, out = FakeSession(), asyncio.Queue()
    before = time.monotonic()
    await _dispatch({"keepalive": True}, sess, out)
    assert sess._last_keepalive >= before
    assert sess.calls == []
    assert _drain(out) == []


@pytest.mark.asyncio
async def test_dispatch_stream_is_main_only_noop():
    sess, out = FakeSession(), asyncio.Queue()
    await _dispatch({"stream": "sub"}, sess, out)  # must not raise
    assert sess.calls == []
    assert _drain(out) == []


@pytest.mark.asyncio
async def test_dispatch_unknown_message_is_ignored(caplog):
    sess, out = FakeSession(), asyncio.Queue()
    await _dispatch({"wat": "huh"}, sess, out)  # must not raise
    assert sess.calls == []
    assert _drain(out) == []


@pytest.mark.asyncio
async def test_dispatch_seek_zero_rejected_gracefully():
    sess, out = FakeSession(), asyncio.Queue()
    await _dispatch({"seek": 0}, sess, out)  # must NOT raise
    assert sess.calls == []
    items = _drain(out)
    assert len(items) == 1 and items[0]["type"] == "error"


@pytest.mark.asyncio
async def test_dispatch_seek_non_int_rejected_gracefully():
    sess, out = FakeSession(), asyncio.Queue()
    await _dispatch({"seek": "not-an-int"}, sess, out)  # must NOT raise
    assert sess.calls == []
    items = _drain(out)
    assert len(items) == 1 and items[0]["type"] == "error"


@pytest.mark.asyncio
async def test_footage_now_advances_with_speed():
    """Footage epoch math is delegated to footage_now (speed-aware)."""
    sess = FakeSession()
    sess._footage = 9999
    assert sess.footage_now() == 9999


# ── Single-egress + ordering ─────────────────────────────────────────────────


class _RecorderWS:
    """Records the FIFO sequence of send_bytes / send_json the egress performs."""

    def __init__(self) -> None:
        self.sent: list = []

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


@pytest.mark.asyncio
async def test_egress_drains_queue_in_fifo_order_via_single_sender():
    """A binary chunk, a clock dict and a reinit dict drain in FIFO order, and
    binary vs JSON is routed to the right WS send method by the ONE sender."""
    ws = _RecorderWS()
    out: asyncio.Queue = asyncio.Queue()
    out.put_nowait(b"\x00\x01frag")
    out.put_nowait({"type": "clock", "wall_ts": 123})
    out.put_nowait({"type": "reinit", "t0": 456})
    out.put_nowait(_EGRESS_STOP)
    await asyncio.wait_for(_egress_loop(ws, out), timeout=1.0)
    assert ws.sent == [
        b"\x00\x01frag",
        {"type": "clock", "wall_ts": 123},
        {"type": "reinit", "t0": 456},
    ]


class _ProducerSession:
    """Drives _fragment_producer: a ring + per-generation pinned init segment."""

    def __init__(self) -> None:
        self.state = SessionState.PLAYING
        self._closing = False
        self._spawn_gen = 1
        self.t0 = 1000
        self.ring_buffer_chunks = 8
        self._ring: asyncio.Queue = asyncio.Queue()
        self._proc = None
        self._init_by_gen = {1: b"INIT-G1", 2: b"INIT-G2"}

    async def wait_init_segment(self, timeout: float = 10.0) -> bytes | None:
        return self._init_by_gen.get(self._spawn_gen)


@pytest.mark.asyncio
async def test_producer_emits_init_then_reinit_init_fragment_in_order():
    """After a simulated respawn the producer enqueues, in order:
    init(gen1) → init-seg1 → frag1 → reinit(gen2) → init-seg2 → frag2."""
    sess = _ProducerSession()
    out: asyncio.Queue = asyncio.Queue()
    sess._ring.put_nowait(b"FRAG-1")  # a gen-1 fragment already buffered

    task = asyncio.create_task(_fragment_producer(sess, out))
    await asyncio.sleep(0.05)  # let it emit init + init-seg + drain FRAG-1

    # Respawn: bump the generation, swap in a fresh pinned init, feed a fragment.
    sess.t0 = 2000
    sess._spawn_gen = 2
    sess._ring.put_nowait(b"FRAG-2")
    await asyncio.sleep(0.05)

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    items = _drain(out)
    assert items == [
        {"type": "init", "t0": 1000, "codec": _INIT_CODEC},
        b"INIT-G1",
        b"FRAG-1",
        {"type": "reinit", "t0": 2000},
        b"INIT-G2",
        b"FRAG-2",
    ]


# ── clean-vs-crash death helper (#2) ────────────────────────────────────────


def test_classify_clean_eof_on_zero_returncode():
    assert _classify_session_end(0) == "eof"


def test_classify_crash_on_nonzero_returncode():
    assert _classify_session_end(1) == "error"
    assert _classify_session_end(69) == "error"


def test_classify_crash_on_signal_death():
    # A natural signal death (not our own kill) is a crash from the WS layer's POV.
    assert _classify_session_end(-11) == "error"
