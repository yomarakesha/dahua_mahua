"""Persist NVR audit events to the DB.

Each call opens its own short-lived session and commits independently. Audit
events must survive regardless of whether the caller's request transaction
later commits or rolls back (e.g. we log a "banned" event and then raise an
HTTPException) — so they deliberately do NOT share the request session.
"""

from __future__ import annotations

from app.db import SessionLocal
from app.models import NvrEvent


async def log_event(
    *,
    nvr_id: str,
    ip: str,
    event_type: str,
    message: str | None = None,
) -> None:
    async with SessionLocal() as session:
        session.add(NvrEvent(nvr_id=nvr_id, ip=ip, event_type=event_type, message=message))
        await session.commit()
