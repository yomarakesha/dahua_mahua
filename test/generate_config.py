#!/usr/bin/env python3
"""
MediaMTX config generator for Dahua NVRs.

Reads nvr_inventory.json and generates mediamtx.yml with all camera paths.

Usage:
    python generate_config.py
    python generate_config.py --inventory custom_inventory.json --output custom.yml
    python generate_config.py --subtype 0  # main stream (heavy)
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

SOURCE_ON_DEMAND_START_TIMEOUT = "10s"
SOURCE_ON_DEMAND_CLOSE_AFTER = "20s"


def load_inventory(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def build_rtsp_url(nvr: dict, channel: int, defaults: dict, subtype: int) -> str:
    """Build RTSP URL pointing directly at the NVR."""
    username = quote(nvr.get("username", defaults.get("default_username", "admin")), safe="")
    password = quote(nvr.get("password", defaults.get("default_password", "admin")), safe="")
    ip = nvr["ip"]
    port = nvr.get("port", defaults.get("default_port", 554))
    return (
        f"rtsp://{username}:{password}@{ip}:{port}"
        f"/cam/realmonitor?channel={channel}&subtype={subtype}"
    )


def build_server_rtsp_url(server_url: str, nvr_id: str, channel: int, suffix: str = "") -> str:
    """Build RTSP URL pointing at a centralized MediaMTX server."""
    base = server_url.rstrip("/")
    path_name = f"{nvr_id}_ch{channel}{suffix}"
    return f"{base}/{path_name}"


def generate_config(inventory: dict, subtype_override: int | None = None) -> str:
    defaults = inventory.get("global", {})
    subtype = subtype_override if subtype_override is not None else defaults.get("default_subtype", 1)
    stream_source = defaults.get("stream_source", "nvr")
    server_url = defaults.get("server_url", "")

    # Filter to enabled NVRs only
    enabled_nvrs = [n for n in inventory["nvrs"] if n.get("enabled", True)]
    disabled_nvrs = [n for n in inventory["nvrs"] if not n.get("enabled", True)]

    lines = [
        "###############################################",
        "# MediaMTX Configuration - Auto-generated",
        f"# NVRs: {len(enabled_nvrs)} enabled, {len(disabled_nvrs)} disabled",
        f"# Stream source: {stream_source}" + (f" ({server_url})" if stream_source == "server" and server_url else ""),
        f"# Grid stream: {'sub-stream' if subtype == 1 else 'main-stream'} (+ main-stream for fullscreen)",
        "###############################################",
        "",
        "# Global settings",
        "logLevel: warn",
        "logDestinations: [stdout]",
        "",
        "# Performance tuning",
        "writeQueueSize: 4096",
        "",
        "# API (used by web UI to list streams)",
        "api: yes",
        "apiAddress: :9997",
        "",
        "# RTSP server",
        "rtspAddress: :8554",
        "",
        "# WebRTC server (used by web UI for low-latency playback)",
        "webrtcAddress: :8889",
        "",
        "# HLS server (fallback)",
        "hlsAddress: :8888",
        "hlsVariant: lowLatency",
        "hlsSegmentCount: 3",
        "hlsSegmentDuration: 1s",
        "hlsPartDuration: 200ms",
        "",
        "# Pull streams on demand — close quickly to avoid RTSP churn across large pages",
        "",
        "paths:",
    ]

    total_channels = 0
    global_use_server = stream_source == "server" and server_url

    for nvr in enabled_nvrs:
        nvr_id = nvr["id"]
        label = nvr.get("label", nvr_id)
        channels = nvr.get("channels", 1)
        group = nvr.get("group", "default")

        # Per-NVR stream source override
        nvr_source = nvr.get("stream_source", "")
        nvr_server_url = nvr.get("server_url", "")
        if nvr_source == "server" and nvr_server_url:
            use_server_for_nvr = True
            effective_server_url = nvr_server_url
        elif nvr_source == "server" and server_url:
            use_server_for_nvr = True
            effective_server_url = server_url
        elif nvr_source == "nvr":
            use_server_for_nvr = False
            effective_server_url = ""
        else:
            use_server_for_nvr = global_use_server
            effective_server_url = server_url

        src_tag = " [server]" if use_server_for_nvr else ""
        lines.append(f"  # --- {label} ({nvr['ip']}, {channels} ch, group: {group}){src_tag} ---")

        for ch in range(1, channels + 1):
            # Sub-stream (default — used in grid view)
            path_name = f"{nvr_id}_ch{ch}"
            if use_server_for_nvr:
                url = build_server_rtsp_url(effective_server_url, nvr_id, ch)
            else:
                url = build_rtsp_url(nvr, ch, defaults, subtype)
            lines.append(f"  {path_name}:")
            lines.append(f"    source: {url}")
            lines.append(f"    rtspTransport: tcp")
            lines.append(f"    sourceOnDemand: yes")
            lines.append(f"    sourceOnDemandStartTimeout: {SOURCE_ON_DEMAND_START_TIMEOUT}")
            lines.append(f"    sourceOnDemandCloseAfter: {SOURCE_ON_DEMAND_CLOSE_AFTER}")

            # Main-stream (used for fullscreen view)
            main_path = f"{nvr_id}_ch{ch}_main"
            if use_server_for_nvr:
                main_url = build_server_rtsp_url(effective_server_url, nvr_id, ch, "_main")
            else:
                main_url = build_rtsp_url(nvr, ch, defaults, 0)
            lines.append(f"  {main_path}:")
            lines.append(f"    source: {main_url}")
            lines.append(f"    rtspTransport: tcp")
            lines.append(f"    sourceOnDemand: yes")
            lines.append(f"    sourceOnDemandStartTimeout: {SOURCE_ON_DEMAND_START_TIMEOUT}")
            lines.append(f"    sourceOnDemandCloseAfter: {SOURCE_ON_DEMAND_CLOSE_AFTER}")

            total_channels += 1

        lines.append("")

    if disabled_nvrs:
        lines.append(f"  # --- DISABLED NVRs ({len(disabled_nvrs)}) ---")
        for nvr in disabled_nvrs:
            lines.append(f"  # {nvr['id']}: {nvr.get('label', nvr['id'])} ({nvr['ip']}, {nvr.get('channels', 1)} ch) — DISABLED")
        lines.append("")

    lines.insert(5, f"# Total channels: {total_channels}")

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Generate mediamtx.yml from NVR inventory")
    parser.add_argument("--inventory", "-i", default="nvr_inventory.json", help="Path to inventory JSON")
    parser.add_argument("--output", "-o", default="mediamtx.yml", help="Output YAML path")
    parser.add_argument("--subtype", "-s", type=int, choices=[0, 1], default=None,
                        help="Stream type: 0=main, 1=sub (default: from inventory)")
    args = parser.parse_args()

    if not Path(args.inventory).exists():
        print(f"Error: inventory file not found: {args.inventory}", file=sys.stderr)
        sys.exit(1)

    inventory = load_inventory(args.inventory)
    config = generate_config(inventory, args.subtype)

    with open(args.output, "w") as f:
        f.write(config)

    enabled = [n for n in inventory["nvrs"] if n.get("enabled", True)]
    disabled = len(inventory["nvrs"]) - len(enabled)
    ch_count = sum(n.get("channels", 1) for n in enabled)
    msg = f"Generated {args.output}: {len(enabled)} NVRs, {ch_count} channels"
    if disabled:
        msg += f" ({disabled} disabled)"
    print(msg)


if __name__ == "__main__":
    main()
