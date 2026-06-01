"""Admin-only MediaMTX management.

Most reconciliation happens automatically (startup + after inventory edits),
but having an explicit endpoint is useful when:
  • MediaMTX was restarted out-of-band and lost its in-memory paths;
  • an operator edited an NVR password and we want to push it without bouncing.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import AdminUser, SessionDep
from app.services import path_sync
from app.services.mediamtx_api import get_client

router = APIRouter(prefix="/mediamtx", tags=["mediamtx"])


@router.get("/health")
async def mediamtx_health(_: AdminUser) -> dict:
    return {"reachable": await get_client().ping()}


@router.post("/reconcile")
async def reconcile_paths(
    session: SessionDep,
    _: AdminUser,
    delete_orphans: bool = True,
) -> dict:
    report = await path_sync.reconcile(session, delete_orphans=delete_orphans)
    return {
        "added": report.added,
        "patched": report.patched,
        "deleted": report.deleted,
        "errors": [{"path": p, "error": e} for p, e in report.errors],
    }
