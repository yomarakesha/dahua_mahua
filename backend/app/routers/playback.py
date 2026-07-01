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
from fastapi.responses import Response
from sqlalchemy import select

from app.crypto import decrypt_password
from app.deps import AdminUser, CurrentUser, SessionDep, user_can_access_camera
from app.models import Camera, Nvr, User
from app.security import decode_token
from app.services.lockouts import get_active_lockout
from app.services.playback.index_parser import Clip
from app.services.playback.media_find import MediaFindError, find_clips
from app.services.playback.nvr_budget import BudgetExhausted, get_budget
from app.services.playback.snapshot import SnapshotError, grab_frame
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
# Active-sessions endpoint — /playback/sessions  (Task 10)
# ══════════════════════════════════════════════════════════════════════════════
#
# Admin-only: lists every currently active PlaybackSession (across all NVRs and
# users) with counters and metadata.  Mirrors go2rtc's /api/streams pattern.
# Per-camera RBAC does NOT apply here — this is a global admin observability view.


@router.get("/sessions")
async def active_playback_sessions(user: AdminUser) -> dict:  # noqa: ARG001
    """List active playback sessions. **Admin-only.**

    Returns:
        ``{"total": N, "sessions": [{session_id, nvr_id, nvr_label, channel,
        user_id, username, client_ip, state, speed, footage_epoch,
        uptime_seconds, seek_count, bytes_sent, fragments_sent}, ...]}``

    A 403 is raised (by the ``AdminUser`` dep) if the caller is not an admin.
    """
    sessions = [s.to_status_dict() for s in _active_sessions.values()]
    return {
        "total": len(sessions),
        "sessions": sessions,
    }


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

# Full MIME for MSE addSourceBuffer() (Contract #14; bare codec strings are
# rejected). avc1.640032 = H.264 High profile, level 5.0 — the ACTUAL avcC of
# the libx264 re-encode, verified against 192.168.20.15 on 2026-07-01 (High L5.0
# covers every camera in this deployment: ≤4MP; a higher declared level than the
# real stream is accepted by MSE). The earlier avc1.42E01E (Baseline L3.0) did
# NOT match the High-profile output. Follow-up: derive from the pinned init
# segment's avcC box for encoder-independence.
_INIT_CODEC = 'video/mp4; codecs="avc1.640032"'

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


# Sentinel pushed onto the egress queue to tell the single sender to drain any
# already-queued items (e.g. a final eof/error) and then stop.
_EGRESS_STOP = object()


def _enqueue_clock(outbound: "asyncio.Queue", item) -> None:
    """Best-effort enqueue of a 2 s clock tick (Contract #3).

    A clock tick is disposable: if the bounded egress queue is momentarily full
    (slow client) we DROP THE TICK rather than evict a queued item — the queue
    may hold a structural message (reinit / init segment / eof / error) or a
    media fragment that must never be discarded.  ``put_nowait`` never suspends.
    """
    try:
        outbound.put_nowait(item)
    except asyncio.QueueFull:
        pass  # drop the tick; never evict a queued item


async def _emit_structural(
    outbound: "asyncio.Queue",
    *items,
    egress: "asyncio.Task | None" = None,
) -> None:
    """Enqueue one or more structural messages that must NEVER be dropped or
    evicted (reinit / init segment / eof / error / the egress sentinel).

    When several items are passed (a ``reinit``/``init`` JSON + its pinned init
    segment) they land CONTIGUOUSLY on the wire: we first wait — without evicting
    anything — until the bounded queue has room for the whole group, then enqueue
    them back-to-back with ``put_nowait`` and NO awaited suspension between them,
    so a clock tick (or any other producer) can never interleave the pair.
    Structural messages are small and rare, so this awaited back-pressure is
    cheap.  An unbounded queue (``maxsize == 0``) always has room.

    ``egress`` — when supplied — is checked on every spin: if it is already done
    (crashed or shut down), the function returns early rather than looping forever
    against a dead drainer.  A hard cap of 400 spins (~2 s) is the final safety
    valve (IMPORTANT 4).
    """
    n = len(items)
    maxsize = outbound.maxsize
    if maxsize:
        _max_spins = 400  # 400 × 0.005 s ≈ 2 s; prevents infinite spin on dead egress
        _spins = 0
        while maxsize - outbound.qsize() < n:
            if egress is not None and egress.done():
                return  # egress is dead — nowhere to drain; give up
            if _spins >= _max_spins:
                return  # safety valve: give up rather than loop forever
            await asyncio.sleep(0.005)  # let the egress loop drain; never evict
            _spins += 1
    for it in items:
        outbound.put_nowait(it)


async def _dispatch(msg: dict, sess: PlaybackSession, outbound: "asyncio.Queue") -> None:
    """Apply one parsed client control message to the session.

    Validators run BEFORE any session mutation (so a bad ``speed``/``seek`` is
    rejected before ffmpeg is touched).  Invalid client input — including a
    non-integer ``speed`` like ``"fast"`` (Task-8 review #3) — is turned into a
    sanitised ``{type:"error"}`` enqueued on the egress queue; this method NEVER
    raises and NEVER tears the session down.

    ``reinit`` is intentionally NOT emitted here.  The single ``_fragment_producer``
    owns reinit → new init segment → new fragments so that ordering can't be
    raced by a second sender (Task-8 review, ordering requirement #2).
    """
    try:
        if "seek" in msg:
            epoch = validate_footage_epoch(msg["seek"])
            await sess.seek(epoch)
        elif "speed" in msg:
            try:
                raw_speed = int(msg["speed"])
            except (ValueError, TypeError):
                # int("fast") → ValueError; map to the same graceful path as an
                # out-of-whitelist speed instead of killing the session (#3).
                raise PlaybackUrlError("speed must be an integer")
            speed = validate_speed(raw_speed)
            await sess.set_speed(speed)
        elif "pause" in msg:
            await sess.pause()
        elif "play" in msg:
            await sess.resume(sess.footage_now())
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
    except PlaybackUrlError as exc:
        # str(exc) is a fixed validator message — no credentials (Contract #12).
        await _emit_structural(outbound, {"type": "error", "reason": str(exc)})


async def _egress_loop(ws: WebSocket, outbound: "asyncio.Queue") -> None:
    """The SOLE owner of the WebSocket send side (Task-8 review, single egress).

    Drains ``outbound`` in FIFO order and is the ONLY coroutine that ever calls
    ``ws.send_bytes`` / ``ws.send_json``.  This serialises every send — the 2 s
    clock tick can no longer interleave ASGI messages mid-fragment (which would
    corrupt frames or raise "another coroutine is already waiting") — and pins
    wire order to enqueue order.  Stops on the ``_EGRESS_STOP`` sentinel.
    """
    while True:
        item = await outbound.get()
        if item is _EGRESS_STOP:
            return
        if isinstance(item, (bytes, bytearray)):
            await ws.send_bytes(item)
        else:
            await ws.send_json(item)


async def _clock_sender(sess: PlaybackSession, outbound: "asyncio.Queue", interval: float) -> None:
    """Enqueue ``{type:"clock", wall_ts:<footage epoch>}`` while PLAYING (Contract #3)."""
    while sess.state not in (SessionState.CLOSED, SessionState.ERROR):
        await asyncio.sleep(interval)
        if sess.state == SessionState.PLAYING:
            _enqueue_clock(outbound, {"type": "clock", "wall_ts": sess.footage_now()})


async def _fragment_producer(sess: PlaybackSession, outbound: "asyncio.Queue") -> None:
    """Single producer of init/reinit JSON, the pinned init segment, and fMP4
    fragments — enqueued onto the egress queue in strict order (Task-8 review).

    On every new ``_spawn_gen`` it emits, contiguously (no awaited suspension
    between them, so no clock/error tick can interleave): the gen-0 ``init`` —
    or a ``reinit`` on respawn — JSON, then the pinned init segment bytes; then
    it streams new-timeline fragments from the back-pressure ring.  Because the
    session CLEARS the ring on every respawn (review #1), the fragments read
    after the init segment are always from the new timeline — stale pre-respawn
    media can never land after the new init segment.

    When the ring drains AND ffmpeg has exited on its own while PLAYING, we
    classify clean-EOF vs crash (review #2), enqueue the signal, and return; the
    endpoint then closes the session.

    INTEGRATION: the exact fMP4 byte-boundary alignment of the pinned init
    segment is an on-network check (no live NVR here).
    """
    last_gen = -1
    held: bytes | None = None  # a fetched chunk that must wait behind a pending reinit
    held_gen = -1              # the _spawn_gen the held chunk was fetched at
    while not sess._closing and sess.state not in (SessionState.CLOSED, SessionState.ERROR):
        if sess._spawn_gen != last_gen:
            init = await sess.wait_init_segment(timeout=_INIT_SEGMENT_TIMEOUT)
            # A respawn during the (wide) init-wait advances _spawn_gen and makes
            # wait_init_segment return the NEWER gen's init, so snapshot the gen
            # AFTER the await — it is the generation the init segment we are about
            # to emit actually corresponds to, not a pre-await snapshot.
            gen = sess._spawn_gen
            # reinit/init JSON + the pinned init segment are emitted as ONE
            # contiguous group so no clock tick can interleave the pair.
            head = (
                {"type": "init", "t0": sess.t0, "codec": _INIT_CODEC}
                if last_gen == -1
                else {"type": "reinit", "t0": sess.t0}
            )
            if init:
                await _emit_structural(outbound, head, init)
            else:
                await _emit_structural(outbound, head)
            last_gen = gen
            # fall through to flush / drop any chunk held across the gen boundary
        if held is not None:
            if held_gen != sess._spawn_gen:
                # A newer spawn superseded this chunk while we waited for the new
                # init segment (rapid double-seek): its init + fragments win, so
                # DROP the now-stale held chunk — emitting an old-timeline fragment
                # after a fresh init segment corrupts the MSE source buffer.
                held = None
                held_gen = -1
                continue
            await outbound.put(held)
            held = None
            held_gen = -1
            continue
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
                    await _emit_structural(outbound, {"type": "eof"})
                else:
                    await _emit_structural(
                        outbound,
                        {"type": "error", "reason": "playback stream ended unexpectedly"},
                    )
                return
            continue
        if sess._spawn_gen != last_gen:
            # A respawn happened while we were blocked on the (now-cleared) ring,
            # so this chunk belongs to the NEW generation: tag it with that gen,
            # hold it, and loop so the reinit + new init segment are emitted ahead
            # of it.  If yet another respawn lands during the next init-wait, the
            # held-gen check above drops this now-stale chunk (review follow-up).
            held = chunk
            held_gen = sess._spawn_gen
            continue
        # Media fragments use the awaited put: when the egress queue fills (slow
        # client) the producer blocks here, the session ring fills, and it drops
        # the OLDEST chunk — preserving the Contract #11 back-pressure discipline.
        await outbound.put(chunk)


async def _receive_loop(ws: WebSocket, sess: PlaybackSession, outbound: "asyncio.Queue") -> None:
    """Read client control JSON and dispatch it (bad input → sanitised error)."""
    while True:
        msg = await ws.receive_json()  # raises WebSocketDisconnect on close
        if not isinstance(msg, dict):
            log.warning("Ignoring non-object playback control message")
            continue
        await _dispatch(msg, sess, outbound)


async def _control_loop(ws: WebSocket, sess: PlaybackSession, clock_interval: float) -> None:
    """Wire up the single egress + the receive/clock/fragment producers.

    All three producers PUT onto one ``outbound`` queue; ``_egress_loop`` is the
    sole WS sender (Task-8 review, single egress).  A client disconnect surfaces
    from ``_receive_loop`` as ``WebSocketDisconnect``; end-of-stream/crash from
    ``_fragment_producer``.  Whichever finishes first — including a crashed egress
    (IMPORTANT 4) — the others are cancelled, the egress is allowed to flush any
    final eof/error then stop, and we re-raise the first producer's exception so
    the endpoint's ``finally`` runs.  The session ``finally`` (close + budget
    release) always runs regardless of which task finishes first.
    """
    outbound: asyncio.Queue = asyncio.Queue(maxsize=max(8, sess.ring_buffer_chunks))
    egress = asyncio.create_task(_egress_loop(ws, outbound), name="pb-egress")
    recv = asyncio.create_task(_receive_loop(ws, sess, outbound), name="pb-recv")
    clock = asyncio.create_task(_clock_sender(sess, outbound, clock_interval), name="pb-clock")
    frag = asyncio.create_task(_fragment_producer(sess, outbound), name="pb-frag")
    producers = {recv, clock, frag}
    # Include egress in the wait set: a crashed sender also tears the loop down
    # (IMPORTANT 4a) so it can never busy-loop with a dead drainer.
    done, pending = await asyncio.wait({*producers, egress}, return_when=asyncio.FIRST_COMPLETED)
    # Stop the still-running producers so nothing new is enqueued.
    for t in pending & producers:
        t.cancel()
    await asyncio.gather(*(pending & producers), return_exceptions=True)
    # Let the egress drain whatever is already queued (e.g. a final eof/error),
    # then stop on the sentinel.  Pass egress so _emit_structural can abort if
    # egress already died (IMPORTANT 4b) — preventing an infinite spin.
    await _emit_structural(outbound, _EGRESS_STOP, egress=egress)
    if egress not in done:
        try:
            await asyncio.wait_for(egress, timeout=2.0)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            egress.cancel()
            await asyncio.gather(egress, return_exceptions=True)
    # Surface the first PRODUCER's exception (e.g. WebSocketDisconnect from recv,
    # eof-signal from frag).  Egress exceptions are NOT re-raised — an egress crash
    # silently tears the loop; the endpoint finally block owns all cleanup.
    for t in done:
        if t is egress:
            continue
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
        # Resolve client IP from the WebSocket transport (may be None in tests).
        _client_ip = websocket.client.host if websocket.client is not None else ""
        sess = PlaybackSession(
            # Observability metadata (Task 10) — set once, never mutated.
            user_id=str(user.id),
            username=user.username,
            client_ip=_client_ip,
            nvr_label=nvr.label,
            # NVR / stream config.
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
        # The {type:"init"} signal + pinned init segment are emitted by the
        # single fragment producer on its first spawn generation (single-egress
        # ordering); the endpoint no longer sends directly on the socket.
        await _control_loop(websocket, sess, settings.playback_clock_interval_seconds)
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        # Never swallow cancellation — re-raise so graceful lifespan shutdown
        # isn't interfered with (Task-8 review #4).  The finally below still
        # runs (session close + budget release).
        raise
    except Exception:  # noqa: BLE001 — ensure cleanup on any failure path
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


# ══════════════════════════════════════════════════════════════════════════════
# Thumbnail endpoint — /playback/{nvr_id}/{channel}/thumb?at=<epoch>  (Task 9)
# ══════════════════════════════════════════════════════════════════════════════
#
# Returns a single JPEG frame extracted by ffmpeg from the NVR recording at the
# requested footage epoch.  Used by the timeline drag-preview (throttled: emit on
# drag-settle/end, not every pointermove — Contract #7).
#
# Auth:  per-camera RBAC (Contract #1) — load Camera by (nvr_id, channel),
#        authorize with user_can_access_camera.  Missing NVR or camera → 404.
# Snapshot does NOT acquire NvrBudget — it is a short-lived one-shot (< 15 s).
# Credential hygiene (Contract #12): password and credentialed URL never logged.


@router.get("/{nvr_id}/{channel}/thumb")
async def playback_thumb(
    nvr_id: str,
    channel: int,
    at: int,            # footage epoch (UTC seconds)
    session: SessionDep,
    user: CurrentUser,
) -> Response:
    """Return a JPEG frame at the given footage epoch.

    Returns:
        JPEG bytes with ``Content-Type: image/jpeg``.

    Error responses:
        400 — invalid channel (< 1) or epoch (≤ 0).
        404 — NVR or camera not found, or user has no per-camera access.
        502 — ffmpeg failed, timed out, or returned empty output.

    Rate note: not cached — callers must throttle (drag-preview: fire on
    drag-settle/end, not every pointermove).
    """
    # ── Input validation (400) ─────────────────────────────────────────────
    try:
        validate_channel(channel)
        validate_footage_epoch(at)
    except PlaybackUrlError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # ── Per-camera RBAC (Contract #1) — no SSRF: IP comes from DB row ─────
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

    # ── Extract frame via ffmpeg ──────────────────────────────────────────
    settings = get_settings()
    password = decrypt_password(nvr.rtsp_password_encrypted)  # never logged
    try:
        jpeg = await grab_frame(
            ip=nvr.ip,
            rtsp_port=nvr.port,          # Contract #9: nvr.port is the RTSP port
            user=nvr.rtsp_username,
            pw=password,
            channel=channel,
            footage_epoch=at,
            tz_offset_minutes=settings.playback_tz_offset_minutes,
            ffbin=settings.reencode_ffmpeg_bin,
        )
    except SnapshotError as exc:
        # Log exc text only — it never contains the password (Contract #12).
        log.warning(
            "Snapshot failed nvr=%s ch=%d at=%d: %s",
            nvr_id, channel, at, exc,
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Snapshot unavailable"
        ) from exc

    return Response(content=jpeg, media_type="image/jpeg")
