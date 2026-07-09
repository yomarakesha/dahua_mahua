"""One-shot ffmpeg snapshot — extract a single JPEG frame from an NVR recording.

Uses TCP transport (reliability over speed — one-shot, Contract #10) and pipes
JPEG bytes to stdout.  Credential hygiene (Contract #12): the credentialed RTSP
URL is passed directly to ffmpeg argv and is never logged or returned to callers.
"""

from __future__ import annotations

import asyncio
import logging

from app.services.playback.url_builder import (
    build_playback_url,
    epoch_to_nvr_local,
    redact_url,
)

log = logging.getLogger("dss.playback.snapshot")

__all__ = ["SnapshotError", "grab_frame", "build_snapshot_argv"]


class SnapshotError(Exception):
    """Raised when ffmpeg fails to extract a frame."""


def build_snapshot_argv(
    ffbin: str,
    rtsp_url: str,
    quality: int = 4,           # JPEG quality (ffmpeg -q:v, 1=best, 31=worst)
) -> list[str]:
    """Build ffmpeg argv for single-frame JPEG extraction.

    Output: JPEG bytes on stdout (pipe:1).
    Stops after the first output frame (-frames:v 1).
    Uses TCP transport (snapshot is one-shot; reliability > speed, Contract #10).

    Args:
        ffbin:    Path to the ffmpeg binary (no spaces in any element).
        rtsp_url: Credentialed RTSP URL — caller must NOT log this value
                  (Contract #12).
        quality:  JPEG quality level passed as ``-q:v``; 1 = best,
                  31 = worst (ffmpeg default scale).

    Returns:
        A ``list[str]`` suitable for ``asyncio.create_subprocess_exec(*argv)``.
        Each element is a single token with no embedded spaces.
    """
    return [
        ffbin,
        "-nostdin", "-loglevel", "error",
        "-rtsp_transport", "tcp",   # TCP for snapshot (one-shot; reliability > speed)
        "-i", rtsp_url,
        "-frames:v", "1",
        "-f", "image2",
        "-vcodec", "mjpeg",
        "-q:v", str(quality),
        "pipe:1",
    ]


async def grab_frame(
    ip: str,
    rtsp_port: int,
    user: str,
    pw: str,
    channel: int,
    footage_epoch: int,
    tz_offset_minutes: int,
    ffbin: str,
    timeout_seconds: float = 15.0,
) -> bytes:
    """Extract one JPEG frame at *footage_epoch* from the NVR recording.

    Opens a 10-second RTSP window (start=epoch, end=epoch+10) so the NVR has a
    target segment to seek into.  The credentialed RTSP URL is passed directly
    to the ffmpeg argv and is never logged.

    Args:
        ip:                NVR IP address (from DB row — no SSRF).
        rtsp_port:         NVR RTSP port (Contract #9: ``nvr.port``).
        user:              NVR RTSP username.
        pw:                NVR RTSP password (plain-text, never logged).
        channel:           1-based camera channel number (caller-validated).
        footage_epoch:     UTC seconds of the frame to extract (caller-validated,
                           must be positive).
        tz_offset_minutes: Fixed NVR timezone offset east of UTC (minutes).
        ffbin:             Path to the ffmpeg binary.
        timeout_seconds:   Hard timeout for the subprocess; process is killed on
                           expiry (default 15 s).

    Returns:
        Raw JPEG bytes.

    Raises:
        SnapshotError: ffmpeg timed out, returned a non-zero exit code, or
                       produced empty stdout.
    """
    start = epoch_to_nvr_local(footage_epoch, tz_offset_minutes)
    end = epoch_to_nvr_local(footage_epoch + 10, tz_offset_minutes)
    rtsp_url = build_playback_url(ip, rtsp_port, user, pw, channel, start, end)
    argv = build_snapshot_argv(ffbin, rtsp_url)

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_seconds
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:  # noqa: BLE001
            pass
        raise SnapshotError(
            f"Snapshot timed out after {timeout_seconds}s"
        )
    except Exception as exc:
        raise SnapshotError(f"Snapshot process error: {exc}") from exc

    if not stdout:
        rc = proc.returncode
        # Redact any credentialed RTSP URL that ffmpeg may print in stderr
        # (Contract #12: passwords must never appear in error messages or logs).
        raw_err = stderr.decode(errors="replace")[:500] if stderr else ""
        err_text = redact_url(raw_err)
        raise SnapshotError(
            f"Snapshot ffmpeg returned empty output (rc={rc}): {err_text}"
        )
    return stdout
