"""File-based go2rtc stream sync (for re-encode / exec sources).

go2rtc refuses `exec:`/`ffmpeg:` (subprocess) sources pushed over its HTTP API —
"source from insecure producer" — and only honours them from its static YAML.
So when re-encoding is on, we can't PUT streams via the API; we write the whole
`streams:` section into go2rtc.yaml and reload go2rtc.

This module owns only the `streams:` key — every other section (api, rtsp,
webrtc, ffmpeg, log) is read and written back untouched. Idempotent: callers
compare desired vs current and only write + reload on a real change, so viewers
aren't dropped on a no-op reconcile.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import yaml

log = logging.getLogger("dss.go2rtc_config")


def _load(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def read_streams(path: str) -> dict[str, str]:
    """Return {name: source} from the config's `streams:` section. A stream value
    may be a bare string or a list of producers — we normalise to the first
    source (DSS publishes exactly one source per stream)."""
    streams = _load(path).get("streams") or {}
    out: dict[str, str] = {}
    for name, val in streams.items():
        if isinstance(val, list):
            out[name] = str(val[0]) if val else ""
        elif isinstance(val, dict):
            # go2rtc can store {producers: [...], ...}; take the first producer.
            prods = val.get("producers") or []
            out[name] = str(prods[0]) if prods else ""
        else:
            out[name] = str(val)
    return out


def ensure_ffmpeg_section(cfg: dict[str, Any], ffmpeg_bin: str | None) -> None:
    """Merge the `ffmpeg:` section needed by UDP main sources (`ffmpeg:…#input=
    rtspudp`): set `bin` (ffmpeg isn't on PATH on the server) and the `rtspudp`
    input template. Preserves any other ffmpeg keys/templates already present."""
    from app.services.go2rtc_reencode import FFMPEG_UDP_INPUT_TEMPLATE

    ff = cfg.get("ffmpeg")
    if not isinstance(ff, dict):
        ff = {}
    if ffmpeg_bin:
        ff["bin"] = ffmpeg_bin
    ff["rtspudp"] = FFMPEG_UDP_INPUT_TEMPLATE
    cfg["ffmpeg"] = ff


def write_streams(path: str, desired: dict[str, str], *, ffmpeg_bin: str | None = None) -> None:
    """Replace the `streams:` section with `desired` (one source per stream),
    preserving every other section. Atomic via temp-file + os.replace.

    `ffmpeg_bin` (when given) also ensures the `ffmpeg:` section the UDP main
    sources depend on."""
    cfg = _load(path)
    if ffmpeg_bin is not None:
        ensure_ffmpeg_section(cfg, ffmpeg_bin)
    # List form (`- src`) matches go2rtc's own persisted style and stays valid
    # for exec sources whose string contains characters YAML would otherwise
    # need to quote — pyyaml handles the quoting.
    cfg["streams"] = {name: [src] for name, src in sorted(desired.items())}
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        # width huge → never line-wrap the long exec: command (keep one source per
        # line so go2rtc's YAML parser never has to fold a scalar).
        yaml.safe_dump(
            cfg, f, default_flow_style=False, sort_keys=False,
            allow_unicode=True, width=1_000_000,
        )
    os.replace(tmp, path)
    log.info("wrote %d streams to %s", len(desired), path)
