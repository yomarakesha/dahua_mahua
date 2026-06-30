"""Tests for the Dahua mediaFileFind HTTP client.

All HTTP calls are mocked — no real NVR required.

Coverage:
  (a) Happy path: create → findFile → findNextFile (paginated) → close/destroy
      returns correctly merged Clip list.
  (b) Error path: when findNextFile raises on the 2nd call, the handle is
      closed (close + destroy called with correct object id) AND MediaFindError
      propagates to the caller.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.playback.index_parser import Clip
from app.services.playback.media_find import MediaFindError, find_clips


# ── Mock helpers ──────────────────────────────────────────────────────────────


def _resp(text: str) -> MagicMock:
    """Build a minimal mock httpx.Response."""
    r = MagicMock()
    r.text = text
    r.status_code = 200
    r.raise_for_status = MagicMock()  # no-op
    return r


def _mock_client(side_effects: list) -> AsyncMock:
    """Build an AsyncMock client whose .get returns/raises scripted values."""
    client = AsyncMock()
    client.get = AsyncMock(side_effect=side_effects)
    return client


def _patch_client(mock_client: AsyncMock):
    """Return a patch() that wires mock_client as the AsyncClient context-manager."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_client)
    cm.__aexit__ = AsyncMock(return_value=False)
    constructor = MagicMock(return_value=cm)
    return patch("app.services.playback.media_find.httpx.AsyncClient", constructor)


# ── Sample NVR bodies ─────────────────────────────────────────────────────────

# Two adjacent Timing/Main records — should merge into a single Clip.
# Mirrors the real NVR body: stream carried by VideoStream, Type=dav is container.
_PAGE_TWO_RECORDS = (
    "items[0].Channel=0\r\n"
    "items[0].StartTime=2026-06-29 08:00:00\r\n"
    "items[0].EndTime=2026-06-29 08:30:00\r\n"
    "items[0].Type=dav\r\n"
    "items[0].Flags[0]=Timing\r\n"
    "items[0].VideoStream=Main\r\n"
    "items[1].StartTime=2026-06-29 08:30:00\r\n"
    "items[1].EndTime=2026-06-29 09:00:00\r\n"
    "items[1].Type=dav\r\n"
    "items[1].Flags[0]=Timing\r\n"
    "items[1].VideoStream=Main\r\n"
)

_PAGE_EMPTY = ""


# ── (a) Happy path ────────────────────────────────────────────────────────────


async def test_happy_path_returns_merged_clips():
    """create → findFile → findNextFile(2 records, <batch) → close → destroy → 1 merged Clip."""
    client = _mock_client([
        _resp("result=1\r\n"),           # factory.create  → id=1
        _resp("OK\r\n"),                 # findFile → bare "OK" (real NVR contract)
        _resp(_PAGE_TWO_RECORDS),        # findNextFile p1 → 2 records (< batch=100, done)
        _resp("result=ok\r\n"),          # close
        _resp("result=ok\r\n"),          # destroy
    ])

    with _patch_client(client):
        clips = await find_clips(
            "192.168.1.100", 80, "admin", "secret",
            channel=1,
            start=datetime(2026, 6, 29, 8, 0, 0),
            end=datetime(2026, 6, 29, 9, 0, 0),
        )

    assert isinstance(clips, list)
    assert len(clips) == 1
    c = clips[0]
    assert isinstance(c, Clip)
    assert c.start == datetime(2026, 6, 29, 8, 0, 0)
    assert c.end == datetime(2026, 6, 29, 9, 0, 0)
    assert c.type == "Timing"
    assert c.stream == "Main"

    # The findFile URL must use %20 for the space in the time strings (RFC 3986)
    call_urls = [str(ca.args[0]) for ca in client.get.call_args_list]
    find_url = next(u for u in call_urls if "action=findFile" in u)
    assert "%20" in find_url, f"Expected %20 in findFile URL but got: {find_url}"
    assert "StartTime=2026-06-29%2008%3A00%3A00" in find_url or "StartTime=2026-06-29%20" in find_url, (
        f"StartTime not properly encoded in: {find_url}"
    )
    # Ensure no raw (unencoded) space appears in the time-condition portion
    cond_part = find_url.split("condition.StartTime=", 1)[1] if "condition.StartTime=" in find_url else ""
    assert " " not in cond_part, f"Raw space found in time condition: {cond_part!r}"


async def test_happy_path_paginates_until_short_batch():
    """When first page is full (== batch), a second page is fetched."""
    # batch=2 → first page returns 2 (full) → second page empty → done
    client = _mock_client([
        _resp("result=7\r\n"),           # factory.create → id=7
        _resp("result=true\r\n"),        # findFile
        _resp(_PAGE_TWO_RECORDS),        # findNextFile p1 — 2 records == batch=2 → continue
        _resp(_PAGE_EMPTY),              # findNextFile p2 — empty → stop
        _resp("result=ok\r\n"),          # close
        _resp("result=ok\r\n"),          # destroy
    ])

    with _patch_client(client):
        clips = await find_clips(
            "192.168.1.100", 80, "admin", "secret",
            channel=1,
            start=datetime(2026, 6, 29, 8, 0, 0),
            end=datetime(2026, 6, 29, 9, 0, 0),
            batch=2,
        )

    # Both pages together still merge into 1 clip
    assert len(clips) == 1
    # 5th call (index 4) must be the close, 6th (index 5) the destroy
    call_urls = [str(c.args[0]) for c in client.get.call_args_list]
    assert any("action=close" in u and "object=7" in u for u in call_urls)
    assert any("action=destroy" in u and "object=7" in u for u in call_urls)


# ── (b) Error path: handle closed even when findNextFile raises ───────────────


async def test_handle_closed_when_findnextfile_raises():
    """When findNextFile raises on 2nd call, close+destroy still fire and MediaFindError propagates."""
    client = _mock_client([
        _resp("result=42\r\n"),                      # factory.create → id=42
        _resp("result=true\r\n"),                    # findFile
        _resp(_PAGE_TWO_RECORDS),                    # findNextFile p1 (2 records == batch=2)
        Exception("Connection reset by peer"),        # findNextFile p2 → raises
        _resp("result=ok\r\n"),                      # close   (must still happen)
        _resp("result=ok\r\n"),                      # destroy (must still happen)
    ])

    with _patch_client(client):
        with pytest.raises(MediaFindError):
            await find_clips(
                "192.168.1.100", 80, "admin", "secret",
                channel=1,
                start=datetime(2026, 6, 29, 8, 0, 0),
                end=datetime(2026, 6, 29, 9, 0, 0),
                batch=2,  # full first page → triggers 2nd call
            )

    call_urls = [str(c.args[0]) for c in client.get.call_args_list]
    assert any("action=close" in u and "object=42" in u for u in call_urls), (
        f"close not called with id=42; URLs seen: {call_urls}"
    )
    assert any("action=destroy" in u and "object=42" in u for u in call_urls), (
        f"destroy not called with id=42; URLs seen: {call_urls}"
    )


async def test_findfile_failure_raises_mediafind_error():
    """If findFile returns non-true, MediaFindError is raised and close/destroy still fire."""
    client = _mock_client([
        _resp("result=99\r\n"),          # factory.create → id=99
        _resp("result=false\r\n"),       # findFile → failure
        _resp("result=ok\r\n"),          # close
        _resp("result=ok\r\n"),          # destroy
    ])

    with _patch_client(client):
        with pytest.raises(MediaFindError):
            await find_clips(
                "192.168.1.100", 80, "admin", "secret",
                channel=1,
                start=datetime(2026, 6, 29, 8, 0, 0),
                end=datetime(2026, 6, 29, 9, 0, 0),
            )

    call_urls = [str(c.args[0]) for c in client.get.call_args_list]
    assert any("action=close" in u and "object=99" in u for u in call_urls)
    assert any("action=destroy" in u and "object=99" in u for u in call_urls)


async def test_empty_result_returns_empty_clips():
    """NVR with no recordings → findNextFile returns empty body → empty clip list."""
    client = _mock_client([
        _resp("result=3\r\n"),           # factory.create
        _resp("result=true\r\n"),        # findFile
        _resp(_PAGE_EMPTY),              # findNextFile → empty (0 < batch)
        _resp("result=ok\r\n"),          # close
        _resp("result=ok\r\n"),          # destroy
    ])

    with _patch_client(client):
        clips = await find_clips(
            "192.168.1.100", 80, "admin", "secret",
            channel=1,
            start=datetime(2026, 6, 29, 8, 0, 0),
            end=datetime(2026, 6, 29, 9, 0, 0),
        )

    assert clips == []
