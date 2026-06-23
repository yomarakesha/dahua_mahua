"""Reconcile DB cameras with MediaMTX path configs.

For every enabled `Camera` whose `Nvr` is enabled we want two MediaMTX paths:
  • `{nvr_id}_ch{N}`       — sub-stream (used by grid view, default).
  • `{nvr_id}_ch{N}_main`  — main-stream (only fired when fullscreen).

Both are `sourceOnDemand: yes` — MediaMTX opens the RTSP session to the NVR
only when a viewer asks for the path, and tears it down `closeAfter` later.
That is what keeps NVR connection counts bounded at "1 per active channel"
regardless of how many operators watch.

`reconcile()` is the load-bearing entry point; it's idempotent — safe to run
on every startup, after any inventory edit, or on a periodic timer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.crypto import decrypt_password
from app.models import Camera, Nvr, StreamQuality
from app.services.mediamtx_api import (
    MediaMTXClient,
    MediaMTXError,
    PathExists,
    PathNotFound,
    get_client,
)
from app.services.rtsp_probe import build_rtsp_url
from app.settings import get_settings

log = logging.getLogger("dss.path_sync")


@dataclass(slots=True)
class SyncReport:
    added: list[str]
    patched: list[str]
    deleted: list[str]
    errors: list[tuple[str, str]]  # (path_name, error_message)

    def summary(self) -> str:
        return (
            f"added={len(self.added)} patched={len(self.patched)} "
            f"deleted={len(self.deleted)} errors={len(self.errors)}"
        )


# Path naming kept in lockstep with `Camera.mediamtx_path()` and the names
# the original setup used, so MediaMTX clients see stable identifiers.
def path_name(
    nvr_id: str, channel: int, quality: StreamQuality, *, relay_variant: bool = False
) -> str:
    if quality != StreamQuality.main:
        return f"{nvr_id}_ch{channel}"
    # `_main` is the default (direct when the camera has an IP). `_main_nvr` is
    # the always-via-NVR variant, offered alongside `_main` only when a direct
    # path exists, so the operator can toggle source per camera in fullscreen.
    suffix = "_main_nvr" if relay_variant else "_main"
    return f"{nvr_id}_ch{channel}{suffix}"


def _build_path_config(
    nvr: Nvr, camera: Camera, quality: StreamQuality, *, force_relay: bool = False
) -> dict[str, Any]:
    """Build the MediaMTX path-config payload for one camera+quality.

    `force_relay=True` builds the via-NVR source even when the camera has a
    direct IP — used for the `_main_nvr` toggle variant."""
    settings = get_settings()
    password = decrypt_password(nvr.rtsp_password_encrypted)
    subtype = 0 if quality == StreamQuality.main else 1
    if quality == StreamQuality.main and camera.ip and not force_relay:
        # Main goes straight to the camera: the NVR's RTSP relay drops packets
        # on main streams even at trivial load (measured: 7815 lost vs 0 direct
        # — docs/audit-plan.md §9). A standalone IP camera serves its own
        # stream as channel 1; camera creds mirror the NVR's on this fleet.
        source = build_rtsp_url(
            ip=camera.ip,
            port=554,
            channel=1,
            vendor=nvr.vendor,
            subtype=subtype,
            username=nvr.rtsp_username,
            password=password,
        )
    else:
        source = build_rtsp_url(
            ip=nvr.ip,
            port=nvr.port,
            channel=camera.channel,
            vendor=nvr.vendor,
            subtype=subtype,
            username=nvr.rtsp_username,
            password=password,
        )
    if quality == StreamQuality.main:
        start_timeout = settings.main_start_timeout
        close_after = settings.main_close_after
    else:
        start_timeout = settings.sub_start_timeout
        close_after = settings.sub_close_after

    # Anti-freeze re-encode: when enabled for this quality, pull the source
    # through ffmpeg and re-emit H.264 with a forced short keyframe interval so
    # WebRTC/MSE cold starts and recoveries are sub-second instead of waiting up
    # to the camera's ~2s GOP. See Settings.reencode_* for the why + measured
    # numbers. Requires ffmpeg on the relay host; CPU scales with concurrently
    # viewed tiles (sourceOnDemand keeps idle paths off).
    if _reencode_for(settings, quality):
        return {
            "runOnDemand": _reencode_cmd(source, settings),
            "runOnDemandRestart": True,
            "runOnDemandStartTimeout": start_timeout,
            "runOnDemandCloseAfter": close_after,
        }

    return {
        "source": source,
        "sourceOnDemand": True,
        "sourceOnDemandStartTimeout": start_timeout,
        "sourceOnDemandCloseAfter": close_after,
        "rtspTransport": "tcp",
    }


def _reencode_for(settings: Any, quality: StreamQuality) -> bool:
    """True if re-encoding is enabled for this stream quality."""
    if not settings.reencode_enabled:
        return False
    want = (settings.reencode_qualities or "sub").lower()
    if want == "both":
        return True
    if quality == StreamQuality.main:
        return want == "main"
    return want == "sub"


def _reencode_cmd(source: str, settings: Any) -> str:
    """ffmpeg command MediaMTX runs on-demand: read `source` (RTSP/TCP) and
    republish a short-GOP H.264 copy to this same path. `$RTSP_PORT`/`$MTX_PATH`
    are substituted by MediaMTX at run time — keep them literal here."""
    kf = settings.reencode_keyframe_seconds
    preset = settings.reencode_preset or "veryfast"
    ffbin = settings.reencode_ffmpeg_bin or "ffmpeg"
    # `-force_key_frames expr:gte(t,n_forced*kf)` forces a keyframe every `kf`
    # seconds regardless of source GOP/framerate; `-bf 0` drops B-frames.
    # Encoder-specific low-latency flags: x264 -> -tune zerolatency; QSV/NVENC
    # have their own. NOTE: MediaMTX runs runOnDemand WITHOUT a shell — it splits
    # on spaces and substitutes $RTSP_PORT/$MTX_PATH itself, so no quotes and no
    # token may contain a space (the RTSP source URL has none).
    vcodec = settings.reencode_vcodec or "libx264"
    if vcodec == "libx264":
        enc = f"-c:v libx264 -preset {preset} -tune zerolatency"
    elif vcodec.endswith("_qsv"):
        enc = f"-c:v {vcodec} -async_depth 1"
    elif vcodec.endswith("_nvenc"):
        enc = f"-c:v {vcodec} -preset p1 -tune ll -delay 0"
    else:
        enc = f"-c:v {vcodec}"
    return (
        f"{ffbin} -nostdin -loglevel error -rtsp_transport tcp "
        f"-i {source} -an {enc} "
        f"-force_key_frames expr:gte(t,n_forced*{kf}) -bf 0 -pix_fmt yuv420p "
        "-f rtsp -rtsp_transport tcp rtsp://localhost:$RTSP_PORT/$MTX_PATH"
    )


def _config_diff(current: dict[str, Any], desired: dict[str, Any]) -> dict[str, Any]:
    """Return only the fields in `desired` that differ from `current` — that's
    what we PATCH back. Reduces noisy reloads when nothing real changed."""
    diff: dict[str, Any] = {}
    for k, v in desired.items():
        if current.get(k) != v:
            diff[k] = v
    return diff


async def _desired_paths(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """Walk the DB and produce {path_name: config} for every path we want
    MediaMTX to know about right now."""
    # populate_existing=True is load-bearing: the `cameras` relationship is
    # lazy="selectin", so a caller that loaded this Nvr earlier in the same
    # session (e.g. create_camera/update_nvr) already has a *stale* collection
    # cached. With expire_on_commit=False that cache survives the commit, and
    # a plain selectinload would NOT overwrite it — so a just-added camera
    # would be invisible here and never get a MediaMTX path. Forcing
    # populate_existing reloads the collection from the DB.
    nvrs = list(
        (
            await session.execute(
                select(Nvr)
                .where(Nvr.enabled.is_(True))
                .options(selectinload(Nvr.cameras))
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )

    desired: dict[str, dict[str, Any]] = {}
    for nvr in nvrs:
        for cam in nvr.cameras:
            if not cam.enabled:
                continue
            if cam.has_sub:
                name = path_name(nvr.id, cam.channel, StreamQuality.sub)
                desired[name] = _build_path_config(nvr, cam, StreamQuality.sub)
            if cam.has_main:
                name = path_name(nvr.id, cam.channel, StreamQuality.main)
                desired[name] = _build_path_config(nvr, cam, StreamQuality.main)
                # When a direct path exists, also publish the via-NVR variant so
                # the operator can switch source per camera. On-demand → costs
                # nothing on the NVR unless someone actually selects it.
                if cam.ip:
                    rname = path_name(
                        nvr.id, cam.channel, StreamQuality.main, relay_variant=True
                    )
                    desired[rname] = _build_path_config(
                        nvr, cam, StreamQuality.main, force_relay=True
                    )
    return desired


def _is_dss_managed(name: str) -> bool:
    """Heuristic: only touch paths that match our naming scheme. Anyone hand-
    editing a path called 'lobby' or 'test' won't lose it on the next reconcile."""
    # nvrXX_chN or nvrXX_chN_main — but we keep it loose so future NVR ids
    # like 'site-7-nvr01' still match.
    parts = name.rsplit("_ch", 1)
    if len(parts) != 2:
        return False
    rhs = parts[1]
    if rhs.endswith("_main_nvr"):
        rhs = rhs[:-9]
    elif rhs.endswith("_main"):
        rhs = rhs[:-5]
    return rhs.isdigit()


async def reconcile(
    session: AsyncSession,
    *,
    client: MediaMTXClient | None = None,
    delete_orphans: bool = True,
) -> SyncReport:
    """Add / patch / delete MediaMTX paths to match the DB.

    `delete_orphans=False` is useful for a "first run" against a MediaMTX
    that may already have paths from an older config — we want to converge,
    not yank live paths out from under operators.
    """
    import time
    client = client or get_client()
    t0 = time.perf_counter()
    desired = await _desired_paths(session)
    log.info("Reconcile start: desired=%d delete_orphans=%s", len(desired), delete_orphans)

    try:
        existing = await client.list_paths()
    except MediaMTXError as e:
        log.error("Reconcile aborted — failed to list paths from MediaMTX: %s", e)
        return SyncReport([], [], [], [("<list>", str(e))])

    log.info("Reconcile: existing=%d on MediaMTX", len(existing))
    report = SyncReport([], [], [], [])

    for name, cfg in desired.items():
        if name not in existing:
            try:
                await client.add_path(name, cfg)
                report.added.append(name)
            except PathExists:
                # Race against another reconciler — patch it instead.
                log.info("Reconcile: %s appeared mid-flight; patching instead of adding", name)
                try:
                    await client.patch_path(name, cfg)
                    report.patched.append(name)
                except MediaMTXError as e:
                    log.warning("Reconcile patch-after-race failed for %s: %s", name, e)
                    report.errors.append((name, str(e)))
            except MediaMTXError as e:
                log.warning("Reconcile add failed for %s: %s", name, e)
                report.errors.append((name, str(e)))
            continue

        diff = _config_diff(existing[name].get("conf", existing[name]), cfg)
        if diff:
            log.debug("Reconcile: %s drift detected fields=%s", name, list(diff.keys()))
            try:
                await client.patch_path(name, diff)
                report.patched.append(name)
            except PathNotFound:
                log.info("Reconcile: %s vanished mid-flight; re-adding", name)
                try:
                    await client.add_path(name, cfg)
                    report.added.append(name)
                except MediaMTXError as e:
                    log.warning("Reconcile re-add failed for %s: %s", name, e)
                    report.errors.append((name, str(e)))
            except MediaMTXError as e:
                log.warning("Reconcile patch failed for %s: %s", name, e)
                report.errors.append((name, str(e)))

    if delete_orphans:
        for name in existing.keys() - desired.keys():
            if not _is_dss_managed(name):
                log.debug("Reconcile: skipping orphan %s (not DSS-managed)", name)
                continue
            try:
                await client.delete_path(name)
                report.deleted.append(name)
            except PathNotFound:
                pass
            except MediaMTXError as e:
                log.warning("Reconcile delete failed for %s: %s", name, e)
                report.errors.append((name, str(e)))

    dt = (time.perf_counter() - t0) * 1000
    log.info("Reconcile complete in %.0fms: %s", dt, report.summary())
    return report


async def remove_paths_for_nvr(session: AsyncSession, nvr_id: str) -> None:
    """Best-effort cleanup when an NVR is deleted. We don't fail the delete
    if MediaMTX is unreachable — reconcile() on next startup will catch up."""
    client = get_client()
    try:
        existing = await client.list_paths()
    except MediaMTXError as e:
        log.warning("Cannot reach MediaMTX during NVR delete cleanup: %s", e)
        return
    prefix = f"{nvr_id}_ch"
    for name in list(existing.keys()):
        if name.startswith(prefix):
            try:
                await client.delete_path(name)
            except (PathNotFound, MediaMTXError):
                pass
