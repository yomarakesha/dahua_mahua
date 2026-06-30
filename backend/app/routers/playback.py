"""Playback endpoints — recording index and month availability per NVR channel.

GET /playback/{nvr_id}/{channel}/index?date=YYYY-MM-DD
    Returns the day's clip list with timestamps as UTC epoch seconds.

GET /playback/{nvr_id}/{channel}/availability?month=YYYY-MM
    Returns which calendar days in the month have recordings and the epoch of
    the oldest recording (null when the month is empty).  Used by the frontend
    day-picker to grey out empty or over-retention days.

NVR-local naive datetimes are shifted to UTC using *playback_tz_offset_minutes*
from settings (Phase-1 source; live NVR-clock querying is wired by a later
spike task).

Results are cached in-process for 120 s per (nvr_id, channel, date|month) key
so that the NVR's mediaFileFind CGI isn't hammered by concurrent viewers.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid as _uuid
from collections import deque
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from app.crypto import decrypt_password
from app.deps import CurrentUser, SessionDep, user_can_access_camera
from app.models import Camera, Nvr, User
from app.security import decode_token
from app.services.lockouts import get_active_lockout
from app.services.playback.index_parser import Clip
from app.services.playback.media_find import MediaFindError, find_clips
from app.services.playback.nvr_budget import BudgetExhausted, get_budget
from app.services.playback.session import (
    PlaybackSession,
    SessionState,
    _active_sessions,
)
from app.services.playback.url_builder import (
    PlaybackUrlError,
    validate_channel,
    validate_footage_epoch,
    validate_speed,
)
from app.settings import get_settings

log = logging.getLogger("dss.playback")

router = APIRouter(prefix="/playback", tags=["playback"])

# ── In-process cache ──────────────────────────────────────────────────────────
#   keyed (nvr_id, channel, date_str) → (inserted_at_monotonic, result_dict)

_CACHE_TTL = 120.0  # seconds
_cache: dict[tuple, tuple[float, dict]] = {}


def _cache_get(key: tuple) -> dict | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    inserted_at, result = entry
    if time.monotonic() - inserted_at < _CACHE_TTL:
        return result
    del _cache[key]
    return None


def _cache_set(key: tuple, result: dict) -> None:
    _cache[key] = (time.monotonic(), result)


# ── Pure time helpers (unit-testable) ─────────────────────────────────────────


def nvr_naive_to_epoch(dt: datetime, tz_offset_minutes: int) -> int:
    """Convert a naive NVR-local datetime to UTC epoch seconds.

    The NVR clock runs at UTC + tz_offset_minutes, so:
        UTC = NVR-local − tz_offset_minutes
    """
    utc_dt = dt - timedelta(minutes=tz_offset_minutes)
    return int(utc_dt.replace(tzinfo=timezone.utc).timestamp())


def day_to_epochs(date_str: str, tz_offset_minutes: int) -> tuple[int, int]:
    """Return (day_start_epoch, day_end_epoch) for *date_str* in the NVR's
    local timezone.

    *date_str* must be ``YYYY-MM-DD``.  The returned epochs bound the half-open
    interval ``[day_start, day_end)`` in UTC, suitable for ``find_clips``.
    """
    day_start = datetime.strptime(date_str, "%Y-%m-%d")
    day_end = day_start + timedelta(days=1)
    return (
        nvr_naive_to_epoch(day_start, tz_offset_minutes),
        nvr_naive_to_epoch(day_end, tz_offset_minutes),
    )


# ── Availability helper (pure — unit-testable) ────────────────────────────────


def clips_to_day_strings(clips: list[Clip]) -> list[str]:
    """Return a sorted list of distinct local date strings touched by *clips*.

    A clip whose ``start`` and ``end`` span midnight contributes **both** the
    start day *and* the end day (and any days in between for very long clips).
    The input datetimes must be NVR-local naive — no tz conversion is needed
    because they are already in local time.
    """
    days: set[str] = set()
    one_day = timedelta(days=1)
    for clip in clips:
        start_date = clip.start.date()
        end_date = clip.end.date()
        d = start_date
        while d <= end_date:
            days.add(d.strftime("%Y-%m-%d"))
            d += one_day
    return sorted(days)


def _month_to_local_bounds(month_str: str) -> tuple[datetime, datetime]:
    """Return (month_start, month_end) as naive NVR-local datetimes.

    *month_end* is the last second of the month (next-month midnight − 1 s),
    mirroring the exclusive-end convention used by the index endpoint.
    """
    year = int(month_str[:4])
    month = int(month_str[5:7])
    month_start = datetime(year, month, 1, 0, 0, 0)
    if month == 12:
        next_month_start = datetime(year + 1, 1, 1, 0, 0, 0)
    else:
        next_month_start = datetime(year, month + 1, 1, 0, 0, 0)
    month_end = next_month_start - timedelta(seconds=1)
    return month_start, month_end


# ── Endpoint ──────────────────────────────────────────────────────────────────

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


@router.get("/{nvr_id}/{channel}/index")
async def recording_index(
    nvr_id: str,
    channel: int,
    date: str,
    session: SessionDep,
    user: CurrentUser,
) -> dict:
    """Recording index for one NVR channel on a given calendar day.

    Returns UTC epoch seconds for the day boundaries and every clip.
    Results are cached for 120 s per (nvr_id, channel, date) tuple.
    """
    # ── Input validation (400) ────────────────────────────────────────────────
    if channel < 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "channel must be a positive integer",
        )
    if not _DATE_RE.match(date):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "date must be in YYYY-MM-DD format",
        )
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "date is not a valid calendar date",
        ) from None

    # ── NVR + Camera lookup — no SSRF: hosts always come from the DB rows ────
    nvr = (
        await session.execute(select(Nvr).where(Nvr.id == nvr_id))
    ).scalar_one_or_none()
    camera = (
        await session.execute(
            select(Camera).where(Camera.nvr_id == nvr_id, Camera.channel == channel)
        )
    ).scalar_one_or_none()
    if nvr is None or camera is None or not user_can_access_camera(user, camera):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording source not found")

    # ── Cache check ───────────────────────────────────────────────────────────
    cache_key = (nvr_id, channel, date)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # ── Compute day boundaries in NVR-local time ──────────────────────────────
    settings = get_settings()
    tz_offset = settings.playback_tz_offset_minutes
    day_start_epoch, day_end_epoch = day_to_epochs(date, tz_offset)

    day_start_local = datetime.strptime(date, "%Y-%m-%d")
    day_end_local = day_start_local + timedelta(days=1)

    # ── Fetch from NVR ────────────────────────────────────────────────────────
    # nvr.port is the RTSP port (default 554); Dahua HTTP CGI is on port 80,
    # matching the convention in camera_import.py (http_port=80).
    password = decrypt_password(nvr.rtsp_password_encrypted)
    try:
        clips = await find_clips(
            nvr.ip,
            80,  # HTTP CGI port, not RTSP port
            nvr.rtsp_username,
            password,
            channel=channel,
            start=day_start_local,
            # Subtract 1 s so the inclusive end boundary stays within the day
            # (next-midnight 00:00:00 could pull a recording from the next day).
            end=day_end_local - timedelta(seconds=1),
        )
    except MediaFindError as exc:
        log.warning(
            "NVR %s ch%d %s index failed: %s",
            nvr_id, channel, date, exc,
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "NVR recording index unavailable",
        ) from exc

    result: dict = {
        "tz_offset_minutes": tz_offset,
        "day_start_epoch": day_start_epoch,
        "day_end_epoch": day_end_epoch,
        "clips": [
            {
                "start_epoch": nvr_naive_to_epoch(c.start, tz_offset),
                "end_epoch": nvr_naive_to_epoch(c.end, tz_offset),
                "type": c.type,
                "stream": c.stream,
            }
            for c in clips
        ],
    }

    _cache_set(cache_key, result)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Playback WebSocket — /playback/{nvr_id}/{channel}/stream  (Task 8)
# ══════════════════════════════════════════════════════════════════════════════
#
# Persistent control channel: JWT-authenticated handshake (BEFORE accept),
# per-camera RBAC (Contract #1), per-NVR/global budget (Task 6), one
# PlaybackSession (Task 7), and a small JSON control protocol multiplexed with
# binary fMP4 fragments.
#
# Close codes (Contract #2):
#   4001 — unauthenticated (missing/bad token, unknown/inactive user)
#   4003 — forbidden (no per-camera access)
#   4004 — NVR/camera not found or disabled (or a bad channel/start target)
#   4429 — resource exhausted (lockout / rate-limit / NVR or global budget cap)
#
# Credential hygiene (Contract #12): the NVR password and credentialed RTSP URL
# never appear in any WS payload or log line.

# MVP hard-codes the libx264 Baseline MIME (Contract #14); validate in integration.
_INIT_CODEC = "avc1.42E01E"

# How long the fragment-sender waits for the pinned fMP4 init segment after a
# (re)spawn before giving up and streaming fragments without it.
_INIT_SEGMENT_TIMEOUT = 10.0

# Per-user session-open rate limiter (Task 8 / spec §7): {user_id: deque[monotonic]}.
_RATE_WINDOW = 60.0
_rate_limits: dict[str, deque] = {}


def _check_rate_limit(user_id: str) -> bool:
    """Sliding 60 s window of session-open attempts per user.

    Returns ``True`` if the attempt is within budget (and records it), ``False``
    if the user has exceeded ``settings.playback_rate_limit_per_minute``.
    """
    limit = get_settings().playback_rate_limit_per_minute
    now = time.monotonic()
    dq = _rate_limits.setdefault(user_id, deque())
    while dq and now - dq[0] > _RATE_WINDOW:
        dq.popleft()
    if len(dq) >= limit:
        return False
    dq.append(now)
    return True


def _classify_session_end(returncode: int | None) -> str:
    """Decide clean end-of-stream vs crash from ffmpeg's exit code (Task-8 #2).

    The session does NOT self-transition to a terminal state when ffmpeg exits
    on its own, so the WS layer makes this call.  A clean exit (``rc == 0``) is
    an EOF (end of the requested recording span); anything else — a non-zero
    code or a signal death we did not initiate — is treated as a crash.

    Pure + mockable: unit-tested without a live NVR.
    """
    return "eof" if returncode == 0 else "error"


async def _dispatch(msg: dict, sess: PlaybackSession, ws: WebSocket) -> None:
    """Dispatch one parsed client control message.

    Validators run BEFORE any session mutation (so a bad ``speed``/``seek`` is
    rejected before ffmpeg is touched).  Raises ``PlaybackUrlError`` on invalid
    input; the receive loop turns that into a sanitised ``{type:"error"}`` rather
    than tearing down the socket.
    """
    if "seek" in msg:
        epoch = validate_footage_epoch(msg["seek"])
        await sess.seek(epoch)
        await ws.send_json({"type": "reinit", "t0": sess.t0})
    elif "speed" in msg:
        speed = validate_speed(int(msg["speed"]))
        await sess.set_speed(speed)
        await ws.send_json({"type": "reinit", "t0": sess.t0})
    elif "pause" in msg:
        await sess.pause()
    elif "play" in msg:
        await sess.resume(sess.footage_now())
        await ws.send_json({"type": "reinit", "t0": sess.t0})
    elif "keepalive" in msg:
        now = time.monotonic()
        sess._last_keepalive = now
        # Keep a PAUSED session off the idle reaper's chopping block.
        if sess.state == SessionState.PAUSED:
            sess._paused_at = now
    elif "stream" in msg:
        # Contract #5: NVR records main-only; silently ignore stream switches.
        log.debug("stream switch requested (%r) — ignored (main-only NVR)", msg["stream"])
    else:
        log.warning("Unknown playback control message: %r", msg)


async def _clock_sender(ws: WebSocket, sess: PlaybackSession, interval: float) -> None:
    """Emit ``{type:"clock", wall_ts:<footage epoch>}`` while PLAYING (Contract #3)."""
    while sess.state not in (SessionState.CLOSED, SessionState.ERROR):
        await asyncio.sleep(interval)
        if sess.state == SessionState.PLAYING:
            await ws.send_json({"type": "clock", "wall_ts": sess.footage_now()})


async def _fragment_sender(ws: WebSocket, sess: PlaybackSession) -> None:
    """Stream fMP4 fragments, pinning the init segment after each (re)spawn.

    Ordering guarantee (Task-8 review #3): on every new ``_spawn_gen`` the
    pinned init segment (ftyp+moov) is sent FIRST, then media fragments from the
    back-pressure ring.  When the ring drains AND the ffmpeg process has exited
    on its own while PLAYING, we classify clean-EOF vs crash (Task-8 review #2),
    signal the client, and return — the endpoint then closes the session.

    INTEGRATION: the exact fMP4 byte-boundary alignment of the pinned init
    segment is an on-network check (no live NVR here).
    """
    last_gen = -1
    while not sess._closing and sess.state not in (SessionState.CLOSED, SessionState.ERROR):
        if sess._spawn_gen != last_gen:
            init = await sess.wait_init_segment(timeout=_INIT_SEGMENT_TIMEOUT)
            last_gen = sess._spawn_gen
            if init:
                await ws.send_bytes(init)
        try:
            chunk = await asyncio.wait_for(sess._ring.get(), timeout=0.5)
        except asyncio.TimeoutError:
            # Ring empty: has ffmpeg ended on its own (not a seek/pause kill)?
            proc = sess._proc
            if (
                sess.state == SessionState.PLAYING
                and proc is not None
                and proc.returncode is not None
                and sess._ring.empty()
            ):
                kind = _classify_session_end(proc.returncode)
                if kind == "eof":
                    await ws.send_json({"type": "eof"})
                else:
                    await ws.send_json(
                        {"type": "error", "reason": "playback stream ended unexpectedly"}
                    )
                return
            continue
        await ws.send_bytes(chunk)


async def _receive_loop(ws: WebSocket, sess: PlaybackSession) -> None:
    """Read client control JSON and dispatch it; bad input → sanitised error."""
    while True:
        msg = await ws.receive_json()  # raises WebSocketDisconnect on close
        if not isinstance(msg, dict):
            log.warning("Ignoring non-object playback control message")
            continue
        try:
            await _dispatch(msg, sess, ws)
        except PlaybackUrlError as exc:
            # str(exc) is a fixed validator message — no credentials (Contract #12).
            await ws.send_json({"type": "error", "reason": str(exc)})


async def _control_loop(ws: WebSocket, sess: PlaybackSession, clock_interval: float) -> None:
    """Run receive + clock + fragment coroutines until any one finishes.

    A client disconnect surfaces from ``_receive_loop`` as ``WebSocketDisconnect``;
    end-of-stream/crash surfaces from ``_fragment_sender``.  Whichever finishes
    first cancels the others, and we re-raise so the endpoint's ``finally`` runs.
    """
    recv = asyncio.create_task(_receive_loop(ws, sess), name="pb-recv")
    clock = asyncio.create_task(_clock_sender(ws, sess, clock_interval), name="pb-clock")
    frag = asyncio.create_task(_fragment_sender(ws, sess), name="pb-frag")
    tasks = {recv, clock, frag}
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    # Surface the first finisher's exception (e.g. WebSocketDisconnect).
    for t in done:
        exc = t.exception()
        if exc is not None:
            raise exc


@router.websocket("/{nvr_id}/{channel}/stream")
async def playback_stream(
    websocket: WebSocket,
    nvr_id: str,
    channel: int,
    session: SessionDep,
    token: str | None = None,   # JWT from ?token= (browsers can't set WS headers)
    t: int | None = None,       # initial footage epoch (UTC seconds) to play from
) -> None:
    """Persistent playback WebSocket — auth handshake + control protocol.

    The full auth + budget gauntlet runs BEFORE ``websocket.accept()`` so no
    ffmpeg is ever spawned for an unauthenticated/forbidden/over-budget client
    (Contract #2).  See the module banner for close-code semantics.
    """
    # ── 1. JWT (Contract #2: validate BEFORE accept) ──────────────────────────
    if not token:
        await websocket.close(code=4001)
        return
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        await websocket.close(code=4001)
        return
    sub = payload.get("sub")
    try:
        user_id = _uuid.UUID(str(sub))
    except (TypeError, ValueError):
        await websocket.close(code=4001)
        return
    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        await websocket.close(code=4001)
        return

    # ── 2. Validate channel + start target (before any ffmpeg op) ─────────────
    try:
        validate_channel(channel)
        start_epoch = validate_footage_epoch(t) if t is not None else None
    except PlaybackUrlError:
        await websocket.close(code=4004)
        return
    if start_epoch is None:
        # A playback session needs a start position; treat a missing one as a
        # bad target rather than accepting and immediately erroring.
        await websocket.close(code=4004)
        return

    # ── 3. Per-camera RBAC (Contract #1) ──────────────────────────────────────
    nvr = (
        await session.execute(select(Nvr).where(Nvr.id == nvr_id))
    ).scalar_one_or_none()
    camera = (
        await session.execute(
            select(Camera).where(Camera.nvr_id == nvr_id, Camera.channel == channel)
        )
    ).scalar_one_or_none()
    if nvr is None or not nvr.enabled or camera is None or not camera.enabled:
        await websocket.close(code=4004)
        return
    if not user_can_access_camera(user, camera):
        await websocket.close(code=4003)
        return

    # ── 4. Lockout (mirror the NVR firmware ban) ──────────────────────────────
    if await get_active_lockout(nvr.ip) is not None:
        await websocket.close(code=4429)
        return

    # ── 5. Per-user rate limit ────────────────────────────────────────────────
    if not _check_rate_limit(str(user.id)):
        log.warning("playback rate-limit hit user=%s nvr=%s", user.id, nvr_id)
        await websocket.close(code=4429)
        return

    # ── 6. Budget (Task 6) — acquire BEFORE accept so we can reject with 4429 ──
    budget_cm = get_budget().session(nvr_id)
    try:
        await budget_cm.__aenter__()
    except BudgetExhausted:
        await websocket.close(code=4429)
        return

    # ── 7. Accept + run the session ───────────────────────────────────────────
    settings = get_settings()
    sess: PlaybackSession | None = None
    opened_at = time.monotonic()
    try:
        await websocket.accept()
        password = decrypt_password(nvr.rtsp_password_encrypted)  # never logged
        sess = PlaybackSession(
            nvr_id=nvr_id,
            nvr_ip=nvr.ip,
            rtsp_port=nvr.port,  # Contract #9: nvr.port is the RTSP port
            rtsp_user=nvr.rtsp_username,
            rtsp_pw=password,
            channel=channel,
            tz_offset_minutes=settings.playback_tz_offset_minutes,
            # Open-ended window from the seek target; the client re-seeks to
            # move around.  Bounding the RTSP endtime is an integration concern.
            clip_end_epoch=start_epoch + 86400,
            ffbin=settings.reencode_ffmpeg_bin,
            keyframe_seconds=settings.reencode_keyframe_seconds,
            maxrate_kbps=settings.reencode_maxrate_kbps,
            ring_buffer_chunks=settings.playback_ring_buffer_chunks,
        )
        await sess.open(start_epoch)  # registers in _active_sessions
        log.info(
            "playback_start nvr=%s ch=%d user=%s session=%s",
            nvr_id, channel, user.id, sess.session_id,
        )
        await websocket.send_json({"type": "init", "t0": sess.t0, "codec": _INIT_CODEC})
        await _control_loop(websocket, sess, settings.playback_clock_interval_seconds)
    except WebSocketDisconnect:
        pass
    except BaseException:  # noqa: BLE001 — ensure cleanup on any failure path
        log.warning("playback session error nvr=%s ch=%d", nvr_id, channel, exc_info=True)
        try:
            await websocket.send_json({"type": "error", "reason": "internal error"})
        except Exception:  # noqa: BLE001
            pass
    finally:
        sid = sess.session_id if sess is not None else "?"
        if sess is not None:
            await sess.close()  # no orphan ffmpeg; removes from _active_sessions
        await budget_cm.__aexit__(None, None, None)
        log.info(
            "playback_stop nvr=%s ch=%d user=%s session=%s duration=%ds",
            nvr_id, channel, user.id, sid, int(time.monotonic() - opened_at),
        )
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass


@router.get("/{nvr_id}/{channel}/availability")
async def recording_availability(
    nvr_id: str,
    channel: int,
    month: str,
    session: SessionDep,
    user: CurrentUser,
) -> dict:
    """Which calendar days in *month* have recordings, plus the oldest recording.

    Returns::

        {
            "days_with_recordings": ["YYYY-MM-DD", ...],  # sorted, distinct
            "oldest_epoch": 1234567890 | null
        }

    A single wide ``find_clips`` call spans the entire month; the response is
    cached for 120 s per ``(nvr_id, channel, month)`` key.
    """
    # ── Input validation (400) ────────────────────────────────────────────────
    if channel < 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "channel must be a positive integer",
        )
    if not _MONTH_RE.match(month):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "month must be in YYYY-MM format",
        )
    # Validate the month value is a real calendar month (e.g. reject 2026-13)
    try:
        year_val, month_val = int(month[:4]), int(month[5:7])
        if not (1 <= month_val <= 12):
            raise ValueError("month out of range")
        datetime(year_val, month_val, 1)  # raises if year is unreasonable
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "month is not a valid calendar month",
        ) from None

    # ── NVR + Camera lookup — no SSRF: hosts always come from the DB rows ────
    nvr = (
        await session.execute(select(Nvr).where(Nvr.id == nvr_id))
    ).scalar_one_or_none()
    camera = (
        await session.execute(
            select(Camera).where(Camera.nvr_id == nvr_id, Camera.channel == channel)
        )
    ).scalar_one_or_none()
    if nvr is None or camera is None or not user_can_access_camera(user, camera):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording source not found")

    # ── Cache check ───────────────────────────────────────────────────────────
    cache_key = (nvr_id, channel, month)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # ── Compute month boundaries in NVR-local time ────────────────────────────
    settings = get_settings()
    tz_offset = settings.playback_tz_offset_minutes
    month_start_local, month_end_local = _month_to_local_bounds(month)

    # ── Fetch from NVR ────────────────────────────────────────────────────────
    password = decrypt_password(nvr.rtsp_password_encrypted)
    try:
        clips = await find_clips(
            nvr.ip,
            80,  # HTTP CGI port, not RTSP port
            nvr.rtsp_username,
            password,
            channel=channel,
            start=month_start_local,
            end=month_end_local,  # already last-second-of-month (exclusive boundary)
        )
    except MediaFindError as exc:
        log.warning(
            "NVR %s ch%d %s availability failed: %s",
            nvr_id, channel, month, exc,
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "NVR recording availability unavailable",
        ) from exc

    # ── Derive distinct local days and oldest epoch ───────────────────────────
    days_with_recordings = clips_to_day_strings(clips)
    oldest_epoch: int | None = None
    if clips:
        oldest_start = min(c.start for c in clips)
        oldest_epoch = nvr_naive_to_epoch(oldest_start, tz_offset)

    result: dict = {
        "days_with_recordings": days_with_recordings,
        "oldest_epoch": oldest_epoch,
    }

    _cache_set(cache_key, result)
    return result
