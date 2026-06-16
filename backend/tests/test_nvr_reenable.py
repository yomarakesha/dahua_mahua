"""When an NVR is turned back on, its watchdog-disabled channels must come
back too — otherwise a re-enabled registrar returns half-dark and the operator
ends up re-adding cameras by hand. `reenable_cameras_for_nvr` is the shared
helper used both by the PATCH-enable path and the startup recovery sweep.
"""
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.crypto import encrypt_password
from app.db import Base
from app.models import Camera, Nvr, Vendor
from app.services.source_watch import reenable_cameras_for_nvr


@pytest_asyncio.fixture
async def session():
    # One shared in-memory SQLite connection for the whole test (StaticPool),
    # so the schema and rows survive across session operations.
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _nvr(enabled: bool = True) -> Nvr:
    return Nvr(
        id="nvr01", label="t", ip="192.168.20.58",
        rtsp_password_encrypted=encrypt_password("pw"),
        vendor=Vendor.dahua, enabled=enabled,
    )


async def test_reenables_disabled_cameras(session):
    session.add(_nvr())
    session.add(Camera(nvr_id="nvr01", channel=1, enabled=False))
    session.add(Camera(nvr_id="nvr01", channel=2, enabled=True))
    await session.commit()

    n = await reenable_cameras_for_nvr(session, "nvr01")
    await session.commit()

    assert n == 1  # only the disabled one was flipped
    states = (
        await session.execute(select(Camera.enabled).where(Camera.nvr_id == "nvr01"))
    ).scalars().all()
    assert all(states)


async def test_leaves_other_nvrs_untouched(session):
    session.add(_nvr())
    other = Nvr(
        id="nvr02", label="o", ip="192.168.20.34",
        rtsp_password_encrypted=encrypt_password("pw"),
        vendor=Vendor.dahua, enabled=True,
    )
    session.add(other)
    session.add(Camera(nvr_id="nvr01", channel=1, enabled=False))
    session.add(Camera(nvr_id="nvr02", channel=1, enabled=False))
    await session.commit()

    n = await reenable_cameras_for_nvr(session, "nvr01")
    await session.commit()

    assert n == 1
    other_cam = (
        await session.execute(select(Camera.enabled).where(Camera.nvr_id == "nvr02"))
    ).scalar_one()
    assert other_cam is False  # untouched


async def test_no_disabled_cameras_returns_zero(session):
    session.add(_nvr())
    session.add(Camera(nvr_id="nvr01", channel=1, enabled=True))
    await session.commit()

    n = await reenable_cameras_for_nvr(session, "nvr01")
    assert n == 0
