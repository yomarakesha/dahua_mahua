"""NVR CRUD + connection test + bulk health probe.

All write endpoints are admin-only. Read endpoints filter NVRs that an
operator can't see (regions outside their grants are hidden).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.crypto import decrypt_password, encrypt_password
from app.deps import AdminUser, CurrentUser, SessionDep, user_can_access_nvr
from app.models import Camera, Nvr, Role
from app.schemas import (
    NvrCreate,
    NvrHealthResult,
    NvrRead,
    NvrTestResult,
    NvrUpdate,
)
from app.services import lockouts, nvr_events, path_sync
from app.services.rtsp_probe import probe_rtsp, tcp_reachable

log = logging.getLogger("dss.nvrs")

router = APIRouter(prefix="/nvrs", tags=["nvrs"])


def _to_read(nvr: Nvr) -> NvrRead:
    return NvrRead(
        id=nvr.id,
        label=nvr.label,
        ip=nvr.ip,
        port=nvr.port,
        rtsp_username=nvr.rtsp_username,
        vendor=nvr.vendor,
        enabled=nvr.enabled,
        group=nvr.group,
        region_id=nvr.region_id,
        created_at=nvr.created_at,
        updated_at=nvr.updated_at,
        camera_count=len(nvr.cameras),
    )


def _visible_nvrs(nvrs: list[Nvr], user) -> list[Nvr]:
    if user.role == Role.admin:
        return nvrs
    allowed = {r.id for r in user.regions}
    return [n for n in nvrs if n.region_id in allowed]


@router.get("", response_model=list[NvrRead])
async def list_nvrs(session: SessionDep, user: CurrentUser) -> list[NvrRead]:
    nvrs = list(
        (
            await session.execute(
                select(Nvr).options(selectinload(Nvr.cameras)).order_by(Nvr.id)
            )
        ).scalars()
    )
    return [_to_read(n) for n in _visible_nvrs(nvrs, user)]


@router.get("/{nvr_id}", response_model=NvrRead)
async def get_nvr(nvr_id: str, session: SessionDep, user: CurrentUser) -> NvrRead:
    nvr = (
        await session.execute(
            select(Nvr).where(Nvr.id == nvr_id).options(selectinload(Nvr.cameras))
        )
    ).scalar_one_or_none()
    if nvr is None or not user_can_access_nvr(user, nvr):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "NVR not found")
    return _to_read(nvr)


@router.post("", response_model=NvrRead, status_code=status.HTTP_201_CREATED)
async def create_nvr(body: NvrCreate, session: SessionDep, _: AdminUser) -> NvrRead:
    nvr = Nvr(
        id=body.id,
        label=body.label,
        ip=body.ip,
        port=body.port,
        rtsp_username=body.rtsp_username,
        rtsp_password_encrypted=encrypt_password(body.rtsp_password),
        vendor=body.vendor,
        enabled=body.enabled,
        group=body.group,
        region_id=body.region_id,
    )
    session.add(nvr)
    # Create cameras 1..N
    for ch in range(1, body.channels + 1):
        session.add(Camera(nvr_id=body.id, channel=ch))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"NVR '{body.id}' already exists") from None
    nvr = (
        await session.execute(
            select(Nvr).where(Nvr.id == body.id).options(selectinload(Nvr.cameras))
        )
    ).scalar_one()
    log.info("NVR created id=%s label=%s channels=%d", nvr.id, nvr.label, body.channels)
    # Push paths to MediaMTX so the new cameras are immediately playable.
    # Best-effort: failures are logged inside reconcile() and don't roll back
    # the DB row — reconcile is idempotent and runs on startup anyway.
    await path_sync.reconcile(session, delete_orphans=False)
    return _to_read(nvr)


@router.patch("/{nvr_id}", response_model=NvrRead)
async def update_nvr(
    nvr_id: str,
    body: NvrUpdate,
    session: SessionDep,
    _: AdminUser,
) -> NvrRead:
    nvr = (
        await session.execute(
            select(Nvr).where(Nvr.id == nvr_id).options(selectinload(Nvr.cameras))
        )
    ).scalar_one_or_none()
    if nvr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "NVR not found")
    data = body.model_dump(exclude_unset=True)
    if "rtsp_password" in data:
        nvr.rtsp_password_encrypted = encrypt_password(data.pop("rtsp_password"))
    for field, value in data.items():
        setattr(nvr, field, value)
    nvr.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(nvr, attribute_names=["cameras"])
    log.info("NVR updated id=%s fields=%s", nvr_id, list(data.keys()))
    # Re-push: fields like ip / port / rtsp creds / enabled change the
    # MediaMTX source URL or the desired set of paths.
    await path_sync.reconcile(session, delete_orphans=True)
    return _to_read(nvr)


@router.delete("/{nvr_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_nvr(nvr_id: str, session: SessionDep, _: AdminUser) -> None:
    nvr = (await session.execute(select(Nvr).where(Nvr.id == nvr_id))).scalar_one_or_none()
    if nvr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "NVR not found")
    await session.delete(nvr)
    await session.commit()
    log.info("NVR deleted id=%s", nvr_id)
    await path_sync.remove_paths_for_nvr(session, nvr_id)


@router.post("/{nvr_id}/test", response_model=NvrTestResult)
async def test_nvr(
    nvr_id: str,
    session: SessionDep,
    request: Request,
    user: AdminUser,
) -> NvrTestResult:
    """Send an RTSP OPTIONS + digest auth probe to verify credentials."""
    nvr = (await session.execute(select(Nvr).where(Nvr.id == nvr_id))).scalar_one_or_none()
    if nvr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "NVR not found")

    lock = await lockouts.get_active_lockout(session, nvr.ip)
    if lock is not None:
        remaining = lockouts.remaining_seconds(lock)
        return NvrTestResult(
            ok=False,
            message=f"Locked out — retry in {remaining // 60}m {remaining % 60}s",
            banned_until=lock.banned_at.timestamp() + lock.cooldown_seconds,
            remaining=remaining,
        )

    password = decrypt_password(nvr.rtsp_password_encrypted)
    # probe_rtsp is sync (raw socket); run in executor to avoid blocking the loop.
    result = await asyncio.to_thread(
        probe_rtsp,
        nvr.ip,
        nvr.port,
        nvr.rtsp_username,
        password,
        channel=1,
        vendor=nvr.vendor,
        tag=f"[{nvr.id}]",
    )

    client_ip = request.client.host if request.client else "?"
    log.info(
        "NVR test nvr_id=%s by=%s@%s ok=%s msg=%s",
        nvr.id, user.username, client_ip, result.ok, result.message,
    )

    if result.banned:
        await lockouts.record_lockout(session, nvr.ip, cooldown_seconds=result.banned_cooldown)
        await nvr_events.log_event(session, nvr_id=nvr.id, ip=nvr.ip,
                                   event_type="banned", message=result.message)
    elif result.ok:
        await lockouts.clear_lockout(session, nvr.ip)
        await nvr_events.log_event(session, nvr_id=nvr.id, ip=nvr.ip,
                                   event_type="auth_ok", message=result.message)
    else:
        await nvr_events.log_event(session, nvr_id=nvr.id, ip=nvr.ip,
                                   event_type="auth_fail", message=result.message)

    out = NvrTestResult(ok=result.ok, message=result.message)
    if result.banned:
        from time import time as _t
        out.banned_until = _t() + result.banned_cooldown
        out.remaining = result.banned_cooldown
    return out


@router.post("/health", response_model=list[NvrHealthResult])
async def health_all(session: SessionDep, user: CurrentUser) -> list[NvrHealthResult]:
    """TCP-only reachability probe for every NVR the user can see. Cheap (no auth)."""
    nvrs = list((await session.execute(select(Nvr))).scalars())
    nvrs = _visible_nvrs(nvrs, user)

    async def _probe(n: Nvr) -> NvrHealthResult:
        if not n.enabled:
            return NvrHealthResult(nvr_id=n.id, ok=False, message="Disabled")
        ok, msg = await asyncio.to_thread(tcp_reachable, n.ip, n.port)
        return NvrHealthResult(nvr_id=n.id, ok=ok, message=msg)

    return await asyncio.gather(*[_probe(n) for n in nvrs])
