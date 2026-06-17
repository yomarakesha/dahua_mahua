"""Reconcile DB cameras → go2rtc streams.

Reuses path_sync._desired_paths (the single source of truth for which streams
exist and their RTSP sources — sub `{nvr}_ch{N}`, direct main `{nvr}_ch{N}_main`,
via-NVR `{nvr}_ch{N}_main_nvr`) and pushes them into go2rtc instead of MediaMTX.
go2rtc stream names == MediaMTX path names, so the frontend's path logic is
unchanged; only the delivery transport differs (buffered MSE vs WebRTC).
"""

from __future__ import annotations

import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.go2rtc_api import Go2rtcClient, Go2rtcError, get_client
from app.services.path_sync import _desired_paths, _is_dss_managed

log = logging.getLogger("dss.go2rtc_sync")


async def reconcile(
    session: AsyncSession,
    *,
    client: Go2rtcClient | None = None,
    delete_orphans: bool = True,
) -> dict:
    """Add/update/delete go2rtc streams to match the DB. Idempotent."""
    client = client or get_client()
    t0 = time.perf_counter()
    desired = await _desired_paths(session)  # {name: {"source": url, ...}}

    try:
        existing = await client.list_streams()  # {name: src}
    except Exception as e:  # noqa: BLE001
        log.error("go2rtc reconcile aborted — cannot list streams: %s", e)
        return {"error": str(e)}

    added = updated = deleted = errors = 0
    for name, cfg in desired.items():
        src = cfg["source"]
        if existing.get(name) == src:
            continue  # already correct — don't disturb active viewers
        try:
            await client.set_stream(name, src)
            if name in existing:
                updated += 1
            else:
                added += 1
        except Go2rtcError as e:
            log.warning("go2rtc set %s failed: %s", name, e)
            errors += 1

    if delete_orphans:
        for name in existing.keys() - desired.keys():
            if not _is_dss_managed(name):
                continue  # leave hand-added streams alone
            try:
                await client.delete_stream(name)
                deleted += 1
            except Go2rtcError as e:
                log.warning("go2rtc delete %s failed: %s", name, e)
                errors += 1

    dt = (time.perf_counter() - t0) * 1000
    log.info(
        "go2rtc reconcile in %.0fms: added=%d updated=%d deleted=%d errors=%d (desired=%d)",
        dt, added, updated, deleted, errors, len(desired),
    )
    return {"added": added, "updated": updated, "deleted": deleted, "errors": errors}


async def remove_streams_for_nvr(session: AsyncSession, nvr_id: str) -> None:
    """Best-effort cleanup of an NVR's streams (mirror of path_sync helper)."""
    client = get_client()
    try:
        existing = await client.list_streams()
    except Exception as e:  # noqa: BLE001
        log.warning("Cannot reach go2rtc during NVR delete cleanup: %s", e)
        return
    prefix = f"{nvr_id}_ch"
    for name in list(existing):
        if name.startswith(prefix):
            try:
                await client.delete_stream(name)
            except Go2rtcError:
                pass
