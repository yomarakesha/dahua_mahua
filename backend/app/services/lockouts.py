"""DB-backed NVR IP lockouts.

Mirror the NVR firmware ban (Dahua/Hikvision lock the source IP after N
failed RTSP auths). The persistent table survives backend restarts, so we
don't accidentally re-trigger the ban window.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Lockout


async def get_active_lockout(session: AsyncSession, ip: str) -> Lockout | None:
    lock = (await session.execute(select(Lockout).where(Lockout.ip == ip))).scalar_one_or_none()
    if lock is None:
        return None
    expires_at = lock.banned_at.timestamp() + lock.cooldown_seconds
    if time.time() >= expires_at:
        await session.execute(delete(Lockout).where(Lockout.ip == ip))
        await session.commit()
        return None
    return lock


async def record_lockout(session: AsyncSession, ip: str, cooldown_seconds: int = 1800) -> None:
    existing = (await session.execute(select(Lockout).where(Lockout.ip == ip))).scalar_one_or_none()
    if existing is None:
        session.add(Lockout(ip=ip, banned_at=datetime.now(timezone.utc), cooldown_seconds=cooldown_seconds))
    else:
        existing.banned_at = datetime.now(timezone.utc)
        existing.cooldown_seconds = cooldown_seconds
    await session.commit()


async def clear_lockout(session: AsyncSession, ip: str) -> bool:
    result = await session.execute(delete(Lockout).where(Lockout.ip == ip))
    await session.commit()
    return bool(result.rowcount)


def remaining_seconds(lock: Lockout) -> int:
    return max(0, int(lock.banned_at.timestamp() + lock.cooldown_seconds - time.time()))
