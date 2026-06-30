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

import logging
import re
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.crypto import decrypt_password
from app.deps import CurrentUser, SessionDep, user_can_access_nvr
from app.models import Nvr
from app.services.playback.index_parser import Clip
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


# ── Availability helper (pure — unit-testable) ────────────────────────────────


def clips_to_day_strings(clips: list[Clip]) -> list[str]:
    """Return a sorted list of distinct local date strings touched by *clips*.

    A clip whose ``start`` and ``end`` span midnight contributes **both** the
    start day *and* the end day (and any days in between for very long clips).
    The input datetimes must be NVR-local naive — no tz conversion is needed
    because they are already in local time.
    """
    from datetime import date as _date

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

    # ── NVR lookup — no SSRF: host always comes from the DB row ──────────────
    nvr = (
        await session.execute(select(Nvr).where(Nvr.id == nvr_id))
    ).scalar_one_or_none()
    if nvr is None or not user_can_access_nvr(user, nvr):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "NVR not found")

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
            f"NVR recording availability unavailable: {exc}",
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
