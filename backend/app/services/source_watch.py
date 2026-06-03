"""Source watchdog — disable NVRs whose MediaMTX source keeps failing.

The problem this solves
-----------------------
MediaMTX retries an on-demand RTSP source aggressively while a viewer is
connected. If the stored password is wrong, every retry is a failed digest
auth (the `bad status code: 401` lines in MediaMTX's log). Dahua/Hikvision
firmware bans the *account* after a handful of these — which is how a typo'd
password locks you out of the NVR's own web UI.

How it works
------------
We poll MediaMTX's runtime API (`/v3/paths/list`) every few seconds. For each
DSS-managed path we look at whether the source is up (`ready`) and whether a
viewer is actively pulling it (`readers` present, or a `source` attempt in
flight). An NVR is considered "failing" only when it has active-but-unready
paths and **no** ready path at all — i.e. the failure is account-wide
(wrong password / host down), not one offline camera. After N consecutive
failing polls we set `enabled=False` and yank the NVR's paths, stopping the
retry loop before the firmware lockout triggers.

This runs as a single asyncio task started in the app lifespan. It works
regardless of how MediaMTX is launched (managed child or separate process),
because it only talks to the HTTP API.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Nvr
from app.services import nvr_events, path_sync
from app.services.mediamtx_api import MediaMTXError, get_client
from app.settings import get_settings

log = logging.getLogger("dss.source_watch")

_task: asyncio.Task | None = None


def _parse_path(name: str) -> tuple[str, int] | None:
    """`nvr-192-168-20-34_ch1_main` -> ('nvr-192-168-20-34', 1). Mirrors the
    naming in path_sync.path_name(); returns None for anything not DSS-managed."""
    parts = name.rsplit("_ch", 1)
    if len(parts) != 2:
        return None
    rhs = parts[1]
    if rhs.endswith("_main"):
        rhs = rhs[:-5]
    if not rhs.isdigit():
        return None
    return parts[0], int(rhs)


def _nvr_id_from_path(name: str) -> str | None:
    parsed = _parse_path(name)
    return parsed[0] if parsed else None


async def _disable_camera(nvr_id: str, channel: int, reason: str) -> None:
    """Disable a single channel and drop its MediaMTX paths. Used when one
    channel keeps failing (phantom channel that doesn't exist on the NVR, or a
    camera that's offline) while the rest of the NVR streams fine — so one bad
    channel can't hammer the NVR into a firmware IP-ban."""
    from app.models import Camera

    async with SessionLocal() as session:
        cam = (
            await session.execute(
                select(Camera).where(Camera.nvr_id == nvr_id, Camera.channel == channel)
            )
        ).scalar_one_or_none()
        if cam is None or not cam.enabled:
            return
        nvr = (await session.execute(select(Nvr).where(Nvr.id == nvr_id))).scalar_one_or_none()
        cam.enabled = False
        await session.commit()
        await nvr_events.log_event(
            nvr_id=nvr_id,
            ip=nvr.ip if nvr else "?",
            event_type="camera_auto_disabled",
            message=f"ch{channel}: {reason}",
        )
        # Reconcile removes this camera's now-undesired paths (delete_orphans).
        try:
            await path_sync.reconcile(session, delete_orphans=True)
        except Exception as e:  # noqa: BLE001
            log.warning("reconcile after disabling %s ch%d failed: %s", nvr_id, channel, e)
    log.warning("Auto-disabled camera %s ch%d — %s", nvr_id, channel, reason)


async def _disable_nvr(nvr_id: str, reason: str) -> None:
    """Set enabled=False, log an audit event, and remove the NVR's MediaMTX
    paths so the retry loop stops immediately. Best-effort on the MediaMTX
    side — the DB flag is the source of truth and reconcile honours it."""
    async with SessionLocal() as session:
        nvr = (
            await session.execute(select(Nvr).where(Nvr.id == nvr_id))
        ).scalar_one_or_none()
        if nvr is None or not nvr.enabled:
            return  # already gone / already disabled — nothing to do
        ip = nvr.ip
        nvr.enabled = False
        await session.commit()
        await nvr_events.log_event(
            nvr_id=nvr_id,
            ip=ip,
            event_type="auto_disabled",
            message=reason,
        )
        # Pull paths last — even if MediaMTX is unreachable, the enabled=False
        # flag means the next reconcile won't re-add them.
        await path_sync.remove_paths_for_nvr(session, nvr_id)
    log.warning("Auto-disabled NVR %s — %s", nvr_id, reason)


async def _poll_once(
    nvr_fail: dict[str, int],
    cam_fail: dict[tuple[str, int], int],
    threshold: int,
    cam_threshold: int,
) -> None:
    client = get_client()
    try:
        paths = await client.list_active_paths()
    except (MediaMTXError, httpx.HTTPError) as e:
        # MediaMTX down / restarting / unreachable. Nothing to police this
        # round — keep it to one quiet line, not a traceback every 3s.
        log.debug("source-watch poll skipped (MediaMTX unreachable): %s", type(e).__name__)
        return

    ok_nvrs: set[str] = set()
    # Per-channel state: a channel is "ready" if any of its paths (sub/main)
    # is up, "failing" if a viewer is pulling it but no path is up.
    ch_ready: set[tuple[str, int]] = set()
    ch_active_fail: set[tuple[str, int]] = set()

    for name, item in paths.items():
        parsed = _parse_path(name)
        if parsed is None:
            continue
        nvr_id, channel = parsed
        ready = bool(item.get("ready"))
        readers = item.get("readers") or []
        source = item.get("source")
        # "Active" = a viewer is pulling, or a source attempt is in flight.
        # Idle on-demand paths (nobody watching) are neither ready nor active
        # and must be ignored, or we'd disable a perfectly fine camera.
        active = bool(readers) or source is not None
        if ready:
            ok_nvrs.add(nvr_id)
            ch_ready.add((nvr_id, channel))
        elif active:
            ch_active_fail.add((nvr_id, channel))

    # A channel with any ready path is fine — clear its counter.
    for key in ch_ready:
        cam_fail.pop(key, None)
    for nvr_id in ok_nvrs:
        if nvr_fail.pop(nvr_id, None):
            log.info("source-watch: %s recovered, NVR counter cleared", nvr_id)

    # Group failing channels by NVR.
    failing_by_nvr: dict[str, set[int]] = {}
    for nvr_id, channel in ch_active_fail:
        if (nvr_id, channel) in ch_ready:
            continue  # one path up is enough
        failing_by_nvr.setdefault(nvr_id, set()).add(channel)

    for nvr_id, channels in failing_by_nvr.items():
        if nvr_id in ok_nvrs:
            # NVR streams fine on other channels → creds are OK. These specific
            # channels are phantom (don't exist) or offline. Disable each one
            # so it stops hammering the NVR (which would otherwise 403-ban us).
            for channel in channels:
                key = (nvr_id, channel)
                cam_fail[key] = cam_fail.get(key, 0) + 1
                n = cam_fail[key]
                log.warning("source-watch: %s ch%d failing while NVR healthy (%d/%d)",
                            nvr_id, channel, n, cam_threshold)
                if n >= cam_threshold:
                    await _disable_camera(
                        nvr_id, channel,
                        "channel kept failing (does not exist on the NVR, or "
                        "camera offline). Auto-disabled to avoid an IP ban. "
                        "Re-enable it from the Cams dialog if the camera comes back.",
                    )
                    cam_fail.pop(key, None)
        else:
            # No channel on this NVR is ready → account-wide failure
            # (wrong password / host unreachable). Disable the whole NVR.
            nvr_fail[nvr_id] = nvr_fail.get(nvr_id, 0) + 1
            n = nvr_fail[nvr_id]
            log.warning("source-watch: %s no channel ready while viewers pull it (%d/%d)",
                        nvr_id, n, threshold)
            if n >= threshold:
                await _disable_nvr(
                    nvr_id,
                    "Source kept failing (likely wrong RTSP password or host "
                    "unreachable). Auto-disabled to avoid an NVR account lockout. "
                    "Fix the password via the lock button, then re-enable.",
                )
                nvr_fail.pop(nvr_id, None)


async def _run() -> None:
    settings = get_settings()
    interval = settings.source_watch_interval_seconds
    threshold = settings.source_watch_threshold
    cam_threshold = settings.source_watch_camera_threshold
    nvr_fail: dict[str, int] = {}
    cam_fail: dict[tuple[str, int], int] = {}
    log.info(
        "Source watchdog running (interval=%.1fs nvr_threshold=%d cam_threshold=%d)",
        interval, threshold, cam_threshold,
    )
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                await _poll_once(nvr_fail, cam_fail, threshold, cam_threshold)
            except Exception as e:  # noqa: BLE001 — watchdog must never die
                log.exception("source-watch poll error: %s", e)
    except asyncio.CancelledError:
        log.info("Source watchdog stopped")
        raise


def start() -> None:
    global _task
    settings = get_settings()
    if not settings.source_watch_enabled:
        log.info("Source watchdog disabled via settings")
        return
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_run(), name="source-watch")


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
