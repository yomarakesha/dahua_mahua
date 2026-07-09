"""Desired-stream builder: DB cameras → the relay's desired stream set.

For every enabled `Camera` whose `Nvr` is enabled we want two streams:
  • `{nvr_id}_ch{N}`       — sub-stream (used by grid view, default).
  • `{nvr_id}_ch{N}_main`  — main-stream (only fired when fullscreen).

Both are on-demand — the relay opens the RTSP session to the source only when a
viewer asks for the stream and tears it down shortly after. That is what keeps
NVR connection counts bounded at "1 per active channel" regardless of how many
operators watch.

`_desired_paths()` is the single source of truth for which streams exist and
their RTSP sources; `go2rtc_sync` consumes it to reconcile go2rtc.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.crypto import decrypt_password
from app.models import Camera, Nvr, StreamQuality
from app.services.rtsp_probe import build_rtsp_url
from app.settings import get_settings

log = logging.getLogger("dss.path_sync")


# Stream naming kept in lockstep with the names the original setup used, so
# relay clients see stable identifiers.
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
    """Build the stream-config payload for one camera+quality.

    `force_relay=True` builds the via-NVR source even when the camera has a
    direct IP — used for the `_main_nvr` toggle variant."""
    settings = get_settings()
    password = decrypt_password(nvr.rtsp_password_encrypted)
    subtype = 0 if quality == StreamQuality.main else 1
    if camera.ip and not force_relay:
        # Pull straight from the camera (sub AND main), not the NVR's RTSP relay.
        # One NVR can't re-stream many channels at once: measured read i/o-timeouts
        # → producer reconnects → grid freezes once ~16+ concurrent pulls hit a
        # single NVR. A standalone IP camera serves its own stream as channel 1 and
        # only ever handles its own 1-2 streams, so load spreads across the fleet.
        # Camera creds mirror the NVR's on this fleet. `_main_nvr` (force_relay)
        # still offers a via-NVR fallback for the main toggle.
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

    return {
        "source": source,
        "sourceOnDemand": True,
        "sourceOnDemandStartTimeout": start_timeout,
        "sourceOnDemandCloseAfter": close_after,
        "rtspTransport": "tcp",
    }


async def _desired_paths(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """Walk the DB and produce {stream_name: config} for every stream we want
    the relay to know about right now."""
    # populate_existing=True is load-bearing: the `cameras` relationship is
    # lazy="selectin", so a caller that loaded this Nvr earlier in the same
    # session (e.g. create_camera/update_nvr) already has a *stale* collection
    # cached. With expire_on_commit=False that cache survives the commit, and
    # a plain selectinload would NOT overwrite it — so a just-added camera
    # would be invisible here and never get a stream. Forcing
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
    """Heuristic: only touch streams that match our naming scheme. Anyone hand-
    editing a stream called 'lobby' or 'test' won't lose it on the next reconcile."""
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
