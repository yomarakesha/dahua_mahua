"""Playback endpoints — recording index per NVR channel and day.

GET /playback/{nvr_id}/{channel}/index?date=YYYY-MM-DD

Returns the day's clip list for the given NVR channel, with all timestamps
expressed as UTC epoch seconds.  NVR-local naive datetimes are shifted to UTC
using *playback_tz_offset_minutes* from settings (Phase-1 source; live
NVR-clock querying is wired by a later spike task).

Results are cached in-process for 120 s keyed on (nvr_id, channel, date) so
that the NVR's mediaFileFind CGI isn't hammered by concurrent viewers of the
same timeline scrubber.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.crypto import decrypt_password
from app.deps import CurrentUser, SessionDep, user_can_access_nvr
from app.models import Nvr
from app.services.playback.media_find import MediaFindError, find_clips
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


# ── Endpoint ──────────────────────────────────────────────────────────────────

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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

    # ── NVR lookup — no SSRF: host always comes from the DB row ──────────────
    nvr = (
        await session.execute(select(Nvr).where(Nvr.id == nvr_id))
    ).scalar_one_or_none()
    if nvr is None or not user_can_access_nvr(user, nvr):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "NVR not found")

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
    password = decrypt_password(nvr.rtsp_password_encrypted)
    try:
        clips = await find_clips(
            nvr.ip,
            nvr.port,
            nvr.rtsp_username,
            password,
            channel=channel,
            start=day_start_local,
            end=day_end_local,
        )
    except MediaFindError as exc:
        log.warning(
            "NVR %s ch%d %s index failed: %s",
            nvr_id, channel, date, exc,
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"NVR recording index unavailable: {exc}",
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
