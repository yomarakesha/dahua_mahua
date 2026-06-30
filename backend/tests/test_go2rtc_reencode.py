"""go2rtc source builders: sub re-encode + switchable direct-main modes.

Subs: short-GOP ffmpeg re-encode (anti-freeze recipe). Mains: one of several
strategies selected by settings.main_stream_mode (native / copy_pipe /
reencode_pipe / reencode_rtsp / copy_rtsp) — switchable without code changes.
See app/services/go2rtc_reencode.py and [[anti-freeze-reencode-recipe]],
[[main-bottleneck-is-camera-delivery]].
"""

from types import SimpleNamespace

from app.models import StreamQuality
from app.services.go2rtc_reencode import (
    build_go2rtc_source,
    main_mode_is_exec,
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
        main_stream_mode="reencode_pipe",
        main_reencode_maxrate_kbps=8000,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_quality_inferred_from_name():
    assert quality_of_stream("nvr1_ch2") is StreamQuality.sub
    assert quality_of_stream("nvr1_ch2_main") is StreamQuality.main
    assert quality_of_stream("nvr1_ch2_main_nvr") is StreamQuality.main


# ── subs ──────────────────────────────────────────────────────────────────────
def test_sub_disabled_passes_through_raw():
    assert build_go2rtc_source("nvr1_ch2", URL, _settings(reencode_enabled=False)) == URL


def test_sub_raw_when_filter_excludes_it():
    assert build_go2rtc_source("nvr1_ch2", URL, _settings(reencode_qualities="main")) == URL


def test_sub_reencode_short_gop_over_tcp_to_output():
    cmd = build_go2rtc_source("nvr1_ch2", URL, _settings())
    assert cmd.startswith("exec:ffmpeg ")
    assert f"-i {URL} " in cmd                       # URL kept as one space-free token
    assert "-rtsp_transport tcp -i" in cmd          # subs keep TCP input
    assert "-c:v h264_qsv -async_depth 1" in cmd
    assert "-force_key_frames expr:gte(t,n_forced*0.5)" in cmd
    assert "-bf 0" in cmd
    assert cmd.rstrip().endswith("-f rtsp -rtsp_transport tcp {output}")
    assert " " not in URL                            # go2rtc has no shell


def test_sub_libx264_fallback_uses_zerolatency():
    cmd = build_go2rtc_source("nvr1_ch2", URL, _settings(reencode_vcodec="libx264"))
    assert "-c:v libx264 -preset veryfast -tune zerolatency" in cmd


# ── direct mains: switchable modes ────────────────────────────────────────────
def test_via_nvr_main_always_raw():
    # The NVR relay is the problem, not the transport — never wrap it, any mode.
    for mode in ("native", "copy_pipe", "reencode_pipe", "reencode_rtsp"):
        assert build_go2rtc_source("nvr1_ch2_main_nvr", URL, _settings(main_stream_mode=mode)) == URL


def test_main_mode_native_is_raw():
    assert build_go2rtc_source("nvr1_ch2_main", URL, _settings(main_stream_mode="native")) == URL


def test_main_mode_copy_pipe():
    src = build_go2rtc_source("nvr1_ch2_main", URL, _settings(main_stream_mode="copy_pipe"))
    assert src.startswith("exec:ffmpeg") and "-rtsp_transport udp -i" in src
    assert "-c copy" in src and "-c:v" not in src
    assert src.rstrip().endswith("-f mpegts -")


def test_main_mode_reencode_pipe_is_default():
    for s in (_settings(), _settings(main_stream_mode="reencode_pipe")):
        src = build_go2rtc_source("nvr1_ch2_main", URL, s)
        assert "-rtsp_transport udp -i" in src and "-c:v" in src
        assert "-force_key_frames" in src
        assert src.rstrip().endswith("-f mpegts -")
        assert "{output}" not in src


def test_main_mode_reencode_rtsp_targets_output():
    src = build_go2rtc_source("nvr1_ch2_main", URL, _settings(main_stream_mode="reencode_rtsp"))
    assert "-rtsp_transport udp -i" in src and "-c:v" in src
    assert src.rstrip().endswith("-f rtsp -rtsp_transport tcp {output}")


def test_main_mode_copy_rtsp_targets_output():
    src = build_go2rtc_source("nvr1_ch2_main", URL, _settings(main_stream_mode="copy_rtsp"))
    assert "-c copy" in src and src.rstrip().endswith("-f rtsp -rtsp_transport tcp {output}")


def test_main_reencode_bitrate_cap():
    # main modes use main_reencode_maxrate_kbps (not the sub's reencode_maxrate_kbps)
    capped = build_go2rtc_source("nvr1_ch2_main", URL, _settings(main_reencode_maxrate_kbps=6000))
    assert "-maxrate 6000k -bufsize 6000k" in capped
    uncapped = build_go2rtc_source("nvr1_ch2_main", URL, _settings(main_reencode_maxrate_kbps=0))
    assert "-maxrate" not in uncapped


def test_main_mode_is_exec_helper():
    assert main_mode_is_exec(_settings(main_stream_mode="native")) is False
    for mode in ("copy_pipe", "reencode_pipe", "reencode_rtsp", "copy_rtsp"):
        assert main_mode_is_exec(_settings(main_stream_mode=mode)) is True


def test_enabled_helper():
    s = _settings(reencode_qualities="sub")
    assert reencode_enabled_for(s, StreamQuality.sub) is True
    assert reencode_enabled_for(s, StreamQuality.main) is False
    assert reencode_enabled_for(_settings(reencode_enabled=False), StreamQuality.sub) is False
