"""Relay-aware reconcile dispatch.

Runtime inventory edits (add/edit/delete camera or NVR, set-channels, import
IPs, discovery import, manual reconcile) must reach whichever relay the frontend
actually uses. Startup picks the relay in main.py, but the routers used to call
path_sync.reconcile (MediaMTX) unconditionally — so under `relay=go2rtc` every
runtime change silently went to MediaMTX and never appeared in go2rtc until a
backend restart. Route all runtime reconciles through here instead.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import path_sync
from app.settings import get_settings


def _use_go2rtc() -> bool:
    return get_settings().relay == "go2rtc"


async def reconcile(session: AsyncSession, *, delete_orphans: bool = False):
    """Reconcile DB streams into the active relay. The report shape differs per
    relay (go2rtc → dict, MediaMTX → ReconcileReport); callers treat it as
    opaque / log via str()."""
    if _use_go2rtc():
        from app.services import go2rtc_sync

        return await go2rtc_sync.reconcile(session, delete_orphans=delete_orphans)
    return await path_sync.reconcile(session, delete_orphans=delete_orphans)


async def remove_paths_for_nvr(session: AsyncSession, nvr_id: str) -> None:
    """Drop an NVR's streams from the active relay (on NVR delete)."""
    if _use_go2rtc():
        from app.services import go2rtc_sync

        await go2rtc_sync.remove_streams_for_nvr(session, nvr_id)
        return
    await path_sync.remove_paths_for_nvr(session, nvr_id)
