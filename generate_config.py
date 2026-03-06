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


def load_inventory(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def build_rtsp_url(nvr: dict, channel: int, defaults: dict, subtype: int) -> str:
    username = quote(nvr.get("username", defaults.get("default_username", "admin")), safe="")
    password = quote(nvr.get("password", defaults.get("default_password", "admin")), safe="")
    ip = nvr["ip"]
    port = nvr.get("port", defaults.get("default_port", 554))
    return (
        f"rtsp://{username}:{password}@{ip}:{port}"
        f"/cam/realmonitor?channel={channel}&subtype={subtype}"
    )


def get_server_ips(inventory: dict) -> list:
    """Collect unique NVR subnet IPs to guess the server's LAN interfaces."""
    subnets = set()
    for nvr in inventory.get("nvrs", []):
        parts = nvr["ip"].rsplit(".", 1)
        if len(parts) == 2:
            subnets.add(parts[0])
    return sorted(subnets)


def generate_config(inventory: dict, subtype_override: int | None = None) -> str:
    defaults = inventory.get("global", {})
    subtype = subtype_override if subtype_override is not None else defaults.get("default_subtype", 1)
    subnets = get_server_ips(inventory)

    lines = [
        "###############################################",
        "# MediaMTX Configuration - Auto-generated",
        f"# NVRs: {len(inventory['nvrs'])}",
        f"# Grid stream: {'sub-stream' if subtype == 1 else 'main-stream'} (+ main-stream for fullscreen)",
        "###############################################",
        "",
        "# Global settings",
        "logLevel: warn",
        "logDestinations: [stdout]",
        "",
        "# Performance tuning",
        "readBufferCount: 1024",
        "writeQueueSize: 4096",
        "",
        "# Metrics (monitor stream health at :9998/metrics)",
        "metrics: yes",
        "metricsAddress: :9998",
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
        "# Single muxed port for all WebRTC UDP — fewer firewall issues",
        "webrtcLocalUDPAddress: :8189",
        "webrtcLocalTCPAddress: :8189",
        "# LAN IPs — prevents ICE gathering failures on local network",
        "webrtcAdditionalHosts:",
    ]

    # Add discovered LAN subnets as hints (user should set actual server IP)
    if subnets:
        for subnet in subnets:
            lines.append(f"  - {subnet}.X  # <-- replace X with your server's IP on this subnet")
    else:
        lines.append("  - 192.168.1.X  # <-- replace with your server's LAN IP")

    lines += [
        "",
        "# HLS server (fallback)",
        "hlsAddress: :8888",
        "hlsVariant: lowLatency",
        "hlsSegmentCount: 3",
        "hlsSegmentDuration: 1s",
        "hlsPartDuration: 200ms",
        "# Save HLS segments to disk instead of RAM (important with many cameras)",
        "hlsDirectory: /tmp/mediamtx-hls",
        "",
        "# Pull streams on demand — close after 5 min idle to reduce RTSP churn",
        "",
        "paths:",
    ]

    total_channels = 0

    for nvr in inventory["nvrs"]:
        nvr_id = nvr["id"]
        label = nvr.get("label", nvr_id)
        channels = nvr.get("channels", 1)
        group = nvr.get("group", "default")

        lines.append(f"  # --- {label} ({nvr['ip']}, {channels} ch, group: {group}) ---")

        for ch in range(1, channels + 1):
            # Sub-stream (default — used in grid view)
            path_name = f"{nvr_id}_ch{ch}"
            url = build_rtsp_url(nvr, ch, defaults, subtype)
            lines.append(f"  {path_name}:")
            lines.append(f"    source: {url}")
            lines.append(f"    rtspTransport: tcp")
            lines.append(f"    sourceOnDemand: yes")
            lines.append(f"    sourceOnDemandCloseAfter: 5m")

            # Main-stream (used for fullscreen view)
            main_path = f"{nvr_id}_ch{ch}_main"
            main_url = build_rtsp_url(nvr, ch, defaults, 0)
            lines.append(f"  {main_path}:")
            lines.append(f"    source: {main_url}")
            lines.append(f"    rtspTransport: tcp")
            lines.append(f"    sourceOnDemand: yes")
            lines.append(f"    sourceOnDemandCloseAfter: 5m")

            total_channels += 1

        lines.append("")

    lines.insert(3, f"# Total channels: {total_channels}")

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

    nvr_count = len(inventory["nvrs"])
    ch_count = sum(n.get("channels", 1) for n in inventory["nvrs"])
    print(f"Generated {args.output}: {nvr_count} NVRs, {ch_count} channels")


if __name__ == "__main__":
    main()
