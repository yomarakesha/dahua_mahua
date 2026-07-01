"""Tests for the playback URL builder, validators, and datetime helpers.

Covers:
- build_playback_url: exact URL shape, underscore time format, channel param,
  no subtype, and percent-encoding of special characters in user/pw (Contract #8).
- validate_speed: whitelist {1,2,4,8}; rejects anything else.
- validate_channel: bounds [1, max_channel]; rejects 0 and >max.
- validate_footage_epoch: positive int-like; rejects 0 and negatives.
- epoch_to_nvr_local: pure offset math, parametrized offsets, no DST awareness.
"""

import re
from datetime import datetime, timedelta, timezone

import pytest

from app.services.playback.url_builder import (
    SPEED_WHITELIST,
    PlaybackUrlError,
    build_playback_url,
    epoch_to_nvr_local,
    validate_channel,
    validate_footage_epoch,
    validate_speed,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

_START = datetime(2026, 6, 30, 16, 0, 0)
_END   = datetime(2026, 6, 30, 17, 0, 0)


def _url(channel: int = 1, user: str = "admin", pw: str = "secret") -> str:
    return build_playback_url("192.168.20.15", 554, user, pw, channel, _START, _END)


# ── build_playback_url ────────────────────────────────────────────────────────

# Assertion 1: exact URL string (plain credentials encode to themselves)
def test_build_playback_url_exact():
    url = _url()
    assert url == (
        "rtsp://admin:secret@192.168.20.15:554/cam/playback"
        "?channel=1&starttime=2026_06_30_16_00_00&endtime=2026_06_30_17_00_00"
    )


# Assertion 2: time format uses underscores, not dashes or colons
def test_build_playback_url_underscore_time_format():
    url = _url()
    assert re.search(r"starttime=\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}", url), (
        f"starttime must use underscore format; got: {url}"
    )
    assert re.search(r"endtime=\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}", url), (
        f"endtime must use underscore format; got: {url}"
    )


# Assertion 3: channel parameter appears correctly
def test_build_playback_url_channel_param():
    url = _url(channel=5)
    assert "channel=5" in url


# Assertion 4: subtype must NOT appear anywhere in the URL
def test_build_playback_url_no_subtype():
    url = _url()
    assert "subtype" not in url


# Contract #8: percent-encode special characters in user and pw
def test_build_playback_url_percent_encodes_credentials():
    url = build_playback_url(
        "192.168.20.15", 554,
        user="ad@min",
        pw="pa@ss*word",
        channel=1,
        start=_START,
        end=_END,
    )
    # '@' → %40, '*' → %2A (or %2a — case-insensitive per RFC 3986)
    assert "ad%40min" in url, f"user '@' must be percent-encoded; got authority in: {url}"
    assert "pa%40ss" in url, f"pw '@' must be percent-encoded; got: {url}"
    # '*' encodes as %2A or %2a
    assert re.search(r"pa%40ss%2[Aa]word", url), (
        f"pw '*' must be percent-encoded; got: {url}"
    )
    # Neither literal '@' nor literal '*' should appear in the authority segment
    authority = url.split("@", 1)  # [scheme+creds, rest]
    # There must be exactly one '@' separating authority from host
    assert url.count("@") == 1, "URL must have exactly one '@' (host separator)"


# ── validate_speed ────────────────────────────────────────────────────────────

# Assertion 5: all valid speeds return the speed value
@pytest.mark.parametrize("speed", [1, 2, 4, 8])
def test_validate_speed_valid(speed: int):
    assert validate_speed(speed) == speed


# Assertion 5 (cross-check): SPEED_WHITELIST is the declared set
def test_speed_whitelist_contents():
    assert SPEED_WHITELIST == frozenset({1, 2, 4, 8})


# Assertion 6: invalid speed raises PlaybackUrlError
def test_validate_speed_invalid():
    with pytest.raises(PlaybackUrlError):
        validate_speed(3)


@pytest.mark.parametrize("speed", [0, 3, 5, 6, 7, 9, 16, -1])
def test_validate_speed_invalid_values(speed: int):
    with pytest.raises(PlaybackUrlError):
        validate_speed(speed)


# ── validate_channel ──────────────────────────────────────────────────────────

# Assertion 7: boundary values [1, 64] are accepted
@pytest.mark.parametrize("ch", [1, 64])
def test_validate_channel_valid_boundaries(ch: int):
    assert validate_channel(ch) == ch


# Assertion 8: channel=0 raises
def test_validate_channel_zero():
    with pytest.raises(PlaybackUrlError):
        validate_channel(0)


# Assertion 9: channel=65 raises (exceeds default max_channel=64)
def test_validate_channel_too_large():
    with pytest.raises(PlaybackUrlError):
        validate_channel(65)


def test_validate_channel_mid_range():
    assert validate_channel(32) == 32


def test_validate_channel_custom_max():
    # Custom max_channel honoured
    assert validate_channel(16, max_channel=16) == 16
    with pytest.raises(PlaybackUrlError):
        validate_channel(17, max_channel=16)


# ── validate_footage_epoch ────────────────────────────────────────────────────

# Assertion 10: valid positive epoch returns int
def test_validate_footage_epoch_valid():
    result = validate_footage_epoch(1719734400)
    assert result == 1719734400
    assert isinstance(result, int)


# Assertion 10 (also): float-valued epoch is cast to int
def test_validate_footage_epoch_float():
    result = validate_footage_epoch(1719734400.9)
    assert result == 1719734400
    assert isinstance(result, int)


# Assertion 11: epoch=0 raises
def test_validate_footage_epoch_zero():
    with pytest.raises(PlaybackUrlError):
        validate_footage_epoch(0)


# Assertion 12: negative epoch raises
def test_validate_footage_epoch_negative():
    with pytest.raises(PlaybackUrlError):
        validate_footage_epoch(-1)


def test_validate_footage_epoch_non_numeric():
    with pytest.raises(PlaybackUrlError):
        validate_footage_epoch("not-a-number")  # type: ignore[arg-type]


# ── epoch_to_nvr_local ────────────────────────────────────────────────────────

# Assertion 13: epoch=0, offset=0 → Unix epoch naive datetime
def test_epoch_to_nvr_local_zero_offset():
    dt = epoch_to_nvr_local(0, 0)
    assert dt == datetime(1970, 1, 1, 0, 0, 0)
    assert dt.tzinfo is None  # must be naive


# Assertion 14: epoch=0, offset=+60 → UTC+1 → 1970-01-01 01:00:00
def test_epoch_to_nvr_local_plus_one_hour():
    dt = epoch_to_nvr_local(0, 60)
    assert dt == datetime(1970, 1, 1, 1, 0, 0)
    assert dt.tzinfo is None


# Assertion 15: parametrize offset math for a realistic epoch
_EPOCH_15 = 1751234400  # 2025-06-29 18:00:00 UTC


@pytest.mark.parametrize("offset_minutes", [0, 60, -60, 300, -300, 720, -720])
def test_epoch_to_nvr_local_offset_math(offset_minutes: int):
    """The returned naive datetime must equal UTC + offset (pure math, no DST)."""
    expected = (
        datetime.fromtimestamp(_EPOCH_15, tz=timezone.utc).replace(tzinfo=None)
        + timedelta(minutes=offset_minutes)
    )
    assert epoch_to_nvr_local(_EPOCH_15, offset_minutes) == expected


# Assertion 16: no DST awareness — fixed offset behaves consistently across a
# known DST transition (US Eastern spring-forward: 2026-03-08 02:00→03:00,
# which occurs at 07:00 UTC). A fixed-offset function must step linearly.
def test_epoch_to_nvr_local_no_dst():
    # 1 second before spring-forward (US/Eastern → UTC 06:59:59)
    epoch_pre  = int(datetime(2026, 3, 8, 6, 59, 59, tzinfo=timezone.utc).timestamp())
    # 1 second after spring-forward (US/Eastern → UTC 07:00:01)
    epoch_post = int(datetime(2026, 3, 8, 7,  0,  1, tzinfo=timezone.utc).timestamp())

    # With any fixed offset the gap must be exactly 2 seconds (no DST jump).
    for offset in (0, -300, 300):  # UTC, US/Eastern winter (-5h), US/Eastern summer (-4h)
        dt_pre  = epoch_to_nvr_local(epoch_pre,  offset)
        dt_post = epoch_to_nvr_local(epoch_post, offset)
        diff = (dt_post - dt_pre).total_seconds()
        assert diff == 2.0, (
            f"DST must not affect epoch_to_nvr_local (offset={offset}); "
            f"got {diff}s gap instead of 2.0s"
        )
