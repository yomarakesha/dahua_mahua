"""Import camera IPs from an NVR's connected-device list.

Dahua NVRs expose their remote-device table over HTTP CGI:
`configManager.cgi?action=getConfig&name=RemoteDevice` (digest auth) returns
one `key=value` line per field, grouped per slot:

    table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_<slot>.Address=192.168.23.11
    table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_<slot>.Enable=true
    ...

Slot is 0-based and maps to NVR channel slot+1. Unused slots are present but
carry Address=192.168.0.0 / Enable=false.

Why this exists: the NVR's RTSP relay drops packets on main streams (measured
§3.1, docs/audit-plan.md §9), so `path_sync` pulls main straight from the
camera whenever `Camera.ip` is known. This module keeps those IPs filled in.
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import decrypt_password
from app.models import Camera, Nvr, Vendor
from app.services.rtsp_probe import probe_rtsp

log = logging.getLogger("dss.camera_import")

# Addresses Dahua firmware uses as "slot is empty" placeholders.
_PLACEHOLDER_IPS = {"", "0.0.0.0", "192.168.0.0"}

_LINE_RE = re.compile(
    r"table\.RemoteDevice\.uuid:System_CONFIG_NETCAMERA_INFO_(\d+)"
    r"\.(Address|Enable)=(.*)"
)


def parse_remote_devices(text: str) -> dict[int, str]:
    """Parse a RemoteDevice config dump into {nvr_channel: camera_ip}.

    Skips placeholder and disabled slots — those are either empty or
    deliberately turned off on the NVR, so we must not route main streams
    at them.
    """
    slots: dict[int, dict[str, str]] = {}
    for line in text.splitlines():
        m = _LINE_RE.match(line.strip())
        if not m:
            continue
        slot, key, val = int(m.group(1)), m.group(2), m.group(3).strip()
        slots.setdefault(slot, {})[key] = val

    out: dict[int, str] = {}
    for slot, fields in slots.items():
        addr = fields.get("Address", "")
        if addr in _PLACEHOLDER_IPS:
            continue
        if fields.get("Enable", "true").lower() != "true":
            continue
        out[slot + 1] = addr
    return out


async def fetch_camera_ips(
    ip: str,
    username: str,
    password: str,
    *,
    http_port: int = 80,
    timeout: float = 8.0,
) -> dict[int, str]:
    """Query the NVR over HTTP CGI and return {channel: camera_ip}.

    Raises httpx.HTTPError / httpx.HTTPStatusError on network or auth
    failures — callers decide whether that's fatal (manual import endpoint)
    or best-effort (NVR create).
    """
    url = (
        f"http://{ip}:{http_port}/cgi-bin/configManager.cgi"
        "?action=getConfig&name=RemoteDevice"
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, auth=httpx.DigestAuth(username, password))
        r.raise_for_status()
    return parse_remote_devices(r.text)


async def _probe_rtsp(
    ip: str,
    *,
    username: str,
    password: str,
    vendor: Vendor | str = Vendor.dahua,
    port: int = 554,
    timeout: float = 1.5,
) -> bool:
    """True only if the camera answers RTSP **and the NVR credentials actually
    authenticate** (digest auth → 200).

    A bare open port is not enough. Two ways a "reachable" camera still yields a
    black screen on direct pull:
      • it sits behind the NVR's PoE switch and never answers directly, or
      • it answers, but its password differs from the NVR's → 401.
    Direct pull reuses the NVR credentials (path_sync: "camera creds mirror the
    NVR's"), so we verify exactly that here. A 401 (wrong password) or 403
    (firmware ban) both mean "do not pull direct" → fall back to the NVR relay.
    Runs the sync digest probe in a thread so it doesn't block the loop."""
    res = await asyncio.to_thread(
        probe_rtsp, ip, port, username, password,
        channel=1, vendor=vendor, timeout=timeout,
    )
    return res.ok


async def filter_reachable(
    ips: set[str],
    *,
    username: str,
    password: str,
    vendor: Vendor | str = Vendor.dahua,
    port: int = 554,
    timeout: float = 1.5,
    concurrency: int = 16,
) -> set[str]:
    """Probe IPs concurrently and return only those that authenticate. Bounded
    concurrency keeps us from opening hundreds of sockets (and from looking like
    a flood to the NVR) when a fleet has many channels."""
    ordered = list(ips)
    if not ordered:
        return set()
    sem = asyncio.Semaphore(concurrency)

    async def _one(ip: str) -> bool:
        async with sem:
            return await _probe_rtsp(
                ip, username=username, password=password,
                vendor=vendor, port=port, timeout=timeout,
            )

    results = await asyncio.gather(*(_one(ip) for ip in ordered))
    return {ip for ip, ok in zip(ordered, results) if ok}


async def apply_camera_ips(session: AsyncSession, nvr: Nvr) -> tuple[int, int]:
    """Fetch the NVR's device list and set `Camera.ip` per channel — but only
    for cameras that actually answer on RTSP.

    Returns `(found, updated)`: channels the NVR reported with a real camera
    IP, and cameras whose stored IP actually changed.

    Reachability-aware: for every channel the NVR reports we set `Camera.ip`
    to the camera's address when it answers on :554 AND authenticates with the
    NVR credentials (main pulls direct), or to `None` when it doesn't (main
    falls back to the NVR relay). A stored-but-dead/401 IP is exactly what
    points _main at nothing and makes the watchdog disable the NVR, so we clear
    those too. Channels the NVR does NOT list are left untouched.
    """
    password = decrypt_password(nvr.rtsp_password_encrypted)
    chan_ips = await fetch_camera_ips(nvr.ip, nvr.rtsp_username, password)
    if not chan_ips:
        log.info("NVR %s: RemoteDevice list is empty — nothing to import", nvr.id)
        return 0, 0

    reachable = await filter_reachable(
        set(chan_ips.values()),
        username=nvr.rtsp_username, password=password, vendor=nvr.vendor,
    )

    cams = list(
        (
            await session.execute(select(Camera).where(Camera.nvr_id == nvr.id))
        ).scalars()
    )
    updated = 0
    direct = 0
    for cam in cams:
        reported = chan_ips.get(cam.channel)
        if reported is None:
            continue  # NVR didn't list this channel — don't touch a manual IP
        target = reported if reported in reachable else None
        if target is not None:
            direct += 1
        if cam.ip != target:
            cam.ip = target
            updated += 1
    await session.commit()
    log.info(
        "NVR %s: camera IP import — %d channel(s) on NVR, %d direct/%d relay, %d updated",
        nvr.id, len(chan_ips), direct, len(chan_ips) - direct, updated,
    )
    return len(chan_ips), updated
