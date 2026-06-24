"""FastAPI dependencies: current user, role + access guards.

RBAC model:
  • admin    — bypasses all checks, sees everything, manages users.
  • operator — sees ONLY the cameras explicitly granted to them (per-camera
               grants in `user.cameras`). No grants → sees nothing. Regions
               remain for optional NVR-level grouping but are not the access
               mechanism.
"""

from __future__ import annotations

import uuid
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Camera, Nvr, Role, User
from app.security import decode_token

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _extract_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization.split(" ", 1)[1].strip()


# Endpoints a user flagged `must_change_password` may still reach — enough to
# read their profile, change the password, and log out, but nothing else.
_PASSWORD_CHANGE_EXEMPT = ("/auth/change-password", "/auth/logout", "/auth/me")


async def get_current_user(
    session: SessionDep,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    token = _extract_bearer(authorization)
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired") from None
    except jwt.PyJWTError:
        # Don't leak the PyJWT failure reason to the client.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from None

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token missing subject")
    try:
        user_id = uuid.UUID(sub)
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token subject is not a UUID") from None

    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")

    # A user who must change their password can't use the rest of the API until
    # they do — otherwise the flag is purely cosmetic (frontend-enforced only).
    if user.must_change_password and not request.url.path.endswith(_PASSWORD_CHANGE_EXEMPT):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Password change required")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != Role.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def _user_region_ids(user: User) -> set[uuid.UUID]:
    return {r.id for r in user.regions}


def _user_camera_ids(user: User) -> set[uuid.UUID]:
    return {c.id for c in user.cameras}


def user_can_access_nvr(user: User, nvr: Nvr) -> bool:
    if user.role == Role.admin:
        return True
    if nvr.region_id is None:
        return False  # unassigned NVRs are admin-only
    return nvr.region_id in _user_region_ids(user)


def user_can_access_camera(user: User, camera: Camera) -> bool:
    """Per-camera access: admins see all; operators see only granted cameras."""
    if user.role == Role.admin:
        return True
    return camera.id in _user_camera_ids(user)


async def authorize_camera(
    camera_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
) -> Camera:
    """Load a camera by id and 404 if the current user can't access it."""
    camera = (
        await session.execute(
            select(Camera).where(Camera.id == camera_id)
        )
    ).scalar_one_or_none()
    if camera is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    nvr = (await session.execute(select(Nvr).where(Nvr.id == camera.nvr_id))).scalar_one_or_none()
    if nvr is None or not nvr.enabled or not camera.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not available")
    if not user_can_access_camera(user, camera):
        # Return 404 (not 403) so we don't reveal that the camera exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    return camera
