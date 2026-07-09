"""Live-view helpers — currently the warm-stream-pool hint endpoint.

POST /live/warm  {camera_ids: [uuid, ...]}
    The frontend posts the cameras currently on-screen (in priority order) so the
    backend can keep their go2rtc SUB producers warm — a warm producer opens in
    ~0.5s vs 2.6–5s cold. The pool is BOUNDED and NvrBudget-aware (global +
    per-NVR caps) so it can never exhaust an NVR's concurrent-pull limit.

    When ``warm_pool_enabled`` is False the endpoint is a 202 NO-OP (nothing is
    recorded, nothing is warmed) so the frontend may call it unconditionally.

Only cameras the caller can access (per-camera RBAC) and that actually have a
SUB stream are forwarded to the pool.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.deps import CurrentUser, SessionDep, user_can_access_camera
from app.models import Camera, Nvr
from app.services.warm_pool import get_warm_pool
from app.settings import get_settings

log = logging.getLogger("dss.live")

router = APIRouter(prefix="/live", tags=["live"])

# Guard against an unbounded body — the caps clamp what's actually warmed, but we
# also refuse to even parse a pathologically large id list.
_MAX_CAMERA_IDS = 256


class WarmRequest(BaseModel):
    camera_ids: list[uuid.UUID] = Field(default_factory=list, max_length=_MAX_CAMERA_IDS)


@router.post("/warm", status_code=status.HTTP_202_ACCEPTED)
async def warm_cameras(
    body: WarmRequest,
    session: SessionDep,
    user: CurrentUser,
) -> dict:
    """Ask the warm pool to keep the given cameras' SUB streams hot.

    Returns 202 with ``{"warming": n, "capped": m}``:
      * *warming* — streams now targeted (after global + per-NVR caps),
      * *capped*  — streams dropped because a cap was full.

    Disabled → immediate 202 ``{"warming": 0, "capped": 0}`` (no-op).
    """
    settings = get_settings()
    if not settings.warm_pool_enabled:
        # Config-gated OFF: record nothing, start nothing.
        return {"warming": 0, "capped": 0}

    pool = get_warm_pool()

    if not body.camera_ids:
        # Empty selection clears the desired set (e.g. the grid emptied).
        return await pool.set_desired([])

    rows = (
        await session.execute(
            select(Camera).where(Camera.id.in_(body.camera_ids))
        )
    ).scalars().all()
    by_id = {c.id: c for c in rows}

    # Which NVRs are enabled — a disabled NVR has no live go2rtc stream to warm.
    nvr_ids = {c.nvr_id for c in rows}
    enabled_nvrs: set[str] = set()
    if nvr_ids:
        enabled_nvrs = set(
            (
                await session.execute(
                    select(Nvr.id).where(
                        Nvr.id.in_(nvr_ids), Nvr.enabled.is_(True)
                    )
                )
            ).scalars().all()
        )

    # Preserve the request order — it conveys priority to the pool's cap logic.
    keys: list[tuple[str, int]] = []
    for cid in body.camera_ids:
        cam = by_id.get(cid)
        if cam is None:
            continue
        if not cam.enabled or not cam.has_sub:
            continue
        if cam.nvr_id not in enabled_nvrs:
            continue
        if not user_can_access_camera(user, cam):
            continue
        keys.append((cam.nvr_id, cam.channel))

    result = await pool.set_desired(keys)
    log.debug(
        "warm request user=%s requested=%d warming=%d capped=%d",
        user.id, len(body.camera_ids), result["warming"], result["capped"],
    )
    return result
