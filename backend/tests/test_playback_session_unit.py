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
import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.services.playback.session import (
    PlaybackSession,
    SessionState,
    _build_ffmpeg_argv,
    _drain_stderr,
    _redact_url,
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


def test_argv_speed1_has_no_vf_or_fps_mode():
    argv = _argv(speed=1)
    assert "-vf" not in argv
    assert "-fps_mode" not in argv


def test_argv_speed_gt1_has_select_filter_and_fps_mode_vfr():
    # -fps_mode vfr (NOT the removed -vsync): verified on the server's ffmpeg
    # build 2026-07-01 — `-vsync` is unrecognized there and aborts FF entirely.
    argv = _argv(speed=2)
    assert "-vf" in argv
    vf = argv[argv.index("-vf") + 1]
    assert vf.startswith("select=not(mod(n")
    assert "-fps_mode" in argv
    assert argv[argv.index("-fps_mode") + 1] == "vfr"


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


async def test_seek_clears_ring_of_stale_fragments():
    """Review #1: a respawn (seek) must CLEAR the ring so pre-seek fragments are
    never delivered after the new init segment (they'd corrupt the MSE buffer)."""
    sess = _session()
    procs = [_FakeProc(), _FakeProc()]
    with patch(
        "app.services.playback.session.asyncio.create_subprocess_exec",
        new=AsyncMock(side_effect=procs),
    ), patch(
        "app.services.playback.session.get_active_lockout",
        new=AsyncMock(return_value=None),
    ):
        await sess.open(1_700_000_000)
        # Stale pre-seek media sitting in the ring.
        sess._ring.put_nowait(b"STALE-1")
        sess._ring.put_nowait(b"STALE-2")
        assert sess._ring.qsize() == 2

        await sess.seek(1_700_000_100)
        # The ring is empty after the respawn — no stale chunk survives. The new
        # FakeProc's stdout blocks (no data), so the ring can only be empty by
        # virtue of the explicit clear.
        assert sess._ring.empty()
        assert sess.t0 == 1_700_000_100
        await sess.close()


async def test_set_speed_clears_ring_of_stale_fragments():
    """Review #1: changing speed also respawns and must clear the ring."""
    sess = _session()
    procs = [_FakeProc(), _FakeProc()]
    with patch(
        "app.services.playback.session.asyncio.create_subprocess_exec",
        new=AsyncMock(side_effect=procs),
    ), patch(
        "app.services.playback.session.get_active_lockout",
        new=AsyncMock(return_value=None),
    ):
        await sess.open(1_700_000_000)
        sess._ring.put_nowait(b"STALE-A")
        sess._ring.put_nowait(b"STALE-B")
        assert sess._ring.qsize() == 2

        await sess.set_speed(2)  # 1 → 2 with a live proc → respawn
        assert sess._ring.empty()
        assert sess.speed == 2
        await sess.close()


def test_footage_now_uses_t0_and_speed():
    sess = _session()
    sess.t0 = 1000
    sess._wall_start = 100.0
    sess.speed = 2
    with patch("app.services.playback.session.time.monotonic", return_value=105.0):
        assert sess.footage_now() == 1010


# ── Contract #12: credential redaction in ffmpeg stderr (Task 7 review fix) ──


def test_redact_url_strips_credentials_standalone():
    """_redact_url on a bare RTSP URL replaces user:pw with ***."""
    raw = "rtsp://admin:pa%40ss%2Aword@10.0.0.1:554/cam/playback"
    out = _redact_url(raw)
    assert "admin" not in out
    assert "pa%40ss%2Aword" not in out
    assert "***" in out
    assert "10.0.0.1" in out  # host preserved


def test_redact_url_handles_embedded_url_in_stderr_line():
    """_redact_url also redacts when the URL is embedded mid-line (ffmpeg error text)."""
    raw = "rtsp://admin:secret@1.2.3.4:554/cam/playback?channel=1: 401 Unauthorized"
    out = _redact_url(raw)
    assert "admin" not in out
    assert "secret" not in out
    assert "***" in out
    assert "401 Unauthorized" in out  # rest of line preserved


class _EofStream:
    """Readline stub: returns queued lines then EOF (b'')."""

    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""


class _FinishedProc:
    """Minimal subprocess stub that has already exited with a given rc."""

    def __init__(self, rc: int, stderr_lines: list[bytes]):
        self.returncode = rc
        self.stderr = _EofStream(stderr_lines)

    async def wait(self) -> int:
        return self.returncode


async def test_drain_stderr_redacts_credentials_in_logged_output(caplog):
    """Contract #12: credentialed RTSP URL in ffmpeg stderr must NOT appear in logs."""
    cred_line = (
        b"rtsp://admin:secret@1.2.3.4:554/cam/playback?channel=1: 401 Unauthorized\n"
    )
    proc = _FinishedProc(rc=1, stderr_lines=[cred_line])

    with patch(
        "app.services.playback.session.record_lockout", new=AsyncMock()
    ), caplog.at_level(logging.ERROR, logger="dss.playback.session"):
        await _drain_stderr(proc, "sess-test-redact", "1.2.3.4")

    # Password must not appear anywhere in what was logged.
    assert "secret" not in caplog.text, "credential leaked into log output"
    # Redaction marker must be present.
    assert "***" in caplog.text, "redaction marker missing from log output"


async def test_drain_stderr_redacts_credentials_in_auth_warning(caplog):
    """Contract #12: even the auth-failure warning path must not log credentials."""
    cred_line = (
        b"rtsp://admin:s3cr3t@1.2.3.4:554/cam/playback: 401 Unauthorized\n"
    )
    # rc=0 so the error log isn't emitted; only the warning path runs.
    proc = _FinishedProc(rc=0, stderr_lines=[cred_line])

    with patch(
        "app.services.playback.session.record_lockout", new=AsyncMock()
    ), caplog.at_level(logging.WARNING, logger="dss.playback.session"):
        await _drain_stderr(proc, "sess-test-warn", "1.2.3.4")

    assert "s3cr3t" not in caplog.text


# ── Task 10: to_status_dict (observability) ───────────────────────────────────


def _obs_session(**kwargs) -> PlaybackSession:
    """Construct a PlaybackSession with observability metadata fields set."""
    return PlaybackSession(
        nvr_id=kwargs.get("nvr_id", "nvr-obs"),
        channel=kwargs.get("channel", 2),
        user_id=kwargs.get("user_id", "uid-obs"),
        username=kwargs.get("username", "obs_user"),
        client_ip=kwargs.get("client_ip", "10.1.2.3"),
        nvr_label=kwargs.get("nvr_label", "Obs NVR"),
    )


def test_to_status_dict_has_all_required_keys():
    """to_status_dict() must return every key defined in the spec."""
    sess = _obs_session()
    d = sess.to_status_dict()
    required = (
        "session_id", "nvr_id", "nvr_label", "channel",
        "user_id", "username", "client_ip",
        "state", "speed", "footage_epoch",
        "uptime_seconds", "seek_count", "bytes_sent", "fragments_sent",
    )
    for key in required:
        assert key in d, f"key '{key}' missing from to_status_dict()"


def test_to_status_dict_values_match_construction():
    """Metadata fields set at construction time must appear verbatim in the dict."""
    sess = _obs_session(
        nvr_id="nvr-x", channel=5,
        user_id="u-123", username="alice", client_ip="192.168.0.1",
        nvr_label="Cam NVR",
    )
    d = sess.to_status_dict()
    assert d["nvr_id"] == "nvr-x"
    assert d["channel"] == 5
    assert d["user_id"] == "u-123"
    assert d["username"] == "alice"
    assert d["client_ip"] == "192.168.0.1"
    assert d["nvr_label"] == "Cam NVR"
    assert d["seek_count"] == 0
    assert d["bytes_sent"] == 0
    assert d["fragments_sent"] == 0


def test_to_status_dict_uptime_increases(monkeypatch):
    """uptime_seconds must reflect wall time elapsed since _started_at."""
    sess = _obs_session()
    sess._started_at = 100.0
    monkeypatch.setattr("app.services.playback.session.time.monotonic", lambda: 110.0)
    d1 = sess.to_status_dict()
    monkeypatch.setattr("app.services.playback.session.time.monotonic", lambda: 130.0)
    d2 = sess.to_status_dict()
    assert d1["uptime_seconds"] == 10
    assert d2["uptime_seconds"] == 30


def test_to_status_dict_footage_epoch_not_playing():
    """footage_epoch == t0 when state is not PLAYING (paused / idle / etc.)."""
    sess = _obs_session()
    sess.t0 = 1_700_000_000
    sess.state = SessionState.PAUSED
    assert sess.to_status_dict()["footage_epoch"] == 1_700_000_000

    sess.state = SessionState.IDLE
    assert sess.to_status_dict()["footage_epoch"] == 1_700_000_000


def test_to_status_dict_footage_epoch_when_playing(monkeypatch):
    """footage_epoch == footage_epoch_at(...) when state is PLAYING."""
    sess = _obs_session()
    sess.t0 = 1_700_000_000
    sess.state = SessionState.PLAYING
    sess._wall_start = 50.0
    sess.speed = 2
    monkeypatch.setattr("app.services.playback.session.time.monotonic", lambda: 60.0)
    d = sess.to_status_dict()
    expected = footage_epoch_at(1_700_000_000, 50.0, 2, 60.0)
    assert d["footage_epoch"] == expected


def test_to_status_dict_counters_reflect_mutations():
    """Mutating _bytes_sent/_fragments_sent/_seek_count must be visible in dict."""
    sess = _obs_session()
    sess._bytes_sent = 99_000
    sess._fragments_sent = 42
    sess._seek_count = 7
    d = sess.to_status_dict()
    assert d["bytes_sent"] == 99_000
    assert d["fragments_sent"] == 42
    assert d["seek_count"] == 7
