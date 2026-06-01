"""Camera list / read / update.

Operators see only cameras attached to NVRs in their allowed regions.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.deps import AdminUser, CurrentUser, SessionDep, user_can_access_nvr
from app.models import Camera, Nvr, Role
from app.schemas import CameraRead, CameraUpdate
from app.services import path_sync

router = APIRouter(prefix="/cameras", tags=["cameras"])


def _to_read(cam: Camera, nvr: Nvr) -> CameraRead:
    return CameraRead(
        id=cam.id,
        nvr_id=cam.nvr_id,
        channel=cam.channel,
        name=cam.name,
        enabled=cam.enabled,
        has_sub=cam.has_sub,
        has_main=cam.has_main,
        display_name=cam.display_name,
        region_id=nvr.region_id,
    )


@router.get("", response_model=list[CameraRead])
async def list_cameras(session: SessionDep, user: CurrentUser) -> list[CameraRead]:
    """Return all cameras visible to the caller. The frontend uses this to
    build the grid; pagination is overkill for ~342 cameras."""
    rows = (
        await session.execute(
            select(Camera, Nvr)
            .join(Nvr, Nvr.id == Camera.nvr_id)
            .where(Nvr.enabled.is_(True))
            .where(Camera.enabled.is_(True))
            .order_by(Camera.nvr_id, Camera.channel)
        )
    ).all()

    if user.role == Role.admin:
        return [_to_read(cam, nvr) for cam, nvr in rows]

    allowed = {r.id for r in user.regions}
    return [_to_read(cam, nvr) for cam, nvr in rows if nvr.region_id in allowed]


@router.patch("/{camera_id}", response_model=CameraRead)
async def update_camera(
    camera_id: uuid.UUID,
    body: CameraUpdate,
    session: SessionDep,
    _: AdminUser,
) -> CameraRead:
    cam = (
        await session.execute(
            select(Camera).where(Camera.id == camera_id)
        )
    ).scalar_one_or_none()
    if cam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(cam, field, value)
    await session.commit()
    nvr = (await session.execute(select(Nvr).where(Nvr.id == cam.nvr_id))).scalar_one()
    # Stream toggles change the set of MediaMTX paths we want — push diffs.
    if {"enabled", "has_sub", "has_main"} & data.keys():
        await path_sync.reconcile(session, delete_orphans=True)
    return _to_read(cam, nvr)
