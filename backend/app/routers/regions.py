"""Region CRUD — admin-only."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.deps import AdminUser, SessionDep
from app.models import Region
from app.schemas import RegionCreate, RegionRead

router = APIRouter(prefix="/regions", tags=["regions"])


@router.get("", response_model=list[RegionRead])
async def list_regions(session: SessionDep, _: AdminUser) -> list[Region]:
    return list((await session.execute(select(Region).order_by(Region.slug))).scalars())


@router.post("", response_model=RegionRead, status_code=status.HTTP_201_CREATED)
async def create_region(body: RegionCreate, session: SessionDep, _: AdminUser) -> Region:
    region = Region(slug=body.slug, name=body.name)
    session.add(region)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"Region '{body.slug}' already exists") from None
    await session.refresh(region)
    return region


@router.delete("/{region_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_region(region_id: uuid.UUID, session: SessionDep, _: AdminUser) -> None:
    region = (await session.execute(select(Region).where(Region.id == region_id))).scalar_one_or_none()
    if region is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Region not found")
    await session.delete(region)
    await session.commit()
