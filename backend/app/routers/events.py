"""NVR event log read API — admin-only."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.deps import AdminUser, SessionDep
from app.models import NvrEvent
from app.schemas import NvrEventRead

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[NvrEventRead])
async def list_events(
    session: SessionDep,
    _: AdminUser,
    nvr_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[NvrEvent]:
    stmt = select(NvrEvent).order_by(NvrEvent.created_at.desc()).limit(limit)
    if nvr_id:
        stmt = stmt.where(NvrEvent.nvr_id == nvr_id)
    return list((await session.execute(stmt)).scalars())
