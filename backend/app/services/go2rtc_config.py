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


def write_streams(path: str, desired: dict[str, str]) -> None:
    """Replace the `streams:` section with `desired` (one source per stream),
    preserving every other section. Atomic via temp-file + os.replace."""
    cfg = _load(path)
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


# ── WebRTC ICE candidate rendering (portability) ─────────────────────────────
#
# go2rtc needs explicit WebRTC candidates = the server's VIEWER-FACING LAN IP(s)
# or the fullscreen main won't connect on the LAN. These are deploy-specific, so
# they must NOT live in the committed go2rtc template. We resolve them from the
# GO2RTC_WEBRTC_CANDIDATES setting, or AUTO-DETECT the box's private-LAN IPv4(s)
# when it's empty, and render them into the runtime .go2rtc/go2rtc.yaml.


def resolve_webrtc_candidates(settings) -> list[str]:
    """Return the list of `host:port` WebRTC candidates for this deploy.

    Priority: the explicit GO2RTC_WEBRTC_CANDIDATES setting (comma-separated),
    else auto-detected private-LAN IPv4(s) as `<ip>:<go2rtc_webrtc_port>`. A
    bare host with no `:port` gets the configured WebRTC port appended.
    """
    port = int(getattr(settings, "go2rtc_webrtc_port", 8556))
    raw = (getattr(settings, "go2rtc_webrtc_candidates", "") or "").strip()
    if raw:
        out: list[str] = []
        for part in raw.split(","):
            cand = part.strip()
            if not cand:
                continue
            # Append the default port if the operator gave a bare host. IPv6 is
            # not used for these LAN candidates, so a lone ':' means host:port.
            if ":" not in cand:
                cand = f"{cand}:{port}"
            if cand not in out:
                out.append(cand)
        return out

    # Auto-detect: primary + any extra private-LAN IPv4s.
    from app.net import detect_lan_ipv4s

    return [f"{ip}:{port}" for ip in detect_lan_ipv4s()]


def render_runtime_config(base_path: str, out_path: str, settings) -> list[str]:
    """Render the runtime go2rtc.yaml from the committed template.

    Copies every section from `base_path` verbatim, then injects the resolved
    WebRTC candidates into `webrtc.candidates` (removing the key entirely when
    none resolve, so go2rtc falls back to its own auto-advertise). Returns the
    candidate list actually written (for logging). Called by the launcher BEFORE
    go2rtc starts (go2rtc reads candidates once, at process start)."""
    cfg = _load(base_path)
    candidates = resolve_webrtc_candidates(settings)
    webrtc = cfg.get("webrtc")
    if not isinstance(webrtc, dict):
        webrtc = {}
    if candidates:
        webrtc["candidates"] = candidates
    else:
        webrtc.pop("candidates", None)
        log.warning(
            "go2rtc render: no LAN IPv4 detected and GO2RTC_WEBRTC_CANDIDATES "
            "unset — WebRTC candidates left to go2rtc auto-advertise"
        )
    cfg["webrtc"] = webrtc

    parent = os.path.dirname(out_path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = f"{out_path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            cfg, f, default_flow_style=False, sort_keys=False,
            allow_unicode=True, width=1_000_000,
        )
    os.replace(tmp, out_path)
    log.info("rendered go2rtc config %s → %s (candidates=%s)",
             base_path, out_path, candidates or "auto")
    return candidates


def main(argv: list[str] | None = None) -> None:
    """CLI: render the runtime go2rtc.yaml. Run by the launchers before starting
    go2rtc, so the WebRTC candidates are correct for THIS box.

        python -m app.services.go2rtc_config --base ../go2rtc.base.yaml \
            --out ../.go2rtc/go2rtc.yaml
    """
    import argparse

    from app.settings import get_settings

    parser = argparse.ArgumentParser(description="Render runtime go2rtc.yaml")
    parser.add_argument("--base", required=True, help="path to go2rtc.base.yaml")
    parser.add_argument("--out", required=True, help="runtime .go2rtc/go2rtc.yaml")
    args = parser.parse_args(argv)

    cands = render_runtime_config(args.base, args.out, get_settings())
    print(f"go2rtc config rendered -> {args.out}")
    print(f"  WebRTC candidates: {', '.join(cands) if cands else '(auto-advertise)'}")


if __name__ == "__main__":
    main()
