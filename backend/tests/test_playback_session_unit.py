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
    footage_epoch_at,
)
from app.services.playback.url_builder import PlaybackUrlError, redact_url


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


def _argv(speed=1, maxrate=8000, transport="udp"):
    return _build_ffmpeg_argv(
        ffbin="ffmpeg",
        rtsp_url=_URL,
        vcodec="libx264",
        keyframe_seconds=0.5,
        speed=speed,
        maxrate_kbps=maxrate,
        transport=transport,
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


def test_argv_transport_tcp_overrides_default():
    """Transport toggle (Clear = TCP): the ffmpeg input uses -rtsp_transport tcp."""
    argv = _argv(transport="tcp")
    assert "-rtsp_transport" in argv
    assert argv[argv.index("-rtsp_transport") + 1] == "tcp"


def test_argv_drops_audio():
    # Audio is dropped (-an): the MSE init MIME is video-only, so an AAC track
    # makes Chrome reject the append (CHUNK_DEMUXER_ERROR). Playback is muted.
    argv = _argv()
    assert "-an" in argv
    assert "-c:a" not in argv


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


class _FakeWriter:
    """Stdin stand-in: records writes so tests can assert the graceful 'q' quit."""

    def __init__(self):
        self.writes = []

    def is_closing(self):
        return False

    def write(self, data):
        self.writes.append(data)

    async def drain(self):
        pass


class _FakeProc:
    def __init__(self):
        self.pid = 4242
        self.returncode = None
        self.stdin = _FakeWriter()
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
        # Graceful path: returns promptly (as if ffmpeg quit on 'q').
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
        # Graceful quit: 'q' sent on stdin (→ RTSP TEARDOWN), no hard kill needed
        # because the fake proc "exits" promptly.
        assert fake.stdin.writes == [b"q"]
        assert fake.kill_count == 0
        assert drain.cancelled() or drain.done()
        assert stderr.cancelled() or stderr.done()

        # Second close must be a no-op (no crash, no extra 'q' on a None proc).
        await sess.close()
        assert sess.state == SessionState.CLOSED
        assert fake.stdin.writes == [b"q"]


async def test_close_hard_kills_when_graceful_quit_times_out(monkeypatch):
    """If ffmpeg doesn't exit after 'q', close() falls back to a hard kill."""
    monkeypatch.setattr(
        "app.services.playback.session._GRACEFUL_QUIT_SECONDS", 0.05
    )

    class _StubbornProc(_FakeProc):
        async def wait(self):
            # Never exits on its own; only kill() sets returncode.
            while self.returncode is None:
                await asyncio.sleep(0.01)
            return self.returncode

    sess = _session()
    fake = _StubbornProc()
    with patch(
        "app.services.playback.session.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake),
    ), patch(
        "app.services.playback.session.get_active_lockout",
        new=AsyncMock(return_value=None),
    ):
        await sess.open(1_700_000_000)
        await sess.close()
        assert fake.stdin.writes == [b"q"]   # graceful attempted first
        assert fake.kill_count >= 1          # then hard-killed on timeout
        assert sess._proc is None


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


def test_footage_now_frozen_while_paused():
    """HIGH-1: while PAUSED, footage_now() is frozen at t0 even as the wall clock
    advances (without the freeze it would keep marching forward)."""
    sess = _session()
    sess.t0 = 1000
    sess._wall_start = 100.0
    sess.speed = 2
    sess.state = SessionState.PAUSED
    with patch("app.services.playback.session.time.monotonic", return_value=100_000.0):
        assert sess.footage_now() == 1000  # frozen at t0, not 1000+huge*speed


async def test_pause_freezes_and_play_resumes_at_frozen_epoch(monkeypatch):
    """HIGH-1: pause captures the live footage epoch, then it stays frozen while
    the wall clock advances — so resume() would start at the pause position, not
    overshoot by the pause duration."""
    sess = _session()
    fake = _FakeProc()
    clock = {"t": 1000.0}
    monkeypatch.setattr(
        "app.services.playback.session.time.monotonic", lambda: clock["t"]
    )
    with patch(
        "app.services.playback.session.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake),
    ), patch(
        "app.services.playback.session.get_active_lockout",
        new=AsyncMock(return_value=None),
    ):
        await sess.open(1_700_000_000)  # t0=1.7e9, _wall_start=1000
        # Advance the wall clock 50 s while PLAYING, then pause: the frozen
        # position is the live footage epoch at that instant (50 s at 1×).
        clock["t"] = 1050.0
        await sess.pause()
        frozen = sess.t0
        assert frozen == 1_700_000_050
        assert sess.state == SessionState.PAUSED
        # Wall clock keeps advancing during the pause…
        clock["t"] = 9999.0
        # …but the position stays frozen (would be 1.7e9+~9000 without the fix).
        assert sess.footage_now() == frozen
        await sess.close()


async def test_forward_seek_recomputes_valid_window(monkeypatch):
    """HIGH-3: a forward seek PAST the window opened at connect time recomputes
    clip_end_epoch so the RTSP window is always start<end (never start>end,
    which starves the Dahua stream)."""
    sess = _session()  # clip_end_epoch defaults to 2_000_000_000 at construction
    procs = [_FakeProc(), _FakeProc()]
    fixed_now = 1_700_100_000
    monkeypatch.setattr(
        "app.services.playback.session.time.time", lambda: fixed_now
    )
    with patch(
        "app.services.playback.session.asyncio.create_subprocess_exec",
        new=AsyncMock(side_effect=procs),
    ), patch(
        "app.services.playback.session.get_active_lockout",
        new=AsyncMock(return_value=None),
    ):
        await sess.open(1_700_000_000)
        # After open the window end is capped at now (min(start+86400, now)).
        assert sess.clip_end_epoch == 1_700_086_400
        # Seek FORWARD to an epoch LATER than that just-opened window end — the
        # exact bug that used to build starttime>endtime.
        await sess.seek(1_700_090_000)
        assert sess.t0 == 1_700_090_000
        # Recomputed: min(1_700_090_000+86400, now) = now.
        assert sess.clip_end_epoch == fixed_now
        assert sess.t0 < sess.clip_end_epoch  # valid, non-inverted window
        await sess.close()


async def test_forward_seek_to_now_clamps_to_valid_window(monkeypatch):
    """R2: a forward seek to >= now is CLAMPED to just-before-now so the RTSP
    window stays start<end.  Without the clamp, start>=now → clip_end=now →
    build_playback_url raises and the session wedges in SEEKING with _proc=None,
    holding its NvrBudget slot until max_lifetime."""
    sess = _session()
    procs = [_FakeProc(), _FakeProc()]
    fixed_now = 1_700_100_000
    monkeypatch.setattr(
        "app.services.playback.session.time.time", lambda: fixed_now
    )
    with patch(
        "app.services.playback.session.asyncio.create_subprocess_exec",
        new=AsyncMock(side_effect=procs),
    ), patch(
        "app.services.playback.session.get_active_lockout",
        new=AsyncMock(return_value=None),
    ):
        await sess.open(1_700_000_000)
        # Seek to exactly `now` — would build an inverted window without clamping.
        await sess.seek(fixed_now)
        assert sess.state == SessionState.PLAYING  # NOT wedged in SEEKING
        assert sess._proc is not None
        assert sess.t0 == fixed_now - 1          # clamped to < now
        assert sess.t0 < sess.clip_end_epoch     # valid, non-inverted window
        await sess.close()


async def test_forward_seek_far_future_clamps(monkeypatch):
    """R2: even a seek WAY past now is clamped to now-1 (latest available)."""
    sess = _session()
    procs = [_FakeProc(), _FakeProc()]
    fixed_now = 1_700_100_000
    monkeypatch.setattr(
        "app.services.playback.session.time.time", lambda: fixed_now
    )
    with patch(
        "app.services.playback.session.asyncio.create_subprocess_exec",
        new=AsyncMock(side_effect=procs),
    ), patch(
        "app.services.playback.session.get_active_lockout",
        new=AsyncMock(return_value=None),
    ):
        await sess.open(1_700_000_000)
        await sess.seek(fixed_now + 999_999)
        assert sess.state == SessionState.PLAYING
        assert sess.t0 == fixed_now - 1
        assert sess.t0 < sess.clip_end_epoch
        await sess.close()


async def test_seek_url_error_closes_session_not_wedged(monkeypatch):
    """R2 defense-in-depth: if the URL STILL can't be built during seek, the
    session transitions to ERROR and close()s (freeing its budget slot) and
    re-raises — it must NOT be left wedged in SEEKING with no proc."""
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

        # Force the URL build to fail regardless of the clamp.
        def _boom(_start_epoch: int) -> str:
            raise PlaybackUrlError("forced")

        monkeypatch.setattr(sess, "_build_url", _boom)
        with pytest.raises(PlaybackUrlError):
            await sess.seek(1_700_000_500)
        assert sess.state == SessionState.CLOSED  # closed, not wedged in SEEKING
        assert sess._proc is None


async def test_reaper_closes_paused_session_past_idle(monkeypatch):
    """HIGH-4: a session paused longer than idle_timeout is reaped (closed +
    deregistered) so its budget slot frees — keepalive no longer refreshes
    _paused_at, so the idle clock keeps counting."""
    import app.services.playback.session as sm

    sm._active_sessions.clear()
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
        await sess.pause()
        assert sess.session_id in sm._active_sessions
        # Simulate a long pause: paused-at well beyond the idle timeout.
        sess._paused_at = sm.time.monotonic() - 100

        # Run exactly ONE reaper pass, then cancel on the next sleep.
        calls = {"n": 0}
        real_sleep = asyncio.sleep

        async def _fake_sleep(_n):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise asyncio.CancelledError
            await real_sleep(0)

        monkeypatch.setattr(
            "app.services.playback.session.asyncio.sleep", _fake_sleep
        )
        with pytest.raises(asyncio.CancelledError):
            await sm._reaper_loop(idle_timeout=1, max_lifetime=10_000_000)

        assert sess.state == SessionState.CLOSED
        assert sess.session_id not in sm._active_sessions
    sm._active_sessions.clear()


# ── Contract #12: credential redaction in ffmpeg stderr (Task 7 review fix) ──


def test_redact_url_strips_credentials_standalone():
    """redact_url on a bare RTSP URL replaces user:pw with ***."""
    raw = "rtsp://admin:pa%40ss%2Aword@10.0.0.1:554/cam/playback"
    out = redact_url(raw)
    assert "admin" not in out
    assert "pa%40ss%2Aword" not in out
    assert "***" in out
    assert "10.0.0.1" in out  # host preserved


def test_redact_url_handles_embedded_url_in_stderr_line():
    """redact_url also redacts when the URL is embedded mid-line (ffmpeg error text)."""
    raw = "rtsp://admin:secret@1.2.3.4:554/cam/playback?channel=1: 401 Unauthorized"
    out = redact_url(raw)
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
