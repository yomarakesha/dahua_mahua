"""Unit tests for the PlaybackSession pure/mockable parts (Task 7).

Coverage:
- ``footage_epoch_at``: pure wall-clock → footage-epoch math at every speed.
- ``_build_ffmpeg_argv``: argv shape — fMP4 (not mpegts), UDP input, AAC,
  speed filter only when speed>1, ``pipe:1`` last, no spaces, maxrate handling.
- Lifecycle (mocked subprocess, no real ffmpeg):
  * ``close()`` is idempotent and cancels the drain + stderr tasks.
  * back-pressure: a full ring drops the OLDEST chunk and never blocks.

INTEGRATION concerns (live NVR / real ffmpeg) are NOT covered here — see the
on-network checklist in the task report.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services.playback.session import (
    PlaybackSession,
    SessionState,
    _build_ffmpeg_argv,
    footage_epoch_at,
)


# ── footage_epoch_at (pure) ────────────────────────────────────────────────


def test_footage_epoch_1x():
    assert footage_epoch_at(1000, 0.0, 1, 5.0) == 1005


def test_footage_epoch_2x():
    assert footage_epoch_at(1000, 0.0, 2, 5.0) == 1010


def test_footage_epoch_4x():
    assert footage_epoch_at(1000, 0.0, 4, 5.0) == 1020


def test_footage_epoch_8x():
    assert footage_epoch_at(1000, 0.0, 8, 5.0) == 1040


def test_footage_epoch_zero_elapsed_zero_t0():
    assert footage_epoch_at(0, 0.0, 1, 0.0) == 0


def test_footage_epoch_truncates_fractional_elapsed():
    # 1.5s elapsed at 1× → +1 (int truncation toward zero).
    assert footage_epoch_at(1000, 100.0, 1, 101.5) == 1001


@pytest.mark.parametrize("speed", [1, 2, 4, 8])
def test_footage_epoch_parametrized_speeds(speed):
    t0, wall_start, now = 5000, 10.0, 17.0  # 7s elapsed
    assert footage_epoch_at(t0, wall_start, speed, now) == t0 + 7 * speed


@pytest.mark.parametrize("speed", [1, 2, 4, 8])
def test_footage_epoch_zero_elapsed_always_t0(speed):
    start = 1234.5
    assert footage_epoch_at(9999, start, speed, start) == 9999


# ── _build_ffmpeg_argv (pure) ──────────────────────────────────────────────

_URL = "rtsp://u:%2A%2A%2A@1.2.3.4:554/cam/playback?channel=1&starttime=x&endtime=y"


def _argv(speed=1, maxrate=8000):
    return _build_ffmpeg_argv(
        ffbin="ffmpeg",
        rtsp_url=_URL,
        vcodec="libx264",
        keyframe_seconds=0.5,
        speed=speed,
        maxrate_kbps=maxrate,
    )


def test_argv_speed1_has_no_vf_or_vsync():
    argv = _argv(speed=1)
    assert "-vf" not in argv
    assert "-vsync" not in argv


def test_argv_speed_gt1_has_select_filter_and_vsync_vfr():
    argv = _argv(speed=2)
    assert "-vf" in argv
    vf = argv[argv.index("-vf") + 1]
    assert vf.startswith("select=not(mod(n")
    assert "-vsync" in argv
    assert argv[argv.index("-vsync") + 1] == "vfr"


def test_argv_pipe1_is_last():
    assert _argv(speed=1)[-1] == "pipe:1"
    assert _argv(speed=4)[-1] == "pipe:1"


def test_argv_uses_fmp4_not_mpegts():
    argv = _argv()
    assert "-f" in argv
    assert argv[argv.index("-f") + 1] == "mp4"
    assert "mpegts" not in argv


def test_argv_has_frag_movflags():
    argv = _argv()
    assert "-movflags" in argv
    assert argv[argv.index("-movflags") + 1] == "frag_keyframe+empty_moov+default_base_moof"


def test_argv_has_udp_rtsp_transport():
    argv = _argv()
    assert "-rtsp_transport" in argv
    assert argv[argv.index("-rtsp_transport") + 1] == "udp"


def test_argv_has_aac_audio():
    argv = _argv()
    assert "-c:a" in argv
    assert argv[argv.index("-c:a") + 1] == "aac"


def test_argv_ffbin_first():
    assert _argv()[0] == "ffmpeg"


def test_argv_no_element_contains_space():
    for speed in (1, 2, 4, 8):
        for el in _argv(speed=speed):
            assert " " not in el, f"shell-split risk in element: {el!r}"


def test_argv_maxrate_zero_omits_maxrate():
    argv = _argv(maxrate=0)
    assert "-maxrate" not in argv
    assert "-bufsize" not in argv


def test_argv_maxrate_set_adds_maxrate_and_bufsize():
    argv = _argv(maxrate=8000)
    assert "-maxrate" in argv
    assert argv[argv.index("-maxrate") + 1] == "8000k"
    assert "-bufsize" in argv
    assert argv[argv.index("-bufsize") + 1] == "8000k"


# ── Lifecycle (mocked subprocess) ──────────────────────────────────────────


class _FakeStream:
    """Stdout/stderr stand-in: yields queued chunks then blocks (live stream)."""

    def __init__(self, chunks=None):
        self._chunks = list(chunks or [])

    async def read(self, n=-1):
        if self._chunks:
            return self._chunks.pop(0)
        await asyncio.sleep(3600)  # emulate a live stream waiting for data
        return b""

    async def readline(self):
        if self._chunks:
            return self._chunks.pop(0)
        await asyncio.sleep(3600)
        return b""


class _FakeProc:
    def __init__(self):
        self.pid = 4242
        self.returncode = None
        self.stdout = _FakeStream()
        self.stderr = _FakeStream()
        self.kill_count = 0

    def kill(self):
        self.kill_count += 1
        if self.returncode is None:
            self.returncode = -9

    def terminate(self):
        if self.returncode is None:
            self.returncode = -15

    async def wait(self):
        return self.returncode if self.returncode is not None else 0


def _session():
    return PlaybackSession(
        nvr_id="nvr1",
        nvr_ip="1.2.3.4",
        rtsp_user="admin",
        rtsp_pw="pa@ss*word",
        channel=1,
        clip_end_epoch=2_000_000_000,
        ring_buffer_chunks=4,
    )


async def test_close_is_idempotent_and_cancels_tasks():
    sess = _session()
    fake = _FakeProc()
    with patch(
        "app.services.playback.session.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake),
    ), patch(
        "app.services.playback.session.get_active_lockout",
        new=AsyncMock(return_value=None),
    ):
        await sess.open(1_700_000_000)
        assert sess.state == SessionState.PLAYING
        drain, stderr = sess._drain_task, sess._stderr_task
        assert drain is not None and stderr is not None

        await sess.close()
        assert sess.state == SessionState.CLOSED
        assert sess._proc is None
        assert fake.kill_count >= 1
        assert drain.cancelled() or drain.done()
        assert stderr.cancelled() or stderr.done()

        # Second close must be a no-op (no crash, no extra kill on a None proc).
        kills_after_first = fake.kill_count
        await sess.close()
        assert sess.state == SessionState.CLOSED
        assert fake.kill_count == kills_after_first


async def test_open_rejected_when_locked_out():
    sess = _session()
    with patch(
        "app.services.playback.session.get_active_lockout",
        new=AsyncMock(return_value=object()),  # truthy lockout
    ), patch(
        "app.services.playback.session.asyncio.create_subprocess_exec",
        new=AsyncMock(side_effect=AssertionError("ffmpeg must not spawn when locked")),
    ):
        with pytest.raises(Exception):
            await sess.open(1_700_000_000)


def test_backpressure_drops_oldest_and_never_blocks():
    sess = _session()  # ring_buffer_chunks=4
    for i in range(4):
        sess._enqueue(bytes([i]))
    assert sess._ring.full()
    # One more — must not raise/block; oldest (b"\x00") is dropped.
    sess._enqueue(b"\x09")
    assert sess._ring.qsize() == 4
    drained = []
    while not sess._ring.empty():
        drained.append(sess._ring.get_nowait())
    assert drained == [b"\x01", b"\x02", b"\x03", b"\x09"]


def test_footage_now_uses_t0_and_speed():
    sess = _session()
    sess.t0 = 1000
    sess._wall_start = 100.0
    sess.speed = 2
    with patch("app.services.playback.session.time.monotonic", return_value=105.0):
        assert sess.footage_now() == 1010
