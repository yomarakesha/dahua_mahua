"""Anti-freeze re-encode for go2rtc sources.

Cameras ship a ~2s GOP; on any jitter the picture freezes up to 2s waiting for
the next keyframe. Re-encoding each stream to a short forced keyframe interval
(default 0.5s) cuts recovery to a blink — the single change that made 4MP stable
pre-redesign (then MediaMTX `runOnDemand`; here a go2rtc `exec:ffmpeg` source).

This wraps a raw RTSP source URL into a go2rtc `exec:` ffmpeg command that pulls
the camera and republishes a short-GOP H.264 stream into go2rtc's `{output}`
sink. go2rtc runs sources on-demand (process starts on first viewer, stops after
the last), so only streams actually being watched are encoded — concurrency is
bounded by viewers, not the full channel count.

Driven by Settings.reencode_* (off by default; on the server set
REENCODE_ENABLED=true + REENCODE_VCODEC=h264_qsv for Intel QuickSync).

IMPORTANT: go2rtc splits the `exec:` command on spaces and has no shell, so no
token may contain a space. The RTSP source URL and the `expr:` keyframe
expression have none — keep it that way.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from app.models import StreamQuality

log = logging.getLogger("dss.go2rtc_reencode")

# Preference order for `vcodec="auto"`. Hardware first (cheap), CPU last (always
# works). A codec may be compiled into ffmpeg yet fail at runtime with no GPU, so
# we don't trust the encoder *list* — we run a tiny real encode to confirm.
_HW_CANDIDATES = ("h264_qsv", "h264_nvenc", "h264_vaapi")
_CPU_FALLBACK = "libx264"

_resolved_vcodec: str | None = None  # cached across reconciles (probe once)


def _test_encoder(ffbin: str, vcodec: str) -> bool:
    """True if a 0.2s synthetic encode with `vcodec` actually succeeds."""
    try:
        proc = subprocess.run(
            [
                ffbin, "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-t", "0.2",
                "-c:v", vcodec, "-f", "null", "-",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def resolve_vcodec(settings: Any) -> str:
    """Return the effective encoder. An explicit `reencode_vcodec` is used as-is;
    `"auto"` probes the host once (real test-encode) and caches the winner —
    falling back to CPU libx264 when no hardware encoder actually works."""
    global _resolved_vcodec
    want = (settings.reencode_vcodec or "auto").lower()
    if want != "auto":
        return want
    if _resolved_vcodec is not None:
        return _resolved_vcodec
    ffbin = settings.reencode_ffmpeg_bin or "ffmpeg"
    chosen = _CPU_FALLBACK
    for vc in _HW_CANDIDATES:
        if _test_encoder(ffbin, vc):
            chosen = vc
            break
    _resolved_vcodec = chosen
    log.info("re-encode vcodec auto-resolved to %s", chosen)
    return chosen


def reset_vcodec_cache() -> None:
    """Drop the cached auto-resolved codec (e.g. after a hardware change)."""
    global _resolved_vcodec
    _resolved_vcodec = None


def quality_of_stream(name: str) -> StreamQuality:
    """Infer quality from a DSS stream name: `…_main` / `…_main_nvr` are main,
    everything else (`{nvr}_chN`) is sub."""
    if name.endswith("_main") or name.endswith("_main_nvr"):
        return StreamQuality.main
    return StreamQuality.sub


def reencode_enabled_for(settings: Any, quality: StreamQuality) -> bool:
    """True if re-encoding is enabled for this stream quality."""
    if not settings.reencode_enabled:
        return False
    want = (settings.reencode_qualities or "sub").lower()
    if want == "both":
        return True
    if quality == StreamQuality.main:
        return want == "main"
    return want == "sub"


def _encoder_flags(settings: Any) -> str:
    """Encoder-specific output flags for low-latency, faithful to the pre-redesign
    `path_sync._reencode_cmd` (commit 3712cc6). vcodec is resolved (auto → probed)."""
    vcodec = resolve_vcodec(settings)
    preset = settings.reencode_preset or "veryfast"
    if vcodec == "libx264":
        return f"-c:v libx264 -preset {preset} -tune zerolatency"
    if vcodec.endswith("_qsv"):
        return f"-c:v {vcodec} -async_depth 1"
    if vcodec.endswith("_nvenc"):
        return f"-c:v {vcodec} -preset p1 -tune ll -delay 0"
    return f"-c:v {vcodec}"


def reencode_source(rtsp_url: str, settings: Any) -> str:
    """Build the go2rtc `exec:ffmpeg` source that re-encodes `rtsp_url` to a short
    GOP and republishes into go2rtc's `{output}` RTSP sink.

    `-force_key_frames expr:gte(t,n_forced*kf)` forces a keyframe every `kf`
    seconds regardless of the source GOP/framerate; `-bf 0` drops B-frames."""
    kf = settings.reencode_keyframe_seconds
    ffbin = settings.reencode_ffmpeg_bin or "ffmpeg"
    enc = _encoder_flags(settings)
    return (
        f"exec:{ffbin} -nostdin -loglevel error -rtsp_transport tcp "
        f"-i {rtsp_url} -an {enc} "
        f"-force_key_frames expr:gte(t,n_forced*{kf}) -bf 0 -pix_fmt yuv420p "
        "-f rtsp -rtsp_transport tcp {output}"
    )


def build_go2rtc_source(name: str, rtsp_url: str, settings: Any) -> str:
    """Return the go2rtc source for a DSS stream: a re-encode `exec:` command when
    re-encoding is enabled for this stream's quality, else the raw RTSP URL."""
    if reencode_enabled_for(settings, quality_of_stream(name)):
        return reencode_source(rtsp_url, settings)
    return rtsp_url
