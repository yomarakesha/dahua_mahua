"""Reachability-aware IP import.

On a PoE site the NVR reports each camera's Address, but those IPs sit behind
the NVR's internal switch and are NOT directly reachable. Storing them anyway
points the _main path at a dead host, which the watchdog then disables. So the
import must probe each candidate IP and only keep it for direct pull when it
actually answers on RTSP; otherwise the channel falls back to the NVR relay
(Camera.ip = None).
"""
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.crypto import encrypt_password
from app.db import Base
from app.models import Camera, Nvr, Vendor
from app.services import camera_import


@pytest_asyncio.fixture
async def session():
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


def _nvr() -> Nvr:
    return Nvr(
        id="nvr01", label="t", ip="192.168.20.15",
        rtsp_password_encrypted=encrypt_password("pw"),
        vendor=Vendor.dahua, enabled=True,
    )


async def test_unreachable_ip_falls_back_to_relay(session, monkeypatch):
    session.add(_nvr())
    session.add(Camera(nvr_id="nvr01", channel=1, enabled=True))  # reachable cam
    session.add(Camera(nvr_id="nvr01", channel=2, enabled=True))  # PoE-hidden cam
    await session.commit()

    async def fake_fetch(*a, **k):
        return {1: "192.168.20.50", 2: "192.168.20.101"}

    async def fake_probe(ip, port=554, timeout=1.5):
        return ip == "192.168.20.50"  # only the first answers on RTSP

    monkeypatch.setattr(camera_import, "fetch_camera_ips", fake_fetch)
    monkeypatch.setattr(camera_import, "_probe_rtsp", fake_probe)

    nvr = (await session.execute(select(Nvr))).scalar_one()
    await camera_import.apply_camera_ips(session, nvr)

    by_ch = {
        c.channel: c.ip
        for c in (await session.execute(select(Camera))).scalars()
    }
    assert by_ch[1] == "192.168.20.50"  # reachable -> direct
    assert by_ch[2] is None             # unreachable -> NVR relay


async def test_stale_unreachable_ip_is_cleared(session, monkeypatch):
    session.add(_nvr())
    # Camera already carries a now-unreachable direct IP from a prior import.
    session.add(Camera(nvr_id="nvr01", channel=1, enabled=True, ip="192.168.20.101"))
    await session.commit()

    async def fake_fetch(*a, **k):
        return {1: "192.168.20.101"}

    async def fake_probe(ip, port=554, timeout=1.5):
        return False

    monkeypatch.setattr(camera_import, "fetch_camera_ips", fake_fetch)
    monkeypatch.setattr(camera_import, "_probe_rtsp", fake_probe)

    nvr = (await session.execute(select(Nvr))).scalar_one()
    await camera_import.apply_camera_ips(session, nvr)

    cam = (await session.execute(select(Camera))).scalar_one()
    assert cam.ip is None  # dead direct IP cleared so main uses the NVR relay
