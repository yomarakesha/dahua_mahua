"""Camera list / read / update.

Operators see only cameras attached to NVRs in their allowed regions.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from sqlalchemy.exc import IntegrityError

from app.deps import AdminUser, CurrentUser, SessionDep, user_can_access_nvr
from app.models import Camera, Nvr, Role
from app.schemas import CameraCreate, CameraRead, CameraUpdate
from app.services import relay_sync

log = logging.getLogger("dss.cameras")

router = APIRouter(prefix="/cameras", tags=["cameras"])


async def _try_reconcile(session, delete_orphans: bool, ctx: str) -> None:
    """Run reconcile but never let a MediaMTX outage fail the DB write."""
    try:
        await relay_sync.reconcile(session, delete_orphans=delete_orphans)
    except Exception as e:
        log.warning("MediaMTX reconcile failed after %s: %s", ctx, e)


def _to_read(cam: Camera, nvr: Nvr) -> CameraRead:
    return CameraRead(
        id=cam.id,
        nvr_id=cam.nvr_id,
        channel=cam.channel,
        name=cam.name,
        ip=cam.ip,
        enabled=cam.enabled,
        has_sub=cam.has_sub,
        has_main=cam.has_main,
        display_name=cam.display_name,
        region_id=nvr.region_id,
    )


@router.get("", response_model=list[CameraRead])
async def list_cameras(
    session: SessionDep,
    user: CurrentUser,
    include_disabled: bool = Query(
        default=False,
        description="Admin-only: include disabled cameras and cameras of disabled NVRs (for CRUD UI).",
    ),
) -> list[CameraRead]:
    """Return all cameras visible to the caller. The frontend uses this to
    build the grid; pagination is overkill for ~342 cameras."""
    stmt = select(Camera, Nvr).join(Nvr, Nvr.id == Camera.nvr_id)
    if not (include_disabled and user.role == Role.admin):
        stmt = stmt.where(Nvr.enabled.is_(True)).where(Camera.enabled.is_(True))
    stmt = stmt.order_by(Camera.nvr_id, Camera.channel)
    rows = (await session.execute(stmt)).all()

    if user.role == Role.admin:
        return [_to_read(cam, nvr) for cam, nvr in rows]

    allowed = {r.id for r in user.regions}
    return [_to_read(cam, nvr) for cam, nvr in rows if nvr.region_id in allowed]


@router.post("", response_model=CameraRead, status_code=status.HTTP_201_CREATED)
async def create_camera(body: CameraCreate, session: SessionDep, _: AdminUser) -> CameraRead:
    nvr = (await session.execute(select(Nvr).where(Nvr.id == body.nvr_id))).scalar_one_or_none()
    if nvr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"NVR '{body.nvr_id}' not found")
    cam = Camera(
        nvr_id=body.nvr_id,
        channel=body.channel,
        name=body.name,
        enabled=body.enabled,
        has_sub=body.has_sub,
        has_main=body.has_main,
    )
    session.add(cam)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Channel {body.channel} already exists on NVR '{body.nvr_id}'",
        ) from None
    await session.refresh(cam)
    await _try_reconcile(session, delete_orphans=False, ctx=f"camera create {body.nvr_id} ch{body.channel}")
    return _to_read(cam, nvr)


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
    if "ip" in data:
        # Empty string from the form means "clear" — main falls back to NVR.
        data["ip"] = (data["ip"] or "").strip() or None
    for field, value in data.items():
        setattr(cam, field, value)
    await session.commit()
    nvr = (await session.execute(select(Nvr).where(Nvr.id == cam.nvr_id))).scalar_one()
    # Stream toggles change the set of MediaMTX paths we want; an IP change
    # flips the _main path's source between camera-direct and via-NVR.
    if {"enabled", "has_sub", "has_main", "ip"} & data.keys():
        await _try_reconcile(session, delete_orphans=True, ctx=f"camera {camera_id} update")
    return _to_read(cam, nvr)


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_camera(camera_id: uuid.UUID, session: SessionDep, _: AdminUser) -> None:
    cam = (
        await session.execute(select(Camera).where(Camera.id == camera_id))
    ).scalar_one_or_none()
    if cam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    await session.delete(cam)
    await session.commit()
    await _try_reconcile(session, delete_orphans=True, ctx=f"camera {camera_id} delete")
