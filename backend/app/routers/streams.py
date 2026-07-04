"""Stream handout — resolves a camera to its relay stream name + audit row.

This endpoint performs:
  1. RBAC check (admin everywhere; operator only inside their regions).
  2. Logs a `StreamSession` row for audit / concurrency telemetry.
  3. Returns the go2rtc stream `path`. The response NEVER contains the NVR IP,
     RTSP user, or password — the operator's player connects to go2rtc by
     stream name; go2rtc talks to the NVR (once, fanned out).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from app.deps import CurrentUser, SessionDep, authorize_camera
from app.models import Camera, Nvr, StreamQuality, StreamSession
from app.schemas import StreamUrlResponse
from app.services import path_sync

log = logging.getLogger("dss.streams")

router = APIRouter(prefix="/streams", tags=["streams"])


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
