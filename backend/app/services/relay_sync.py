"""Reconcile dispatch → go2rtc.

Runtime inventory edits (add/edit/delete camera or NVR, set-channels, import
IPs, discovery import, manual reconcile) must reach the relay the frontend
actually uses. go2rtc is the sole relay; this thin module keeps a stable call
surface for the routers so a future relay swap only touches one file.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import go2rtc_sync


async def reconcile(session: AsyncSession, *, delete_orphans: bool = False) -> dict:
    """Reconcile DB streams into go2rtc. Returns go2rtc's report dict; callers
    treat it as opaque / log via str()."""
    return await go2rtc_sync.reconcile(session, delete_orphans=delete_orphans)


async def remove_paths_for_nvr(session: AsyncSession, nvr_id: str) -> None:
    """Drop an NVR's streams from go2rtc (on NVR delete)."""
    await go2rtc_sync.remove_streams_for_nvr(session, nvr_id)
