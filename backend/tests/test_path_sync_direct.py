"""Source-selection tests: main goes direct to the camera when Camera.ip is
set; sub always stays on the NVR relay; no ip → everything via NVR (fallback).
"""

from app.crypto import encrypt_password
from app.models import Camera, Nvr, StreamQuality, Vendor
from app.services.path_sync import _build_path_config
from app.services.rtsp_probe import build_rtsp_url

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


def test_sub_stays_on_nvr_even_with_camera_ip():
    nvr = make_nvr()
    cam = make_cam("192.168.23.17")
    cfg = _build_path_config(nvr, cam, StreamQuality.sub)
    assert cfg["source"] == nvr_url(nvr, channel=7, subtype=1)


def test_main_without_ip_falls_back_to_nvr():
    nvr = make_nvr()
    cam = make_cam(None)
    cfg = _build_path_config(nvr, cam, StreamQuality.main)
    assert cfg["source"] == nvr_url(nvr, channel=7, subtype=0)
