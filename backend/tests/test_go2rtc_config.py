"""File-based go2rtc stream sync + auto codec resolution.

Covers the re-encode path that can't go through go2rtc's API (exec sources are
API-rejected): writing the streams section while preserving everything else, and
picking a WORKING encoder (hardware probe → CPU fallback). See
[[anti-freeze-reencode-recipe]].
"""

import yaml

import app.services.go2rtc_reencode as rc
from app.services.go2rtc_config import read_streams, write_streams


# ── file writer ──────────────────────────────────────────────────────────────

def test_write_streams_preserves_other_sections(tmp_path):
    cfg = tmp_path / "go2rtc.yaml"
    cfg.write_text(yaml.safe_dump({
        "api": {"listen": ":1984", "origin": "*"},
        "rtsp": {"listen": ":8554"},
        "streams": {"old": ["rtsp://stale"]},
    }))
    write_streams(str(cfg), {
        "nvr_ch1": "rtsp://cam1",
        "nvr_ch2_main": "exec:ffmpeg -i rtsp://cam2 ... {output}",
    })
    loaded = yaml.safe_load(cfg.read_text())
    # untouched sections survive
    assert loaded["api"] == {"listen": ":1984", "origin": "*"}
    assert loaded["rtsp"] == {"listen": ":8554"}
    # streams fully replaced (old gone), exec source intact
    assert set(loaded["streams"]) == {"nvr_ch1", "nvr_ch2_main"}
    assert "old" not in loaded["streams"]
    assert loaded["streams"]["nvr_ch2_main"][0].startswith("exec:ffmpeg")


def test_read_streams_roundtrip_and_normalises_forms(tmp_path):
    cfg = tmp_path / "go2rtc.yaml"
    cfg.write_text(yaml.safe_dump({"streams": {
        "as_list": ["rtsp://a"],
        "as_str": "rtsp://b",
        "as_obj": {"producers": ["exec:ffmpeg c {output}"]},
    }}))
    got = read_streams(str(cfg))
    assert got == {"as_list": "rtsp://a", "as_str": "rtsp://b",
                   "as_obj": "exec:ffmpeg c {output}"}


def test_read_streams_missing_file_is_empty(tmp_path):
    assert read_streams(str(tmp_path / "nope.yaml")) == {}


def test_write_then_read_is_stable(tmp_path):
    cfg = tmp_path / "go2rtc.yaml"
    desired = {"a": "rtsp://x", "b": "exec:ffmpeg y {output}"}
    write_streams(str(cfg), desired)
    assert read_streams(str(cfg)) == desired  # idempotent compare won't loop


# ── auto codec resolution ────────────────────────────────────────────────────

class _S:
    def __init__(self, vcodec="auto", ffbin="ffmpeg"):
        self.reencode_vcodec = vcodec
        self.reencode_ffmpeg_bin = ffbin
        self.reencode_preset = "veryfast"


def test_explicit_codec_skips_probe(monkeypatch):
    rc.reset_vcodec_cache()
    monkeypatch.setattr(rc, "_test_encoder", lambda *a: (_ for _ in ()).throw(AssertionError("probed!")))
    assert rc.resolve_vcodec(_S(vcodec="libx264")) == "libx264"


def test_auto_falls_back_to_libx264_when_no_hw(monkeypatch):
    rc.reset_vcodec_cache()
    monkeypatch.setattr(rc, "_test_encoder", lambda ffbin, vc: False)  # no GPU
    assert rc.resolve_vcodec(_S()) == "libx264"


def test_auto_picks_first_working_hw(monkeypatch):
    rc.reset_vcodec_cache()
    monkeypatch.setattr(rc, "_test_encoder", lambda ffbin, vc: vc == "h264_nvenc")
    assert rc.resolve_vcodec(_S()) == "h264_nvenc"


def test_auto_result_is_cached(monkeypatch):
    rc.reset_vcodec_cache()
    calls = []
    monkeypatch.setattr(rc, "_test_encoder", lambda ffbin, vc: calls.append(vc) or False)
    rc.resolve_vcodec(_S())
    n = len(calls)
    rc.resolve_vcodec(_S())  # cached → no new probes
    assert len(calls) == n
    rc.reset_vcodec_cache()
