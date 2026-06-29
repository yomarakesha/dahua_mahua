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


def test_via_nvr_main_is_never_reencoded():
    # _main_nvr pulls from the overloaded NVR — re-encoding it spawns a doomed
    # exec (i/o timeout → black). Must stay raw passthrough even when "both" is on.
    s = _settings(reencode_qualities="both")
    assert build_go2rtc_source("nvr1_ch2_main_nvr", URL, s) == URL
    # the DIRECT main still re-encodes
    assert build_go2rtc_source("nvr1_ch2_main", URL, s).startswith("exec:ffmpeg")


def test_qualities_filter_targets_only_chosen_quality():
    s = _settings(reencode_qualities="sub")
    # sub re-encoded (TCP input); direct main = UDP copy via MPEG-TS pipe
    assert build_go2rtc_source("nvr1_ch2", URL, s).startswith("exec:ffmpeg")
    main = build_go2rtc_source("nvr1_ch2_main", URL, s)
    assert "-rtsp_transport udp -i" in main and main.rstrip().endswith("-c copy -f mpegts -")

    s = _settings(reencode_qualities="main")
    assert build_go2rtc_source("nvr1_ch2", URL, s) == URL  # sub raw
    # main_pull_udp (UDP pipe) takes precedence over the re-encode filter
    assert build_go2rtc_source("nvr1_ch2_main", URL, s).rstrip().endswith("-c copy -f mpegts -")


def test_direct_main_pulls_udp_copy_via_pipe():
    s = _settings(reencode_qualities="sub")
    main = build_go2rtc_source("nvr1_ch2_main", URL, s)
    assert main.startswith("exec:ffmpeg")
    assert "-rtsp_transport udp -i" in main      # UDP pull
    assert f"-i {URL} " in main
    assert "-c copy" in main and "-c:v" not in main  # copy, not transcode
    assert main.rstrip().endswith("-f mpegts -")     # stdout pipe, not RTSP {output}
    assert "{output}" not in main
    # subs keep re-encode over TCP; via-NVR main stays raw
    assert "-rtsp_transport tcp -i" in build_go2rtc_source("nvr1_ch2", URL, s)
    assert build_go2rtc_source("nvr1_ch2_main_nvr", URL, s) == URL


def test_main_pull_udp_can_be_disabled():
    # main_pull_udp off + main not in re-encode filter → raw passthrough
    s = _settings(reencode_qualities="sub", main_pull_udp=False)
    assert build_go2rtc_source("nvr1_ch2_main", URL, s) == URL


def test_qsv_command_forces_short_gop_and_targets_go2rtc_output():
    # main_pull_udp off → the main takes the re-encode path (else it's a UDP pipe)
    cmd = build_go2rtc_source("nvr1_ch2_main", URL, _settings(main_pull_udp=False))
    assert cmd.startswith("exec:ffmpeg ")
    assert f"-i {URL} " in cmd  # source URL kept as one space-free token
    assert "-c:v h264_qsv -async_depth 1" in cmd
    assert "-force_key_frames expr:gte(t,n_forced*0.5)" in cmd
    assert "-bf 0" in cmd
    assert cmd.rstrip().endswith("-f rtsp -rtsp_transport tcp {output}")
    # go2rtc has no shell — no token may contain a space inside the source URL
    assert " " not in URL


def test_bitrate_cap_off_by_default_and_applied_when_set():
    # uncapped (0) → no VBV flags  (main_pull_udp off → re-encode path)
    assert "-maxrate" not in build_go2rtc_source("nvr1_ch2_main", URL, _settings(main_pull_udp=False))
    # capped → VBV maxrate + ~1s bufsize, before the keyframe forcing
    s = _settings(reencode_maxrate_kbps=6000, main_pull_udp=False)
    cmd = build_go2rtc_source("nvr1_ch2_main", URL, s)
    assert "-maxrate 6000k -bufsize 6000k" in cmd
    assert cmd.index("-maxrate") < cmd.index("-force_key_frames")


def test_main_scale_and_fps_apply_to_main_only():
    # scale/fps are re-encode levers, so exercise the main's re-encode path
    s = _settings(reencode_main_scale="1920:-2", reencode_main_fps=15, main_pull_udp=False)
    main = build_go2rtc_source("nvr1_ch2_main", URL, s)
    sub = build_go2rtc_source("nvr1_ch2", URL, s)
    # main downscaled + fps-capped
    assert "-vf scale=1920:-2" in main and "-r 15" in main
    # sub left at source resolution / fps
    assert "scale=" not in sub and "-r 15" not in sub
    # default: no scale/fps anywhere
    plain = build_go2rtc_source("nvr1_ch2_main", URL, _settings(main_pull_udp=False))
    assert "scale=" not in plain and "-r " not in plain


def test_libx264_fallback_uses_zerolatency():
    cmd = build_go2rtc_source("nvr1_ch2", URL, _settings(reencode_vcodec="libx264"))
    assert "-c:v libx264 -preset veryfast -tune zerolatency" in cmd


def test_enabled_helper():
    s = _settings(reencode_qualities="sub")
    assert reencode_enabled_for(s, StreamQuality.sub) is True
    assert reencode_enabled_for(s, StreamQuality.main) is False
    assert reencode_enabled_for(_settings(reencode_enabled=False), StreamQuality.sub) is False
