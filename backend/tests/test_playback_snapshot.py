"""TDD tests for snapshot.py — build_snapshot_argv and grab_frame.

Step 9.1: build_snapshot_argv assertions (sync, no network).
Step 9.2: grab_frame timeout/error handling (async, fully mocked).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.playback.snapshot import SnapshotError, build_snapshot_argv, grab_frame

# ── build_snapshot_argv ───────────────────────────────────────────────────────


def test_ffbin_is_first_element():
    """ffbin must be the first element of the argv list."""
    argv = build_snapshot_argv("ffmpeg", "rtsp://cam.example.com/stream")
    assert argv[0] == "ffmpeg"


def test_frames_v_1_present():
    """-frames:v 1 must appear in argv (stop after the first output frame)."""
    argv = build_snapshot_argv("ffmpeg", "rtsp://cam.example.com/stream")
    assert "-frames:v" in argv
    assert argv[argv.index("-frames:v") + 1] == "1"


def test_f_image2_present():
    """-f image2 must be in argv (force image2 muxer for single-frame JPEG)."""
    argv = build_snapshot_argv("ffmpeg", "rtsp://cam.example.com/stream")
    assert "-f" in argv
    assert argv[argv.index("-f") + 1] == "image2"


def test_vcodec_mjpeg_present():
    """-vcodec mjpeg must be in argv (JPEG encoder)."""
    argv = build_snapshot_argv("ffmpeg", "rtsp://cam.example.com/stream")
    assert "-vcodec" in argv
    assert argv[argv.index("-vcodec") + 1] == "mjpeg"


def test_pipe1_is_last_element():
    """pipe:1 must be the last element (write JPEG bytes to stdout)."""
    argv = build_snapshot_argv("ffmpeg", "rtsp://cam.example.com/stream")
    assert argv[-1] == "pipe:1"


def test_rtsp_transport_tcp():
    """-rtsp_transport tcp must be in argv (NOT udp — snapshot uses TCP for reliability)."""
    argv = build_snapshot_argv("ffmpeg", "rtsp://cam.example.com/stream")
    assert "-rtsp_transport" in argv
    idx = argv.index("-rtsp_transport")
    assert argv[idx + 1] == "tcp", "snapshot must use TCP transport (Contract #10)"


def test_quality_default_4():
    """Default quality=4 → -q:v 4 in argv."""
    argv = build_snapshot_argv("ffmpeg", "rtsp://cam.example.com/stream", quality=4)
    assert "-q:v" in argv
    assert argv[argv.index("-q:v") + 1] == "4"


def test_no_element_contains_space():
    """No element in the argv list may contain a space (argv, not shell string)."""
    url = "rtsp://admin:pa%40ss%2Aword@10.10.1.15:554/cam/playback?channel=1&starttime=t&endtime=t"
    argv = build_snapshot_argv("/usr/bin/ffmpeg", url, quality=4)
    for elem in argv:
        assert " " not in elem, f"argv element contains a space: {elem!r}"


def test_quality_1_in_argv():
    """quality=1 → -q:v 1 in argv (best JPEG quality)."""
    argv = build_snapshot_argv("ffmpeg", "rtsp://cam.example.com/stream", quality=1)
    assert argv[argv.index("-q:v") + 1] == "1"


# ── grab_frame error handling ─────────────────────────────────────────────────

_GRAB_KWARGS = dict(
    ip="10.10.1.15",
    rtsp_port=554,
    user="admin",
    pw="secret",
    channel=1,
    footage_epoch=1_719_734_400,
    tz_offset_minutes=0,
    ffbin="ffmpeg",
)


@pytest.mark.asyncio
async def test_grab_frame_empty_stdout_raises_snapshot_error():
    """When ffmpeg returns empty stdout, grab_frame must raise SnapshotError."""
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        # wait_for returns (stdout, stderr); empty stdout triggers the error path.
        with patch("asyncio.wait_for", new=AsyncMock(return_value=(b"", b"some ffmpeg error"))):
            with pytest.raises(SnapshotError):
                await grab_frame(**_GRAB_KWARGS)


@pytest.mark.asyncio
async def test_grab_frame_timeout_raises_snapshot_error_with_timed_out():
    """asyncio.TimeoutError from wait_for → SnapshotError containing 'timed out'."""
    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        with patch("asyncio.wait_for", new=AsyncMock(side_effect=asyncio.TimeoutError())):
            with pytest.raises(SnapshotError) as exc_info:
                await grab_frame(**_GRAB_KWARGS)
            assert "timed out" in str(exc_info.value).lower()
            # The process must have been killed on timeout.
            mock_proc.kill.assert_called_once()


# ── Contract #12: credential redaction in SnapshotError ───────────────────────


@pytest.mark.asyncio
async def test_grab_frame_stderr_credentials_are_redacted_in_snapshot_error():
    """Contract #12: if ffmpeg stderr contains a credentialed RTSP URL, the
    resulting SnapshotError must NOT expose the password — it must be redacted
    to *** before being embedded in the exception message."""
    credentialed_stderr = (
        b"rtsp://admin:secret@10.10.1.15:554/cam/playback?channel=1: "
        b"401 Unauthorized\n"
    )
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        with patch(
            "asyncio.wait_for",
            new=AsyncMock(return_value=(b"", credentialed_stderr)),
        ):
            with pytest.raises(SnapshotError) as exc_info:
                await grab_frame(**_GRAB_KWARGS)

    err_msg = str(exc_info.value)
    # Password must not appear in the error message.
    assert "secret" not in err_msg, "credential 'secret' leaked into SnapshotError"
    assert "admin:secret" not in err_msg, "credential pair leaked into SnapshotError"
    # Redaction marker must be present to show something was there.
    assert "***" in err_msg, "expected redaction marker '***' in SnapshotError"
