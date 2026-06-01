"""Persist NVR audit events to the DB."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NvrEvent


async def log_event(
    session: AsyncSession,
    *,
    nvr_id: str,
    ip: str,
    event_type: str,
    message: str | None = None,
) -> None:
    session.add(NvrEvent(nvr_id=nvr_id, ip=ip, event_type=event_type, message=message))
    await session.commit()
