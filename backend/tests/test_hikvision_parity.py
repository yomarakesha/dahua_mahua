"""Hikvision NVR parity for the three Dahua-only backend flows:

  1. channel auto-detect via ISAPI (`detect_hikvision_channels`)
  2. the create flow dispatching autodetect by vendor (`_detect_channels` +
     `create_nvr`)
  3. camera-IP import from ISAPI InputProxy (`apply_camera_ips` for hikvision)

All network is mocked (httpx.MockTransport) — NO real sockets.
"""
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.crypto import encrypt_password
from app.db import Base
from app.models import Camera, Nvr, Vendor
from app.routers import nvrs as nvrs_router
from app.schemas import NvrCreate
from app.services import camera_import
from app.services import discovery
from app.services.camera_import import parse_input_proxy_channels
from app.services.discovery import detect_hikvision_channels
from app.services.rtsp_probe import ProbeResult


# ── Realistic ISAPI fixtures ─────────────────────────────────────────────────

INPUT_PROXY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<InputProxyChannelList version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
  <InputProxyChannel>
    <id>1</id>
    <name>Front Gate</name>
    <sourceInputPortDescriptor>
      <ipAddress>192.168.20.101</ipAddress>
      <managePortNo>8000</managePortNo>
      <srcInputPort>1</srcInputPort>
    </sourceInputPortDescriptor>
  </InputProxyChannel>
  <InputProxyChannel>
    <id>2</id>
    <name>Yard</name>
    <sourceInputPortDescriptor>
      <ipAddress>192.168.20.102</ipAddress>
    </sourceInputPortDescriptor>
  </InputProxyChannel>
  <InputProxyChannel>
    <id>3</id>
    <name>Lobby</name>
    <sourceInputPortDescriptor>
      <ipAddress>192.168.20.103</ipAddress>
    </sourceInputPortDescriptor>
  </InputProxyChannel>
</InputProxyChannelList>"""

# Some firmware prefixes a namespace on every element.
INPUT_PROXY_XML_NS = """<?xml version="1.0" encoding="UTF-8"?>
<hik:InputProxyChannelList xmlns:hik="http://www.hikvision.com/ver20/XMLSchema">
  <hik:InputProxyChannel>
    <hik:id>1</hik:id>
    <hik:sourceInputPortDescriptor>
      <hik:ipAddress>10.0.0.5</hik:ipAddress>
    </hik:sourceInputPortDescriptor>
  </hik:InputProxyChannel>
</hik:InputProxyChannelList>"""

VIDEO_INPUTS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<VideoInputChannelList version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
  <VideoInputChannel><id>1</id><inputPort>1</inputPort></VideoInputChannel>
  <VideoInputChannel><id>2</id><inputPort>2</inputPort></VideoInputChannel>
</VideoInputChannelList>"""


def _mock_httpx(monkeypatch, target_module, handler):
    """Replace `<module>.httpx.AsyncClient` with one backed by a MockTransport.

    Constructor kwargs (auth/trust_env/timeout) are accepted and ignored so the
    real call sites work unchanged. The handler returns 200 directly, so a
    DigestAuth passed to .get() short-circuits without a 401 round-trip.
    """
    transport = httpx.MockTransport(handler)
    real_client_cls = httpx.AsyncClient

    def _factory(*args, **kwargs):
        return real_client_cls(transport=transport)

    monkeypatch.setattr(target_module.httpx, "AsyncClient", _factory)


# ── 1. detect_hikvision_channels ─────────────────────────────────────────────


async def test_detect_primary_input_proxy_count(monkeypatch):
    def handler(request):
        assert request.url.path == "/ISAPI/ContentMgmt/InputProxy/channels"
        return httpx.Response(200, text=INPUT_PROXY_XML)

    _mock_httpx(monkeypatch, discovery, handler)
    assert await detect_hikvision_channels("192.168.20.28", "admin", "pw") == 3


async def test_detect_falls_back_to_video_inputs(monkeypatch):
    def handler(request):
        if request.url.path == "/ISAPI/ContentMgmt/InputProxy/channels":
            return httpx.Response(404, text="<err/>")
        if request.url.path == "/ISAPI/System/Video/inputs/channels":
            return httpx.Response(200, text=VIDEO_INPUTS_XML)
        return httpx.Response(500)

    _mock_httpx(monkeypatch, discovery, handler)
    assert await detect_hikvision_channels("192.168.20.28", "admin", "pw") == 2


async def test_detect_error_returns_none(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("no route to host")

    _mock_httpx(monkeypatch, discovery, handler)
    assert await detect_hikvision_channels("192.168.20.28", "admin", "pw") is None


async def test_detect_unparseable_returns_none(monkeypatch):
    def handler(request):
        return httpx.Response(200, text="not xml at all")

    _mock_httpx(monkeypatch, discovery, handler)
    assert await detect_hikvision_channels("192.168.20.28", "admin", "pw") is None


# ── 2. create flow vendor dispatch ───────────────────────────────────────────


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


async def test_detect_channels_dispatches_by_vendor(monkeypatch):
    calls = {}

    async def fake_dahua(ip, u, p):
        calls["dahua"] = ip
        return 16

    async def fake_hik(ip, u, p):
        calls["hik"] = ip
        return 8

    monkeypatch.setattr(nvrs_router, "detect_dahua_channels", fake_dahua)
    monkeypatch.setattr(nvrs_router, "detect_hikvision_channels", fake_hik)

    assert await nvrs_router._detect_channels(Vendor.dahua, "1.1.1.1", "a", "b") == 16
    assert await nvrs_router._detect_channels(Vendor.hikvision, "2.2.2.2", "a", "b") == 8
    assert calls == {"dahua": "1.1.1.1", "hik": "2.2.2.2"}


async def test_create_hikvision_autodetects_channels(session, monkeypatch):
    """create_nvr for a hikvision NVR must call the Hikvision detector and
    create that many cameras."""
    async def fake_hik(ip, u, p):
        return 4

    def fake_probe(*a, **k):
        return ProbeResult(True, "OK")

    async def fake_apply(sess, nvr):
        return (0, 0)

    async def fake_reconcile(sess, **k):
        return None

    async def no_lockout(ip):
        return None

    monkeypatch.setattr(nvrs_router.lockouts, "get_active_lockout", no_lockout)
    monkeypatch.setattr(nvrs_router, "detect_hikvision_channels", fake_hik)
    monkeypatch.setattr(nvrs_router, "probe_rtsp", fake_probe)
    monkeypatch.setattr(camera_import, "apply_camera_ips", fake_apply)
    monkeypatch.setattr(nvrs_router.relay_sync, "reconcile", fake_reconcile)

    body = NvrCreate(
        label="hik", ip="192.168.20.28", rtsp_username="admin",
        rtsp_password="pw", vendor=Vendor.hikvision,
    )
    out = await nvrs_router.create_nvr(body, session, None)

    assert out.camera_count == 4
    cams = (
        await session.execute(select(Camera).where(Camera.nvr_id == out.id))
    ).scalars().all()
    assert sorted(c.channel for c in cams) == [1, 2, 3, 4]


# ── 3. apply_camera_ips for hikvision ────────────────────────────────────────


def test_parse_input_proxy_basic():
    assert parse_input_proxy_channels(INPUT_PROXY_XML) == {
        1: "192.168.20.101",
        2: "192.168.20.102",
        3: "192.168.20.103",
    }


def test_parse_input_proxy_namespaced():
    assert parse_input_proxy_channels(INPUT_PROXY_XML_NS) == {1: "10.0.0.5"}


def test_parse_input_proxy_bad_xml_is_empty():
    assert parse_input_proxy_channels("<<< not xml") == {}
    assert parse_input_proxy_channels("") == {}


def test_parse_input_proxy_skips_placeholder_ip():
    xml = """<InputProxyChannelList>
      <InputProxyChannel><id>1</id>
        <sourceInputPortDescriptor><ipAddress>0.0.0.0</ipAddress></sourceInputPortDescriptor>
      </InputProxyChannel>
    </InputProxyChannelList>"""
    assert parse_input_proxy_channels(xml) == {}


def _hik_nvr() -> Nvr:
    return Nvr(
        id="hik01", label="t", ip="192.168.20.28",
        rtsp_password_encrypted=encrypt_password("pw"),
        vendor=Vendor.hikvision, enabled=True,
    )


async def test_apply_camera_ips_hikvision_fills_ips(session, monkeypatch):
    session.add(_hik_nvr())
    session.add(Camera(nvr_id="hik01", channel=1, enabled=True))
    session.add(Camera(nvr_id="hik01", channel=2, enabled=True))
    session.add(Camera(nvr_id="hik01", channel=3, enabled=True))
    await session.commit()

    def handler(request):
        assert request.url.path == "/ISAPI/ContentMgmt/InputProxy/channels"
        return httpx.Response(200, text=INPUT_PROXY_XML)

    _mock_httpx(monkeypatch, camera_import, handler)

    # Only ch1+ch2 answer RTSP; ch3 is PoE-hidden → falls back to relay.
    async def fake_filter(ips, **kw):
        return {"192.168.20.101", "192.168.20.102"}

    monkeypatch.setattr(camera_import, "filter_reachable", fake_filter)

    nvr = (await session.execute(select(Nvr))).scalar_one()
    found, updated = await camera_import.apply_camera_ips(session, nvr)

    assert found == 3
    by_ch = {
        c.channel: c.ip
        for c in (await session.execute(select(Camera))).scalars()
    }
    assert by_ch[1] == "192.168.20.101"
    assert by_ch[2] == "192.168.20.102"
    assert by_ch[3] is None  # reported but unreachable → NVR relay


async def test_apply_camera_ips_hikvision_bad_xml_non_fatal(session, monkeypatch):
    session.add(_hik_nvr())
    session.add(Camera(nvr_id="hik01", channel=1, enabled=True))
    await session.commit()

    def handler(request):
        return httpx.Response(200, text="totally not xml")

    _mock_httpx(monkeypatch, camera_import, handler)

    nvr = (await session.execute(select(Nvr))).scalar_one()
    found, updated = await camera_import.apply_camera_ips(session, nvr)

    assert (found, updated) == (0, 0)  # import 0, no crash
