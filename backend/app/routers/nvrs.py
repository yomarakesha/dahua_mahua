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
from app.models import Camera, Nvr, Role, Vendor
from app.schemas import (
    NvrCreate,
    NvrHealthResult,
    NvrRead,
    NvrTestResult,
    NvrUpdate,
)
from app.services import lockouts, nvr_events, path_sync
from app.services.discovery import detect_dahua_channels
from app.services.rtsp_probe import probe_rtsp, tcp_reachable

log = logging.getLogger("dss.nvrs")

router = APIRouter(prefix="/nvrs", tags=["nvrs"])


def _to_read(nvr: Nvr, *, create_notice: str | None = None) -> NvrRead:
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
        create_notice=create_notice,
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


def _derive_nvr_id(ip: str) -> str:
    """Build a stable slug from the IP: '192.168.20.34' -> 'nvr-192-168-20-34'."""
    return "nvr-" + ip.replace(".", "-")


_DEFAULT_FALLBACK_CHANNELS = 1


@router.post("", response_model=NvrRead, status_code=status.HTTP_201_CREATED)
async def create_nvr(body: NvrCreate, session: SessionDep, _: AdminUser) -> NvrRead:
    """Create an NVR with safeguards against the classic footgun:
    "wrong password gets written to DB → MediaMTX hammers RTSP with bad
    creds → NVR firmware IP-bans us for 30 minutes". We validate before
    we write, and refuse the write if creds are bad or IP is in cooldown.

    Channel count is auto-detected via Dahua's magicBox CGI when the caller
    doesn't specify it — same UX as DSS Pro's discovery flow.
    """
    nvr_id = body.id or _derive_nvr_id(body.ip)

    # ── Guard 1: don't add NVRs whose IP is currently locked out — any
    #    further auth attempt would just extend the ban.
    lock = await lockouts.get_active_lockout(session, body.ip)
    if lock is not None:
        remaining = lockouts.remaining_seconds(lock)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"NVR refused: IP {body.ip} is in lockout for "
            f"{remaining // 60}m {remaining % 60}s. Wait it out before retrying.",
        )

    # ── Guard 2: validate RTSP credentials with a single auth attempt
    #    before committing anything. Wrong password → reject; banned → record
    #    + reject; unreachable → save but disable.
    notice: str | None = None
    enable_after_create = body.enabled
    if body.skip_probe:
        log.info("NVR %s create: probe skipped by caller", nvr_id)
    else:
        result = await asyncio.to_thread(
            probe_rtsp,
            body.ip,
            body.port,
            body.rtsp_username,
            body.rtsp_password,
            channel=1,
            vendor=body.vendor,
            tag=f"[create:{nvr_id}]",
        )
        if result.banned:
            await lockouts.record_lockout(
                session, body.ip, cooldown_seconds=result.banned_cooldown,
            )
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"NVR refused: {result.message}. Cooldown started "
                f"({result.banned_cooldown // 60}m).",
            )
        if not result.ok and "Authentication failed" in result.message:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"NVR refused: {result.message}. Check the password / username "
                "and try again. Nothing was saved.",
            )
        if not result.ok:
            # Timeout / network error / unexpected status. The creds *might*
            # be right but we can't tell. Save the config but keep it disabled
            # so MediaMTX won't repeatedly retry on a flaky host.
            enable_after_create = False
            notice = (
                f"NVR unreachable during validation ({result.message}). "
                "Saved as DISABLED — enable it from the row once the NVR is online."
            )
            log.info("NVR %s create: %s", nvr_id, notice)
        else:
            log.info("NVR %s create: credentials OK", nvr_id)

    # ── Channel auto-detect — only when caller didn't pin a value.
    channels = body.channels
    if channels is None:
        if body.vendor == Vendor.dahua and not body.skip_probe:
            try:
                detected = await detect_dahua_channels(
                    body.ip, body.rtsp_username, body.rtsp_password,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("Channel autodetect for %s errored: %s", nvr_id, e)
                detected = None
        else:
            detected = None
        channels = detected or _DEFAULT_FALLBACK_CHANNELS
        log.info(
            "NVR %s channels: auto=%s, using=%d", nvr_id, detected, channels,
        )
        if notice is None and detected is None:
            notice = f"Channel count couldn't be detected; created with {channels} channel."
        elif detected is not None:
            notice = (notice + " " if notice else "") + f"Detected {detected} channels."

    nvr = Nvr(
        id=nvr_id,
        label=body.label,
        ip=body.ip,
        port=body.port,
        rtsp_username=body.rtsp_username,
        rtsp_password_encrypted=encrypt_password(body.rtsp_password),
        vendor=body.vendor,
        enabled=enable_after_create,
        group=body.group,
        region_id=body.region_id,
    )
    session.add(nvr)
    for ch in range(1, channels + 1):
        session.add(Camera(nvr_id=nvr_id, channel=ch))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"NVR '{nvr_id}' already exists") from None
    nvr = (
        await session.execute(
            select(Nvr).where(Nvr.id == nvr_id).options(selectinload(Nvr.cameras))
        )
    ).scalar_one()
    log.info("NVR created id=%s label=%s channels=%d enabled=%s",
             nvr.id, nvr.label, channels, nvr.enabled)
    # Push paths to MediaMTX so the new cameras are immediately playable.
    # Best-effort: if MediaMTX is unreachable we still return 201 so the DB
    # row stays. Reconcile is idempotent and the operator can retry via
    # POST /mediamtx/reconcile once MediaMTX is back.
    try:
        await path_sync.reconcile(session, delete_orphans=False)
    except Exception as e:
        log.warning("NVR %s created in DB but MediaMTX reconcile failed: %s", nvr.id, e)
    return _to_read(nvr, create_notice=notice)


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

    # Compute the post-update target so all guards reason about the same state.
    final_enabled = bool(data.get("enabled", nvr.enabled))
    final_ip = data.get("ip", nvr.ip)
    final_port = data.get("port", nvr.port)
    final_user = data.get("rtsp_username", nvr.rtsp_username)
    cred_fields = {"ip", "port", "rtsp_username", "rtsp_password"}
    cred_changed = bool(cred_fields & data.keys())

    # Guard 1: re-enabling an NVR whose IP is in lockout would resume the
    # 401-loop and dig the hole deeper. Refuse with the remaining cooldown.
    if final_enabled and not nvr.enabled:
        lock = await lockouts.get_active_lockout(session, final_ip)
        if lock is not None:
            remaining = lockouts.remaining_seconds(lock)
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Cannot enable: IP {final_ip} is locked out for another "
                f"{remaining // 60}m {remaining % 60}s.",
            )

    # Guard 2: if the caller is changing RTSP creds (ip/port/user/pass) AND
    # the NVR will remain (or become) enabled, validate the new creds first.
    # Saving a typo'd password to an enabled NVR is the exact path that gets
    # MediaMTX to hammer the NVR with 401s and trigger a firmware-side ban.
    if final_enabled and cred_changed:
        final_pw = (
            data["rtsp_password"]
            if "rtsp_password" in data
            else decrypt_password(nvr.rtsp_password_encrypted)
        )
        result = await asyncio.to_thread(
            probe_rtsp,
            final_ip, final_port, final_user, final_pw,
            channel=1, vendor=nvr.vendor, tag=f"[update:{nvr_id}]",
        )
        if result.banned:
            await lockouts.record_lockout(
                session, final_ip, cooldown_seconds=result.banned_cooldown,
            )
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Update refused: {result.message}. Cooldown started.",
            )
        if not result.ok and "Authentication failed" in result.message:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Update refused: {result.message}. NVR row not changed.",
            )
        # Unreachable / non-auth failure: trust the operator and apply, but
        # they'll see paths fail in MediaMTX until the NVR comes back online.

    if "rtsp_password" in data:
        nvr.rtsp_password_encrypted = encrypt_password(data.pop("rtsp_password"))
    for field, value in data.items():
        setattr(nvr, field, value)
    nvr.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(nvr, attribute_names=["cameras"])
    log.info("NVR updated id=%s fields=%s", nvr_id, list(data.keys()))
    # Re-push: fields like ip / port / rtsp creds / enabled change the
    # MediaMTX source URL or the desired set of paths. Non-fatal: a 200
    # still goes back if MediaMTX is unreachable.
    try:
        await path_sync.reconcile(session, delete_orphans=True)
    except Exception as e:
        log.warning("NVR %s saved in DB but MediaMTX reconcile failed: %s", nvr_id, e)
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
