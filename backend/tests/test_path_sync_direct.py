"""Source-selection tests: main goes direct to the camera when Camera.ip is
set; sub always stays on the NVR relay; no ip → everything via NVR (fallback).
"""

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.crypto import encrypt_password
from app.db import Base
from app.models import Camera, Nvr, StreamQuality, Vendor
from app.services.path_sync import (
    _build_path_config,
    _desired_paths,
    _is_dss_managed,
    path_name,
)
from app.services.rtsp_probe import build_rtsp_url
from app.services.source_watch import _parse_path

PW = "secret-pw"


def make_nvr() -> Nvr:
    return Nvr(
        id="nvr01",
        label="test",
        ip="192.168.20.58",
        port=554,
        rtsp_username="admin",
        rtsp_password_encrypted=encrypt_password(PW),
        vendor=Vendor.dahua,
    )


def make_cam(ip: str | None) -> Camera:
    return Camera(nvr_id="nvr01", channel=7, ip=ip)


def nvr_url(nvr: Nvr, channel: int, subtype: int) -> str:
    return build_rtsp_url(
        ip=nvr.ip, port=nvr.port, channel=channel, vendor=nvr.vendor,
        subtype=subtype, username=nvr.rtsp_username, password=PW,
    )


def test_main_with_camera_ip_pulls_direct():
    nvr = make_nvr()
    cam = make_cam("192.168.23.17")
    cfg = _build_path_config(nvr, cam, StreamQuality.main)
    # Direct camera URL: the camera's own channel is always 1.
    assert cfg["source"] == build_rtsp_url(
        ip="192.168.23.17", port=554, channel=1, vendor=nvr.vendor,
        subtype=0, username="admin", password=PW,
    )


def test_sub_with_camera_ip_pulls_direct():
    # Since bc64f1b, subs also pull direct from the camera IP (not the NVR) when
    # the camera has one — one NVR can't re-stream many channels without dropping.
    nvr = make_nvr()
    cam = make_cam("192.168.23.17")
    cfg = _build_path_config(nvr, cam, StreamQuality.sub)
    assert cfg["source"] == build_rtsp_url(
        ip="192.168.23.17", port=554, channel=1, vendor=nvr.vendor,
        subtype=1, username="admin", password=PW,
    )


def test_main_without_ip_falls_back_to_nvr():
    nvr = make_nvr()
    cam = make_cam(None)
    cfg = _build_path_config(nvr, cam, StreamQuality.main)
    assert cfg["source"] == nvr_url(nvr, channel=7, subtype=0)


# ── per-camera source toggle: the _main_nvr relay variant ────────────────────

def test_force_relay_uses_nvr_even_with_camera_ip():
    """The _main_nvr toggle variant pulls via the NVR even when a direct IP
    exists — that's the whole point of letting the operator switch back."""
    nvr = make_nvr()
    cam = make_cam("192.168.23.17")
    cfg = _build_path_config(nvr, cam, StreamQuality.main, force_relay=True)
    assert cfg["source"] == nvr_url(nvr, channel=7, subtype=0)


def test_path_name_relay_variant():
    assert path_name("nvr01", 7, StreamQuality.main) == "nvr01_ch7_main"
    assert path_name("nvr01", 7, StreamQuality.main, relay_variant=True) == "nvr01_ch7_main_nvr"
    # sub never has a relay variant
    assert path_name("nvr01", 7, StreamQuality.sub) == "nvr01_ch7"


def test_main_nvr_variant_is_dss_managed_and_parsed():
    # reconcile must own it (create/patch/clean), not treat it as a foreign orphan
    assert _is_dss_managed("nvr01_ch7_main_nvr") is True
    assert _is_dss_managed("nvr01_ch7_main") is True
    assert _is_dss_managed("nvr01_ch7") is True
    assert _is_dss_managed("lobby_test") is False
    # the watchdog must map the variant back to the same (nvr, channel)
    assert _parse_path("nvr01_ch7_main_nvr") == ("nvr01", 7)
    assert _parse_path("nvr-192-168-20-15_ch3_main_nvr") == ("nvr-192-168-20-15", 3)


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


async def test_desired_paths_emits_both_variants_only_for_direct_cameras(session):
    nvr = make_nvr()
    nvr.enabled = True
    session.add(nvr)
    # ch7 has a direct IP → gets sub + _main (direct) + _main_nvr (relay).
    session.add(Camera(nvr_id="nvr01", channel=7, ip="192.168.20.101",
                       enabled=True, has_main=True, has_sub=True))
    # ch8 is relay-only → sub + _main (relay), NO _main_nvr.
    session.add(Camera(nvr_id="nvr01", channel=8, ip=None,
                       enabled=True, has_main=True, has_sub=True))
    await session.commit()

    desired = await _desired_paths(session)

    # direct camera: all three, with the right sources
    assert "nvr01_ch7" in desired                       # sub
    assert "nvr01_ch7_main" in desired                  # direct main
    assert "nvr01_ch7_main_nvr" in desired              # via-NVR variant
    assert desired["nvr01_ch7_main"]["source"] == build_rtsp_url(
        ip="192.168.20.101", port=554, channel=1, vendor=nvr.vendor,
        subtype=0, username="admin", password=PW,
    )
    assert desired["nvr01_ch7_main_nvr"]["source"] == nvr_url(nvr, channel=7, subtype=0)

    # relay-only camera: no _main_nvr variant
    assert "nvr01_ch8_main" in desired
    assert "nvr01_ch8_main_nvr" not in desired
    assert desired["nvr01_ch8_main"]["source"] == nvr_url(nvr, channel=8, subtype=0)
