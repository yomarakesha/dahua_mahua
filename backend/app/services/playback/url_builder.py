"""Playback URL builder, datetime helpers, and input validators.

Pure functions — no network, no I/O — fully unit-testable.  Consumed by the
PlaybackSession (Task 7) and the WS control-message handler (Task 8).

Time format
-----------
Dahua NVR RTSP playback URLs use an *underscore* time format::

    YYYY_MM_DD_HH_MM_SS

This is **different** from the dash/space/colon format used by the HTTP
``mediaFileFind`` CGI (``YYYY-MM-DD HH:MM:SS``).  The underscore format was
verified against 192.168.20.15 on 2026-06-30 (spike finding).

Credential encoding
-------------------
``build_playback_url`` percent-encodes ``user`` and ``pw`` with
``urllib.parse.quote(..., safe="")``.  The production NVR password contains
``*``; other special characters (``@``, ``:``, ``/``, ``?``) would silently
corrupt the RTSP authority if passed verbatim (Contract #8).

Credential hygiene
------------------
The encoded URL is passed directly to an ``ffmpeg`` argv list (never logged or
sent to the client).  Callers must not log the returned URL (Contract #12).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

__all__ = [
    "PlaybackUrlError",
    "build_playback_url",
    "validate_channel",
    "validate_speed",
    "validate_footage_epoch",
    "epoch_to_nvr_local",
    "redact_url",
    "SPEED_WHITELIST",
]

SPEED_WHITELIST: frozenset[int] = frozenset({1, 2, 4, 8})

# Underscore format — VERIFIED spike finding (vs dash/colon mediaFileFind format).
_RTSP_TIME_FMT = "%Y_%m_%d_%H_%M_%S"

# Matches the ``user:pw@`` credential authority of an ``rtsp://…`` URL.
_CRED_RE = re.compile(r"(rtsp://)[^@/]*@")


class PlaybackUrlError(ValueError):
    """Raised when a playback URL cannot be built due to bad inputs."""


def redact_url(url: str) -> str:
    """Replace the credentials in an ``rtsp://user:pw@host`` URL with ``***``.

    Credential hygiene (Contract #12): callers must never log the credentialed
    URL, but if one slips through this guarantees the password never lands in a
    log record.  Also redacts a credentialed URL embedded mid-line (e.g. an
    ffmpeg stderr message).  This module owns credential hygiene for playback
    URLs, so the helper lives here and is imported by ``session`` + ``snapshot``.
    """
    return _CRED_RE.sub(r"\1***@", url)


def build_playback_url(
    ip: str,
    rtsp_port: int,
    user: str,
    pw: str,
    channel: int,      # 1-based, caller-validated
    start: datetime,   # naive NVR-local datetime
    end: datetime,     # naive NVR-local datetime
) -> str:
    """Build the RTSP playback URL for a Dahua NVR.

    Time format: ``YYYY_MM_DD_HH_MM_SS`` (underscores, verified against
    192.168.20.15 on 2026-06-30 — NOT the dash/colon mediaFileFind format).
    Does NOT include ``&subtype=`` (NVR ignores it; records main-only).
    Channel is 1-based.

    ``user`` and ``pw`` are percent-encoded (Contract #8) so that special
    characters in the password (``*``, ``@``, ``:``, etc.) do not corrupt the
    RTSP authority.  The URL is intended for ffmpeg argv (list, no shell) and
    must never be logged or sent to the client.

    Args:
        ip:        NVR IP address.
        rtsp_port: NVR RTSP port (554 is the Dahua default; see Contract #9).
        user:      NVR username (percent-encoded before interpolation).
        pw:        NVR password (percent-encoded before interpolation).
        channel:   1-based camera channel number (caller-validated).
        start:     Segment start — naive NVR-local datetime.
        end:       Segment end   — naive NVR-local datetime.

    Returns:
        Fully-formed RTSP playback URL string.

    Raises:
        PlaybackUrlError: if ``start >= end`` — a non-positive window makes the
            Dahua ``/cam/playback`` CGI starve the stream (only the fMP4 init
            segment is produced, no media fragments; verified on 192.168.20.15,
            2026-07-01).  A forward seek past the open window must recompute the
            end boundary before building the URL (HIGH-3).
    """
    if start >= end:
        raise PlaybackUrlError(
            f"playback window is empty or inverted: start={start.isoformat()} "
            f">= end={end.isoformat()}"
        )
    encoded_user = quote(user, safe="")
    encoded_pw   = quote(pw,   safe="")
    s = start.strftime(_RTSP_TIME_FMT)
    e = end.strftime(_RTSP_TIME_FMT)
    return (
        f"rtsp://{encoded_user}:{encoded_pw}@{ip}:{rtsp_port}/cam/playback"
        f"?channel={channel}&starttime={s}&endtime={e}"
    )


def validate_channel(channel: int, max_channel: int = 64) -> int:
    """Return *channel* if it is within ``[1, max_channel]``.

    Args:
        channel:     1-based channel number to validate.
        max_channel: Upper bound (inclusive); default 64 covers all Dahua NVRs.

    Returns:
        The validated channel number (unchanged).

    Raises:
        PlaybackUrlError: If ``channel`` is outside the valid range.
    """
    if not (1 <= channel <= max_channel):
        raise PlaybackUrlError(
            f"channel must be between 1 and {max_channel}, got {channel}"
        )
    return channel


def validate_speed(speed: int) -> int:
    """Return *speed* if it is in the playback speed whitelist ``{1, 2, 4, 8}``.

    Speed is backend-owned (Contract #13): server-side frame decimation keeps
    ``<video>.playbackRate`` at 1.0.  Only whitelisted values reach ffmpeg.

    Args:
        speed: Requested playback speed multiplier.

    Returns:
        The validated speed value (unchanged).

    Raises:
        PlaybackUrlError: If *speed* is not in ``SPEED_WHITELIST``.
    """
    if speed not in SPEED_WHITELIST:
        raise PlaybackUrlError(
            f"speed must be one of {sorted(SPEED_WHITELIST)}, got {speed}"
        )
    return speed


def validate_footage_epoch(epoch: int | float) -> int:
    """Return *epoch* cast to ``int`` if it is a positive number.

    Args:
        epoch: Footage UTC epoch (seconds).  Accepts ``int`` or ``float``;
               the fractional part is truncated.

    Returns:
        The epoch as a plain ``int``.

    Raises:
        PlaybackUrlError: If *epoch* cannot be converted to int, or is ≤ 0.
    """
    try:
        v = int(epoch)
    except (TypeError, ValueError):
        raise PlaybackUrlError(
            f"footage_epoch must be an integer, got {epoch!r}"
        )
    if v <= 0:
        raise PlaybackUrlError(f"footage_epoch must be positive, got {v}")
    return v


def epoch_to_nvr_local(epoch: int, tz_offset_minutes: int) -> datetime:
    """Convert a UTC epoch to a naive NVR-local datetime.

    This is the pure inverse needed to build the RTSP playback URL from a
    client seek target.  The client sends a UTC epoch (``{seek: <epoch>}``);
    the ffmpeg URL requires a naive NVR-local datetime.

    The NVR timezone offset is a *fixed integer* (minutes east of UTC) read
    from ``settings.playback_tz_offset_minutes``.  There is **no DST
    awareness** by design — the NVR offset is a deploy-time constant, not a
    IANA timezone rule.

    Args:
        epoch:             UTC seconds since the Unix epoch.
        tz_offset_minutes: Fixed offset east of UTC, in minutes (negative for
                           west).  Example: ``300`` for UTC+5, ``-300`` for
                           UTC-5.

    Returns:
        Naive ``datetime`` representing the same instant in NVR-local time.
        ``tzinfo`` is ``None`` (stripped so callers can pass it directly to
        ``strftime`` without conversion warnings).
    """
    utc_dt   = datetime.fromtimestamp(epoch, tz=timezone.utc)
    local_dt = utc_dt + timedelta(minutes=tz_offset_minutes)
    return local_dt.replace(tzinfo=None)  # strip tz → naive NVR-local
