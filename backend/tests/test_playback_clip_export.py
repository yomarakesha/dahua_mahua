"""Unit tests for clip_export.py — build_clip_argv + export_clip.

build_clip_argv assertions are sync (no network).  export_clip tests fully mock
asyncio.create_subprocess_exec so no real ffmpeg/NVR is touched; they exercise
the teardown path (graceful 'q' quit), empty-output ("no data"), timeout, and
credential redaction.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.playback.clip_export import (
    ClipExportError,
    build_clip_argv,
    export_clip,
)

# ── build_clip_argv ─────────────────────────────────────────────────────────

_URL = "rtsp://admin:pa%40ss%2Aword@10.10.1.15:554/cam/playback?channel=1&starttime=t&endtime=t"


def test_ffbin_first_out_path_last():
    argv = build_clip_argv("ffmpeg", _URL, "/tmp/out.mp4")
    assert argv[0] == "ffmpeg"
    assert argv[-1] == "/tmp/out.mp4"


def test_default_is_stream_copy():
    argv = build_clip_argv("ffmpeg", _URL, "/tmp/out.mp4")
    assert "-c" in argv and argv[argv.index("-c") + 1] == "copy"
    # No re-encode in the fast path.
    assert "libx264" not in argv


def test_faststart_present():
    argv = build_clip_argv("ffmpeg", _URL, "/tmp/out.mp4")
    assert "-movflags" in argv
    assert argv[argv.index("-movflags") + 1] == "+faststart"


def test_transport_tcp_by_default():
    argv = build_clip_argv("ffmpeg", _URL, "/tmp/out.mp4")
    assert "-rtsp_transport" in argv
    assert argv[argv.index("-rtsp_transport") + 1] == "tcp"


def test_reencode_fallback_uses_libx264_and_drops_audio():
    argv = build_clip_argv("ffmpeg", _URL, "/tmp/out.mp4", reencode=True)
    assert "libx264" in argv
    assert "-an" in argv
    assert "copy" not in argv


def test_no_element_contains_space():
    argv = build_clip_argv("/usr/bin/ffmpeg", _URL, "/tmp/out file.mp4")
    # Only the deliberately-spaced out_path may contain a space; every ffmpeg
    # FLAG/token must be space-free.
    for elem in argv[:-1]:
        assert " " not in elem, f"argv element contains a space: {elem!r}"


# ── export_clip ─────────────────────────────────────────────────────────────

_KW = dict(
    ip="10.10.1.15",
    rtsp_port=554,
    user="admin",
    pw="secret",
    channel=1,
    start_epoch=1_719_734_400,
    end_epoch=1_719_734_460,
    tz_offset_minutes=0,
    ffbin="ffmpeg",
)


def _mock_proc(returncode=0, stderr=b""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.wait = AsyncMock(return_value=returncode)
    proc.kill = MagicMock()
    proc.stderr = MagicMock()
    proc.stderr.read = AsyncMock(return_value=stderr)
    proc.stdin = MagicMock()
    proc.stdin.is_closing = MagicMock(return_value=False)
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    return proc


@pytest.mark.asyncio
async def test_export_success_writes_no_error(tmp_path):
    out = tmp_path / "clip.mp4"
    out.write_bytes(b"mp4bytes")  # simulate ffmpeg output
    proc = _mock_proc(returncode=0)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        # No exception on a clean run with non-empty output.
        await export_clip(**_KW, out_path=str(out), overall_timeout_seconds=100)


@pytest.mark.asyncio
async def test_export_empty_output_raises_no_data(tmp_path):
    out = tmp_path / "clip.mp4"
    out.write_bytes(b"")  # NVR yielded nothing
    proc = _mock_proc(returncode=1, stderr=b"some ffmpeg error")
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        with pytest.raises(ClipExportError) as ei:
            await export_clip(**_KW, out_path=str(out), overall_timeout_seconds=100)
    assert "no recorded video" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_export_nonzero_rc_with_partial_output_fails_and_deletes(tmp_path):
    """A non-zero ffmpeg rc is a HARD failure even with partial bytes: +faststart
    writes the moov atom only on a clean exit, so the file is unplayable.  It must
    raise ClipExportError (→ 502) AND delete the corrupt temp file (never 200)."""
    out = tmp_path / "clip.mp4"
    out.write_bytes(b"\x00\x00\x00\x18ftyppartialdata")  # truncated, no moov
    proc = _mock_proc(returncode=1, stderr=b"muxer error near EOF")
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        with pytest.raises(ClipExportError) as ei:
            await export_clip(**_KW, out_path=str(out), overall_timeout_seconds=100)
    assert "rc=1" in str(ei.value)
    # The corrupt partial file must be gone (not served as a 200).
    assert not out.exists()


@pytest.mark.asyncio
async def test_export_teardown_on_client_disconnect(tmp_path):
    """A disconnecting client tears ffmpeg down GRACEFULLY (stdin 'q' → RTSP
    TEARDOWN) and raises ClipExportError — no orphan ffmpeg, no leaked NVR slot."""
    out = tmp_path / "clip.mp4"
    proc = _mock_proc(returncode=None)  # still running when we abort

    async def _disconnected():
        return True  # client is gone on the first poll

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        with pytest.raises(ClipExportError) as ei:
            await export_clip(
                **_KW,
                out_path=str(out),
                overall_timeout_seconds=100,
                is_disconnected=_disconnected,
            )
    assert "disconnect" in str(ei.value).lower()
    # Graceful teardown: 'q' was written to ffmpeg stdin.
    proc.stdin.write.assert_any_call(b"q")


@pytest.mark.asyncio
async def test_export_timeout_raises(tmp_path):
    out = tmp_path / "clip.mp4"
    proc = _mock_proc(returncode=None)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        with pytest.raises(ClipExportError) as ei:
            # Negative timeout → deadline already passed on the first loop check.
            await export_clip(**_KW, out_path=str(out), overall_timeout_seconds=-1)
    assert "timed out" in str(ei.value).lower()
    proc.stdin.write.assert_any_call(b"q")  # torn down gracefully on timeout too


@pytest.mark.asyncio
async def test_export_error_redacts_credentials(tmp_path):
    """Contract #12: a credentialed RTSP URL in ffmpeg stderr must be redacted
    before it lands in the ClipExportError message."""
    out = tmp_path / "clip.mp4"
    out.write_bytes(b"")  # empty → error path embeds stderr
    stderr = (
        b"rtsp://admin:secret@10.10.1.15:554/cam/playback?channel=1: 401 Unauthorized\n"
    )
    proc = _mock_proc(returncode=1, stderr=stderr)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        with pytest.raises(ClipExportError) as ei:
            await export_clip(**_KW, out_path=str(out), overall_timeout_seconds=100)
    msg = str(ei.value)
    assert "secret" not in msg
    assert "admin:secret" not in msg
    assert "***" in msg
