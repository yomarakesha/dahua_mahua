"""User CRUD + region grants — admin-only."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.deps import AdminUser, SessionDep
from app.models import Region, Role, User
from app.schemas import UserCreate, UserRead, UserUpdate
from app.security import hash_password

router = APIRouter(prefix="/users", tags=["users"])


def _to_read(user: User) -> UserRead:
    return UserRead(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        region_ids=[r.id for r in user.regions],
    )


@router.get("", response_model=list[UserRead])
async def list_users(session: SessionDep, _: AdminUser) -> list[UserRead]:
    users = list(
        (
            await session.execute(
                select(User).options(selectinload(User.regions)).order_by(User.username)
            )
        ).scalars()
    )
    return [_to_read(u) for u in users]


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, session: SessionDep, _: AdminUser) -> UserRead:
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
        is_active=body.is_active,
        must_change_password=True,  # force change on first login
    )
    if body.region_ids:
        regions = list(
            (await session.execute(select(Region).where(Region.id.in_(body.region_ids)))).scalars()
        )
        if len(regions) != len(body.region_ids):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "One or more region_ids do not exist")
        user.regions = regions
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"User '{body.username}' already exists") from None
    await session.refresh(user, attribute_names=["regions"])
    return _to_read(user)


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    session: SessionDep,
    admin: AdminUser,
) -> UserRead:
    user = (
        await session.execute(
            select(User).where(User.id == user_id).options(selectinload(User.regions))
        )
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    data = body.model_dump(exclude_unset=True)

    # Guard 1: don't let an admin lock themselves out mid-session by demoting
    # or deactivating their own account.
    if user.id == admin.id:
        if "role" in data and data["role"] != user.role:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot change your own role")
        if data.get("is_active") is False:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate your own account")

    # Guard 2: never remove the last active admin (demote or deactivate).
    final_role = data.get("role", user.role)
    final_active = data.get("is_active", user.is_active)
    losing_admin = (
        user.role == Role.admin and user.is_active
        and (final_role != Role.admin or not final_active)
    )
    if losing_admin:
        other_admins = (
            await session.execute(
                select(func.count())
                .select_from(User)
                .where(User.id != user.id, User.role == Role.admin, User.is_active.is_(True))
            )
        ).scalar_one()
        if other_admins == 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Cannot demote or deactivate the last active admin",
            )

    if "new_password" in data:
        pw = data.pop("new_password")
        if pw is not None:
            user.password_hash = hash_password(pw)
            user.must_change_password = True
    if "region_ids" in data:
        ids = data.pop("region_ids") or []
        regions = list(
            (await session.execute(select(Region).where(Region.id.in_(ids)))).scalars()
        )
        if len(regions) != len(ids):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "One or more region_ids do not exist")
        user.regions = regions
    for field, value in data.items():
        setattr(user, field, value)
    await session.commit()
    await session.refresh(user, attribute_names=["regions"])
    return _to_read(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: uuid.UUID, session: SessionDep, admin: AdminUser) -> None:
    if user_id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot delete the user issuing the request")
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    await session.delete(user)
    await session.commit()
