"""PlaybackSession — the ffmpeg subprocess lifecycle for NVR playback.

One ``PlaybackSession`` owns exactly one ffmpeg process pulling
``/cam/playback`` over RTSP/UDP, re-encoding to fragmented MP4 (fMP4) on
stdout, and draining fragments into a bounded async ring buffer that the
WebSocket endpoint (Task 8) forwards to the browser.

Design contracts honoured here (see the binding-contracts header in the task
brief):

* **#10 ffmpeg I/O** — UDP RTSP input, fMP4 output (``-f mp4`` +
  ``frag_keyframe+empty_moov+default_base_moof`` on ``pipe:1``), audio → AAC.
  ``argv`` is always a **list** (no shell).
* **#11 No orphan ffmpeg** — ``close()`` kills the process, ``await``s it, and
  cancels the drain + stderr tasks; it is idempotent.  On Windows the process
  is assigned to a Job Object (kill-on-close) on spawn.  The lifespan shutdown
  closes every entry in ``_active_sessions``.
* **#11 Back-pressure** — the stdout reader uses ``put_nowait`` and drops the
  OLDEST chunk when the ring is full.  It **never** ``await``s ``ring.put`` —
  blocking the reader would stall ffmpeg's RTSP pipeline.
* **#12 Credential hygiene** — the password and the credentialed RTSP URL are
  never logged; URLs are redacted to ``***`` before any log line.
* **#13 Speed = backend-owned** — server-side frame decimation; the speed
  filter appears in ``argv`` only when ``speed > 1``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator

from app.services.lockouts import get_active_lockout, record_lockout
from app.services.playback.url_builder import (
    SPEED_WHITELIST,
    build_playback_url,
    epoch_to_nvr_local,
    validate_speed,
)

log = logging.getLogger("dss.playback.session")

__all__ = [
    "PlaybackSession",
    "SessionState",
    "footage_epoch_at",
    "start_reaper",
    "stop_reaper",
]

# Bytes per stdout read. fMP4 fragments are small; 64 KiB keeps the reader
# responsive without spinning on tiny reads.
_READ_CHUNK = 64 * 1024

# ffmpeg exit codes we treat as clean shutdowns (we killed it).
_CLEAN_EXIT_CODES = (0, -15, -9)


class SessionState(str):
    IDLE = "idle"
    LOADING = "loading"
    PLAYING = "playing"
    PAUSED = "paused"
    SEEKING = "seeking"
    CLOSED = "closed"
    ERROR = "error"


def _redact_url(url: str) -> str:
    """Replace the credentials in an ``rtsp://user:pw@host`` URL with ``***``.

    Defence-in-depth: callers must never log the credentialed URL, but if one
    slips through this guarantees the password never lands in a log record
    (Contract #12).
    """
    return re.sub(r"(rtsp://)[^@/]*@", r"\1***@", url)


def footage_epoch_at(t0: int, wall_start: float, speed: int, now_wall: float) -> int:
    """Pure function: current footage epoch given session start state and speed.

    Args:
        t0:         Footage epoch (UTC) at the keyframe where ffmpeg started.
        wall_start: Monotonic time when ffmpeg started (``time.monotonic()``).
        speed:      Playback speed multiplier (1, 2, 4, 8).
        now_wall:   Current monotonic time.

    Returns:
        UTC epoch seconds of the current footage position.

    The WS heartbeat uses this to emit ``{type:"clock"}`` (Contract #3).
    """
    return t0 + int((now_wall - wall_start) * speed)


def _build_ffmpeg_argv(
    ffbin: str,
    rtsp_url: str,
    vcodec: str,
    keyframe_seconds: float,
    speed: int,
    maxrate_kbps: int,
) -> list[str]:
    """Build the ffmpeg argv for playback (list, no shell).

    Output: fMP4 on stdout (``pipe:1``).  Audio: transcoded to AAC.
    Speed > 1: an I-frame-stride filter drops non-keyframe frames and remaps
    PTS so the output plays at realtime pace on the client (each output second
    covers ``speed`` seconds of footage).

    Note: the exact ``-vf`` filter for speed>1 must be validated during
    integration testing.  The signature and structure are specced here; the
    runtime ffmpeg behaviour is not unit-testable.
    """
    argv = [
        ffbin,
        "-nostdin", "-loglevel", "error",
        "-rtsp_transport", "udp",
        "-i", rtsp_url,
    ]
    # Video re-encode.
    argv += [
        "-c:v", vcodec,
        "-force_key_frames", f"expr:gte(t,n_forced*{keyframe_seconds})",
        "-bf", "0",
        "-pix_fmt", "yuv420p",
    ]
    if maxrate_kbps > 0:
        argv += ["-maxrate", f"{maxrate_kbps}k", "-bufsize", f"{maxrate_kbps}k"]
    # Speed filter (server-side frame decimation; Contract #13).
    if speed > 1:
        # INTEGRATION NOTE: validate this filter on the real NVR stream.
        # Selects every (speed)th frame and remaps timestamps so the client
        # sees continuous realtime media time while each second covers `speed`
        # seconds of footage time.
        argv += ["-vf", f"select=not(mod(n\\,{speed})),setpts=N/(FRAME_RATE*TB)"]
        argv += ["-vsync", "vfr"]
    # Audio: transcode to AAC (handles G.711, G.726 — V7 unmeasured on new NVR).
    argv += ["-c:a", "aac"]
    # fMP4 fragmented output on stdout.
    argv += [
        "-f", "mp4",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "pipe:1",
    ]
    return argv


def _assign_job_object(pid: int) -> None:
    """Assign process PID to a Windows Job Object with kill-on-close.

    No-op on non-Windows.  Uses ctypes (no pywin32 dep).  On error: logs a
    warning and continues — the session still works, but an orphan ffmpeg on
    crash becomes possible (Contract #11).
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        import ctypes.wintypes

        PROCESS_ALL_ACCESS = 0x1F0FFF
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        JobObjectExtendedLimitInformation = 9

        kernel32 = ctypes.windll.kernel32
        proc_handle = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        if not proc_handle:
            log.warning("Job Object: OpenProcess(%d) failed", pid)
            return
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            kernel32.CloseHandle(proc_handle)
            log.warning("Job Object: CreateJobObjectW failed")
            return

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.wintypes.DWORD),
                ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                ("PriorityClass", ctypes.wintypes.DWORD),
                ("SchedulingClass", ctypes.wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                (f, ctypes.c_uint64)
                for f in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
                )
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        kernel32.AssignProcessToJobObject(job, proc_handle)
        kernel32.CloseHandle(proc_handle)
        # Do NOT close `job` — closing it removes the kill-on-close protection.
        # It leaks intentionally; the OS reclaims it when the Python process exits.
        log.debug("Job Object assigned to ffmpeg PID %d", pid)
    except Exception:  # noqa: BLE001
        log.warning("Job Object assignment failed for PID %d", pid, exc_info=True)


# Detects an RTSP 401 / auth failure in ffmpeg stderr → record a lockout so we
# don't keep hammering an NVR that has banned us (Contract / integration #476).
_AUTH_FAIL_RE = re.compile(r"\b401\b|unauthorized|authentication failed", re.IGNORECASE)


async def _drain_stderr(
    proc: asyncio.subprocess.Process, session_id: str, nvr_ip: str
) -> None:
    """Drain ffmpeg stderr (bounded ring of 200 lines); log on non-zero exit.

    If the stderr stream indicates an auth failure (RTSP 401), record an IP
    lockout so the session manager backs off the NVR.
    """
    lines: list[str] = []
    auth_failed = False
    while True:
        line = await proc.stderr.readline()
        if not line:
            break
        text = line.decode(errors="replace").rstrip()
        if _AUTH_FAIL_RE.search(text):
            auth_failed = True
        lines.append(text)
        if len(lines) > 200:
            lines.pop(0)  # ring: keep last 200 lines
    rc = await proc.wait()
    if auth_failed:
        # Mirror the NVR's IP ban so we stop retrying for the cooldown window.
        try:
            await record_lockout(nvr_ip)
            log.warning(
                "playback ffmpeg session=%s hit RTSP auth failure; lockout recorded for %s",
                session_id, nvr_ip,
            )
        except Exception:  # noqa: BLE001
            log.warning("failed to record lockout for %s", nvr_ip, exc_info=True)
    if rc not in _CLEAN_EXIT_CODES:
        log.error(
            "playback ffmpeg session=%s exited rc=%d stderr:\n%s",
            session_id, rc, "\n".join(lines[-20:]),
        )
    else:
        log.debug("playback ffmpeg session=%s exited rc=%d", session_id, rc)


@dataclass
class PlaybackSession:
    """Owns one ffmpeg process for a playback session.

    Lifecycle:
      1. Instantiate with NVR credentials + clip bounds.
      2. ``await open(start_epoch)`` to spawn ffmpeg and begin draining.
      3. Iterate ``drain_queue()`` to receive fMP4 byte chunks.
      4. ``seek(epoch)`` / ``set_speed(speed)`` / ``pause()`` / ``resume()``.
      5. ``await close()`` on WS disconnect or idle timeout — always.

    No orphan ffmpeg: ``close()`` kills the process and cancels the drain +
    stderr tasks.  Windows: the process is assigned to a Job Object
    (kill-on-close) on spawn.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    nvr_id: str = ""
    nvr_ip: str = ""
    rtsp_port: int = 554
    rtsp_user: str = ""
    rtsp_pw: str = ""          # decrypted, never logged
    channel: int = 1
    tz_offset_minutes: int = 0

    # Clip end boundary — ffmpeg end time; updated on seek.
    clip_end_epoch: int = 0

    # ffmpeg knobs (passed from settings by the WS handler).
    ffbin: str = "ffmpeg"
    vcodec: str = "libx264"
    keyframe_seconds: float = 0.5
    maxrate_kbps: int = 8000
    ring_buffer_chunks: int = 32

    # Runtime state.
    state: str = SessionState.IDLE
    speed: int = 1
    t0: int = 0                 # footage epoch of current ffmpeg start
    _wall_start: float = 0.0    # monotonic when current ffmpeg started
    _proc: asyncio.subprocess.Process | None = None
    _drain_task: asyncio.Task | None = None
    _stderr_task: asyncio.Task | None = None
    _ring: asyncio.Queue | None = None
    _started_at: float = field(default_factory=time.monotonic)
    _paused_at: float = 0.0
    _closing: bool = False

    def __post_init__(self) -> None:
        if self._ring is None:
            self._ring = asyncio.Queue(maxsize=self.ring_buffer_chunks)

    # ── public API ─────────────────────────────────────────────────────────

    async def open(self, start_epoch: int) -> None:
        """Spawn ffmpeg at ``start_epoch``.  Sets ``t0``; begins draining.

        Raises if the NVR IP is currently locked out (mirrors the firmware ban
        so we don't re-trigger it).
        """
        lock = await get_active_lockout(self.nvr_ip)
        if lock is not None:
            self.state = SessionState.ERROR
            raise RuntimeError(f"NVR {self.nvr_id} is locked out; not spawning ffmpeg")
        self.state = SessionState.LOADING
        await self._spawn(start_epoch)
        self.state = SessionState.PLAYING
        _active_sessions[self.session_id] = self

    async def seek(self, footage_epoch: int) -> None:
        """Respawn ffmpeg at ``footage_epoch``.  Updates ``t0``.

        Caller sends a ``reinit`` to the client afterward.
        """
        self.state = SessionState.SEEKING
        await self._kill_proc()
        await self._spawn(footage_epoch)
        self.state = SessionState.PLAYING

    async def set_speed(self, speed: int) -> None:
        """Change speed (respawn at the current footage position).

        Validates against ``SPEED_WHITELIST``.
        """
        validate_speed(speed)
        if speed == self.speed and self._proc is not None:
            return
        resume_at = self.footage_now() if self._proc is not None else self.t0
        self.speed = speed
        if self._proc is not None:
            self.state = SessionState.SEEKING
            await self._kill_proc()
            await self._spawn(resume_at)
            self.state = SessionState.PLAYING

    async def pause(self) -> None:
        """Kill ffmpeg, keep the session alive.  State → PAUSED."""
        if self.state == SessionState.PAUSED:
            return
        # Freeze the footage position before tearing down the timing base.
        self.t0 = self.footage_now() if self._proc is not None else self.t0
        await self._kill_proc()
        self.state = SessionState.PAUSED
        self._paused_at = time.monotonic()

    async def resume(self, footage_epoch: int) -> None:
        """Respawn ffmpeg from ``footage_epoch`` after a pause.  State → LOADING."""
        self.state = SessionState.LOADING
        await self._spawn(footage_epoch)
        self.state = SessionState.PLAYING

    async def close(self) -> None:
        """Terminate ffmpeg, cancel the drain + stderr tasks.  Idempotent.

        Safe to call multiple times and from the reaper concurrently with a WS
        teardown.  Guarantees no orphan ffmpeg: kills the process and
        ``await``s it before returning (Contract #11).
        """
        if self._closing or self.state == SessionState.CLOSED:
            self.state = SessionState.CLOSED
            return
        self._closing = True
        await self._kill_proc()
        await self._cancel_tasks()
        self.state = SessionState.CLOSED
        _active_sessions.pop(self.session_id, None)

    def footage_now(self) -> int:
        """Current footage epoch (UTC) based on wall clock + speed."""
        return footage_epoch_at(self.t0, self._wall_start, self.speed, time.monotonic())

    async def drain_queue(self) -> AsyncIterator[bytes]:
        """Yield fMP4 byte chunks from the ring buffer until CLOSED or ERROR."""
        while self.state not in (SessionState.CLOSED, SessionState.ERROR):
            try:
                chunk = await asyncio.wait_for(self._ring.get(), timeout=1.0)
                yield chunk
            except asyncio.TimeoutError:
                continue

    # ── internals ────────────────────────────────────────────────────────────

    def _build_url(self, start_epoch: int) -> str:
        start_dt = epoch_to_nvr_local(start_epoch, self.tz_offset_minutes)
        end_dt = epoch_to_nvr_local(self.clip_end_epoch, self.tz_offset_minutes)
        return build_playback_url(
            ip=self.nvr_ip,
            rtsp_port=self.rtsp_port,
            user=self.rtsp_user,
            pw=self.rtsp_pw,
            channel=self.channel,
            start=start_dt,
            end=end_dt,
        )

    async def _spawn(self, start_epoch: int) -> None:
        """Spawn ffmpeg at ``start_epoch`` and start the drain + stderr tasks."""
        rtsp_url = self._build_url(start_epoch)
        argv = _build_ffmpeg_argv(
            ffbin=self.ffbin,
            rtsp_url=rtsp_url,
            vcodec=self.vcodec,
            keyframe_seconds=self.keyframe_seconds,
            speed=self.speed,
            maxrate_kbps=self.maxrate_kbps,
        )
        # Credential hygiene: log only the redacted URL (Contract #12).
        log.info(
            "playback session=%s spawning ffmpeg ch=%d t0=%d speed=%dx url=%s",
            self.session_id, self.channel, start_epoch, self.speed,
            _redact_url(rtsp_url),
        )
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._proc = proc
        self.t0 = start_epoch
        self._wall_start = time.monotonic()
        _assign_job_object(proc.pid)
        self._drain_task = asyncio.create_task(
            self._drain_loop(proc), name=f"playback-drain-{self.session_id}"
        )
        self._stderr_task = asyncio.create_task(
            _drain_stderr(proc, self.session_id, self.nvr_ip),
            name=f"playback-stderr-{self.session_id}",
        )

    def _enqueue(self, chunk: bytes) -> None:
        """Push a chunk into the ring; drop the OLDEST if full.  Never blocks.

        The stdout reader calls this — it MUST NOT ``await`` ``ring.put`` or a
        slow WS client would stall ffmpeg's RTSP pipeline (Contract #11).
        """
        try:
            self._ring.put_nowait(chunk)
        except asyncio.QueueFull:
            try:
                self._ring.get_nowait()  # drop oldest
            except asyncio.QueueEmpty:
                pass
            try:
                self._ring.put_nowait(chunk)
            except asyncio.QueueFull:
                pass
            log.debug("playback session=%s ring full — dropped oldest chunk", self.session_id)

    async def _drain_loop(self, proc: asyncio.subprocess.Process) -> None:
        """Read fMP4 bytes off ffmpeg stdout into the ring until EOF."""
        try:
            while True:
                chunk = await proc.stdout.read(_READ_CHUNK)
                if not chunk:
                    break
                self._enqueue(chunk)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.warning(
                "playback session=%s drain loop error", self.session_id, exc_info=True
            )

    async def _kill_proc(self) -> None:
        """Kill the current ffmpeg process and ``await`` its exit (no orphan)."""
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            except Exception:  # noqa: BLE001
                log.warning(
                    "playback session=%s kill failed", self.session_id, exc_info=True
                )
        try:
            await proc.wait()
        except Exception:  # noqa: BLE001
            pass

    async def _cancel_tasks(self) -> None:
        """Cancel and await the drain + stderr tasks."""
        for attr in ("_drain_task", "_stderr_task"):
            task = getattr(self, attr)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            setattr(self, attr, None)


# ── Idle + max-lifetime reaper ─────────────────────────────────────────────

_active_sessions: dict[str, "PlaybackSession"] = {}


async def _reaper_loop(idle_timeout: int, max_lifetime: int) -> None:
    """Background task: close idle (paused too long) and over-age sessions."""
    while True:
        await asyncio.sleep(10)
        now = time.monotonic()
        for sid, sess in list(_active_sessions.items()):
            try:
                if (
                    sess.state == SessionState.PAUSED
                    and now - sess._paused_at > idle_timeout
                ):
                    log.info("Reaper: closing idle session %s", sid)
                    await sess.close()
                    continue
                if now - sess._started_at > max_lifetime:
                    log.info("Reaper: closing over-age session %s", sid)
                    await sess.close()
            except Exception:  # noqa: BLE001
                log.warning("Reaper: error closing session %s", sid, exc_info=True)


_reaper_task: asyncio.Task | None = None


def start_reaper(idle_timeout: int, max_lifetime: int) -> None:
    global _reaper_task
    _reaper_task = asyncio.create_task(
        _reaper_loop(idle_timeout, max_lifetime), name="playback-reaper"
    )


async def stop_reaper() -> None:
    global _reaper_task
    if _reaper_task:
        _reaper_task.cancel()
        try:
            await _reaper_task
        except asyncio.CancelledError:
            pass
        _reaper_task = None
