"""Stream URL handout — what the player actually fetches.

This endpoint is the *only* way for an operator to learn how to play a camera.
It performs:
  1. RBAC check (admin everywhere; operator only inside their regions).
  2. Logs a `StreamSession` row for audit / concurrency telemetry.
  3. Returns MediaMTX-side URLs (WebRTC + HLS). Crucially the response NEVER
     contains the NVR IP, RTSP user, or password. The operator only ever
     talks to MediaMTX; MediaMTX talks to the NVR (once, fanned out).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from app.deps import CurrentUser, SessionDep, authorize_camera
from app.models import Camera, Nvr, Role, StreamQuality, StreamSession
from app.schemas import StreamUrlResponse
from app.services import path_sync
from app.settings import get_settings

log = logging.getLogger("dss.streams")

router = APIRouter(prefix="/streams", tags=["streams"])


def _webrtc_whep_url(base: str, path: str) -> str:
    """MediaMTX exposes WHEP at `<webrtc_base>/<path>/whep`."""
    return f"{base.rstrip('/')}/{path}/whep"


def _hls_url(base: str, path: str) -> str:
    """MediaMTX low-latency HLS playlist at `<hls_base>/<path>/index.m3u8`."""
    return f"{base.rstrip('/')}/{path}/index.m3u8"


def _proxied_rtsp_url(base: str, path: str) -> str:
    """Pull from MediaMTX (no NVR creds), e.g. for admin debug / VLC."""
    return f"{base.rstrip('/')}/{path}"


@router.get("/{camera_id}", response_model=StreamUrlResponse)
async def get_stream_urls(
    camera_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
    request: Request,
    quality: StreamQuality = Query(default=StreamQuality.sub),
) -> StreamUrlResponse:
    """Return playback URLs for one camera at the requested quality.

    The frontend calls this twice per camera in normal use:
      • once with `quality=sub` when the camera enters the grid;
      • once with `quality=main` when the operator goes fullscreen.
    """
    camera = await authorize_camera(camera_id, session, user)

    if quality == StreamQuality.sub and not camera.has_sub:
        raise HTTPException(status.HTTP_409_CONFLICT, "Camera has no sub-stream")
    if quality == StreamQuality.main and not camera.has_main:
        raise HTTPException(status.HTTP_409_CONFLICT, "Camera has no main-stream")

    settings = get_settings()
    path = path_sync.path_name(camera.nvr_id, camera.channel, quality)

    session.add(
        StreamSession(
            user_id=user.id,
            camera_id=camera.id,
            quality=quality,
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    )
    await session.commit()

    return StreamUrlResponse(
        camera_id=camera.id,
        quality=quality,
        path=path,
        webrtc_whep_url=_webrtc_whep_url(settings.mediamtx_webrtc_url, path),
        hls_url=_hls_url(settings.mediamtx_hls_url, path),
        # Only admins get the (still creds-free) RTSP URL — operators don't
        # need it and shouldn't be able to share it from the browser.
        rtsp_url=(
            _proxied_rtsp_url(settings.mediamtx_rtsp_url, path)
            if user.role == Role.admin
            else None
        ),
    )


@router.post("/{camera_id}/end", status_code=status.HTTP_204_NO_CONTENT)
async def end_stream_session(
    camera_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
) -> None:
    """Stamp the most recent open `StreamSession` for this user+camera as
    ended. Best-effort — the player calling this on tab-close lets us
    measure real concurrency; missing the call only loses telemetry."""
    from datetime import datetime, timezone
    from sqlalchemy import update

    stmt = (
        select(StreamSession.id)
        .where(StreamSession.user_id == user.id)
        .where(StreamSession.camera_id == camera_id)
        .where(StreamSession.ended_at.is_(None))
        .order_by(StreamSession.started_at.desc())
        .limit(1)
    )
    sid = (await session.execute(stmt)).scalar_one_or_none()
    if sid is not None:
        await session.execute(
            update(StreamSession)
            .where(StreamSession.id == sid)
            .values(ended_at=datetime.now(timezone.utc))
        )
        await session.commit()
