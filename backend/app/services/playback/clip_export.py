"""Clip export — pull an NVR ``/cam/playback`` RTSP range to a downloadable MP4.

The router hands a footage ``[start, end]`` window; this module runs a single
ffmpeg process that pulls that range and writes a seekable MP4 to a temp file,
which the endpoint then streams back via ``FileResponse`` and deletes.

Design notes / contracts honoured here:

* **TCP transport (Contract #10)** — export uses TCP, *not* UDP: a dropped UDP
  packet would silently corrupt the saved file.  The NVR delivers TCP playback
  at ~0.2x realtime, so an export pulls in roughly real time (or slower); the
  router caps the requested duration (``clip_export_max_seconds``) so a single
  download can't pin an ffmpeg + NVR playback slot indefinitely.
* **Remux, no re-encode (fast path)** — ``-c copy`` remuxes the recorded H.264
  elementary stream into MP4 with ``-movflags +faststart`` so the ``moov`` atom
  is at the front and the browser can start playing before the full download.
  ``+faststart`` needs a seekable output, which is why we write a temp file
  rather than stream ffmpeg stdout.  ``reencode=True`` switches to a libx264
  re-encode fallback for streams that won't cleanly remux.
* **No orphan ffmpeg (Contract #11)** — on client disconnect, cancellation, or
  timeout the process is torn down GRACEFULLY: we write ``q`` to ffmpeg's stdin
  so it emits an RTSP TEARDOWN (releasing the NVR's small playback pool, exactly
  like ``PlaybackSession._kill_proc``) and only hard-kill if it doesn't quit in
  ``_GRACEFUL_QUIT_SECONDS``.
* **Credential hygiene (Contract #12)** — the credentialed RTSP URL is passed
  directly to the ffmpeg argv and is only ever logged/embedded after
  ``redact_url``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Awaitable, Callable

from app.services.playback.url_builder import (
    build_playback_url,
    epoch_to_nvr_local,
    redact_url,
)

log = logging.getLogger("dss.playback.clip_export")

__all__ = ["ClipExportError", "build_clip_argv", "export_clip"]

# How long to wait for a graceful ffmpeg quit (stdin 'q' → RTSP TEARDOWN) before
# hard-killing on disconnect/timeout.  Mirrors PlaybackSession._GRACEFUL_QUIT.
_GRACEFUL_QUIT_SECONDS = 2.0

# How often the run loop polls the client-disconnect callback while ffmpeg pulls.
_POLL_INTERVAL_SECONDS = 1.0


class ClipExportError(Exception):
    """Raised when the clip export fails — ffmpeg error, timeout, client
    disconnect, or the NVR yielding no data for the requested range.  The
    message never contains an unredacted credentialed URL (Contract #12)."""


def build_clip_argv(
    ffbin: str,
    rtsp_url: str,
    out_path: str,
    *,
    reencode: bool = False,
    transport: str = "tcp",
) -> list[str]:
    """Build the ffmpeg argv for a clip export (list, no shell).

    Output: an MP4 file at *out_path* with ``+faststart`` (seekable download).
    Input transport is TCP by default (reliability over speed; Contract #10).

    ``reencode=False`` (default) remuxes with ``-c copy`` — fast, no CPU, exact
    quality; requires a cleanly-muxable recorded stream.  ``reencode=True``
    re-encodes video to libx264 and drops audio (``-an``) for streams that won't
    remux cleanly.

    Args:
        ffbin:     Path to the ffmpeg binary (no spaces in any element).
        rtsp_url:  Credentialed RTSP URL — caller must NOT log this (Contract #12).
        out_path:  Destination MP4 path (a seekable file — needed for faststart).
        reencode:  Re-encode instead of stream-copy.
        transport: RTSP transport for the input ("tcp" for export reliability).

    Returns:
        A ``list[str]`` suitable for ``asyncio.create_subprocess_exec(*argv)``.
    """
    argv = [
        ffbin,
        # No -nostdin: we send 'q' on stdin for a graceful quit so ffmpeg emits
        # an RTSP TEARDOWN and the NVR releases the playback session on abort.
        "-loglevel", "error",
        "-rtsp_transport", transport,
        "-i", rtsp_url,
    ]
    if reencode:
        # Fallback: re-encode video, drop audio (video-only container).
        argv += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-an"]
    else:
        # Fast path: remux the recorded elementary streams as-is.
        argv += ["-c", "copy"]
    argv += [
        "-movflags", "+faststart",
        "-f", "mp4",
        "-y",            # overwrite the pre-created temp file
        out_path,
    ]
    return argv


async def _teardown(proc: "asyncio.subprocess.Process") -> None:
    """Stop ffmpeg gracefully (stdin 'q' → RTSP TEARDOWN), then ``await`` it.

    Only hard-kills if ffmpeg doesn't quit within ``_GRACEFUL_QUIT_SECONDS`` — a
    hard kill skips the RTSP TEARDOWN and leaks the NVR's playback session until
    its own timeout (the small playback pool then exhausts; see
    PlaybackSession._kill_proc).  Never raises.
    """
    if proc.returncode is not None:
        return
    stdin = getattr(proc, "stdin", None)
    if stdin is not None:
        try:
            if not stdin.is_closing():
                stdin.write(b"q")
                await stdin.drain()
        except Exception:  # noqa: BLE001
            pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=_GRACEFUL_QUIT_SECONDS)
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        except Exception:  # noqa: BLE001
            pass
    try:
        await proc.wait()
    except Exception:  # noqa: BLE001
        pass


async def export_clip(
    *,
    ip: str,
    rtsp_port: int,
    user: str,
    pw: str,
    channel: int,
    start_epoch: int,
    end_epoch: int,
    tz_offset_minutes: int,
    ffbin: str,
    out_path: str,
    overall_timeout_seconds: float,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    reencode: bool = False,
    transport: str = "tcp",
) -> None:
    """Pull ``[start_epoch, end_epoch]`` of channel *channel* into *out_path*.

    Runs one ffmpeg process (TCP, ``-c copy`` + faststart by default) writing the
    MP4 to *out_path*.  While it runs we poll *is_disconnected* every second so a
    client that navigated away tears the export down promptly (no orphan ffmpeg,
    no leaked NVR playback slot).  ``overall_timeout_seconds`` bounds a hung NVR.

    Args:
        ip / rtsp_port / user / pw: NVR RTSP target (from DB row — no SSRF).
        channel:            1-based channel (caller-validated).
        start_epoch/end_epoch: Footage UTC epochs (caller-validated: 0<start<end).
        tz_offset_minutes:  Fixed NVR timezone offset east of UTC (minutes).
        ffbin:              Path to the ffmpeg binary.
        out_path:           Destination MP4 file (caller owns creation/cleanup).
        overall_timeout_seconds: Hard wall-clock cap for the pull; the process is
                            torn down and ClipExportError raised on expiry.
        is_disconnected:    Optional async predicate polled while ffmpeg runs; a
                            truthy result tears the export down.
        reencode:           Re-encode instead of remuxing (fallback).
        transport:          RTSP transport ("tcp" for export reliability).

    Raises:
        ClipExportError: ffmpeg failed, timed out, the client disconnected, or
            the NVR produced no data (empty/missing output).  The credentialed
            URL never appears in the message (Contract #12).
    """
    start_dt = epoch_to_nvr_local(start_epoch, tz_offset_minutes)
    end_dt = epoch_to_nvr_local(end_epoch, tz_offset_minutes)
    # build_playback_url raises PlaybackUrlError on start>=end — caller validates
    # 0<start<end, so this is a defence-in-depth guard, surfaced as ClipExportError.
    try:
        rtsp_url = build_playback_url(ip, rtsp_port, user, pw, channel, start_dt, end_dt)
    except Exception as exc:  # noqa: BLE001
        raise ClipExportError(f"could not build playback URL: {exc}") from exc

    argv = build_clip_argv(ffbin, rtsp_url, out_path, reencode=reencode, transport=transport)
    # Credential hygiene: log only the redacted URL (Contract #12).
    log.info(
        "clip export ch=%d [%d,%d] transport=%s url=%s",
        channel, start_epoch, end_epoch, transport, redact_url(rtsp_url),
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,   # for the graceful 'q' quit
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:  # noqa: BLE001
        raise ClipExportError(f"failed to start ffmpeg: {exc}") from exc

    # Drain stderr concurrently so a full pipe buffer can never block ffmpeg;
    # read() returns the full buffer at EOF (when the process closes stderr).
    stderr_task = asyncio.create_task(proc.stderr.read())

    deadline = time.monotonic() + overall_timeout_seconds
    try:
        while True:
            if is_disconnected is not None:
                try:
                    gone = await is_disconnected()
                except Exception:  # noqa: BLE001
                    gone = False
                if gone:
                    await _teardown(proc)
                    raise ClipExportError("client disconnected during export")
            if time.monotonic() > deadline:
                await _teardown(proc)
                raise ClipExportError(
                    f"export timed out after {overall_timeout_seconds:.0f}s"
                )
            try:
                await asyncio.wait_for(proc.wait(), timeout=_POLL_INTERVAL_SECONDS)
                break  # ffmpeg exited on its own
            except asyncio.TimeoutError:
                continue
    except asyncio.CancelledError:
        # Never leave an orphan on cancellation (e.g. server shutdown).
        await _teardown(proc)
        raise
    finally:
        # Defence in depth: if we left the loop with ffmpeg still alive, tear down.
        if proc.returncode is None:
            await _teardown(proc)

    rc = proc.returncode
    try:
        raw_err = await asyncio.wait_for(stderr_task, timeout=2.0)
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        raw_err = b""
    err_text = redact_url(raw_err.decode(errors="replace"))[:500] if raw_err else ""

    # No data: the NVR yielded nothing for the range (missing or empty output).
    size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    if size == 0:
        raise ClipExportError(
            f"no recorded video for the requested range (ffmpeg rc={rc}): {err_text}"
        )
    if rc not in (0, None):
        # We have bytes but ffmpeg reported an error — log it; the partial file
        # is still likely playable (faststart remux), so we do not fail hard.
        log.warning("clip export ch=%d ffmpeg rc=%d stderr: %s", channel, rc, err_text)
