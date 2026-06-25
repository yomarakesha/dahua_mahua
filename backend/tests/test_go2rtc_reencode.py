"""Anti-freeze re-encode source builder for go2rtc.

Locks in the recipe that made 4MP stable pre-redesign: short-GOP ffmpeg
re-encode, on-demand, driven by REENCODE_* settings. See
app/services/go2rtc_reencode.py and the [[anti-freeze-reencode-recipe]] note.
"""

from types import SimpleNamespace

from app.models import StreamQuality
from app.services.go2rtc_reencode import (
    build_go2rtc_source,
    quality_of_stream,
    reencode_enabled_for,
)

URL = "rtsp://admin:pw@192.168.20.102:554/cam/realmonitor?channel=1&subtype=0"


def _settings(**over):
    base = dict(
        reencode_enabled=True,
        reencode_keyframe_seconds=0.5,
        reencode_qualities="both",
        reencode_vcodec="h264_qsv",
        reencode_preset="veryfast",
        reencode_ffmpeg_bin="ffmpeg",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_quality_inferred_from_name():
    assert quality_of_stream("nvr1_ch2") is StreamQuality.sub
    assert quality_of_stream("nvr1_ch2_main") is StreamQuality.main
    assert quality_of_stream("nvr1_ch2_main_nvr") is StreamQuality.main


def test_disabled_passes_source_through_raw():
    s = _settings(reencode_enabled=False)
    assert build_go2rtc_source("nvr1_ch2", URL, s) == URL


def test_qualities_filter_targets_only_chosen_quality():
    s = _settings(reencode_qualities="sub")
    # sub re-encoded, main left raw
    assert build_go2rtc_source("nvr1_ch2", URL, s).startswith("exec:ffmpeg")
    assert build_go2rtc_source("nvr1_ch2_main", URL, s) == URL

    s = _settings(reencode_qualities="main")
    assert build_go2rtc_source("nvr1_ch2", URL, s) == URL
    assert build_go2rtc_source("nvr1_ch2_main", URL, s).startswith("exec:ffmpeg")


def test_qsv_command_forces_short_gop_and_targets_go2rtc_output():
    cmd = build_go2rtc_source("nvr1_ch2_main", URL, _settings())
    assert cmd.startswith("exec:ffmpeg ")
    assert f"-i {URL} " in cmd  # source URL kept as one space-free token
    assert "-c:v h264_qsv -async_depth 1" in cmd
    assert "-force_key_frames expr:gte(t,n_forced*0.5)" in cmd
    assert "-bf 0" in cmd
    assert cmd.rstrip().endswith("-f rtsp -rtsp_transport tcp {output}")
    # go2rtc has no shell — no token may contain a space inside the source URL
    assert " " not in URL


def test_libx264_fallback_uses_zerolatency():
    cmd = build_go2rtc_source("nvr1_ch2", URL, _settings(reencode_vcodec="libx264"))
    assert "-c:v libx264 -preset veryfast -tune zerolatency" in cmd


def test_enabled_helper():
    s = _settings(reencode_qualities="sub")
    assert reencode_enabled_for(s, StreamQuality.sub) is True
    assert reencode_enabled_for(s, StreamQuality.main) is False
    assert reencode_enabled_for(_settings(reencode_enabled=False), StreamQuality.sub) is False
