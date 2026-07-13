"""File-based go2rtc stream sync + auto codec resolution.

Covers the re-encode path that can't go through go2rtc's API (exec sources are
API-rejected): writing the streams section while preserving everything else, and
picking a WORKING encoder (hardware probe → CPU fallback). See
[[anti-freeze-reencode-recipe]].
"""

import yaml

import app.services.go2rtc_reencode as rc
import app.services.go2rtc_config as g2cfg
from app.services.go2rtc_config import (
    read_streams,
    render_runtime_config,
    resolve_webrtc_candidates,
    write_streams,
)


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


# ── WebRTC candidate resolution + rendering (portability) ─────────────────────

class _WS:
    def __init__(self, candidates="", port=8556):
        self.go2rtc_webrtc_candidates = candidates
        self.go2rtc_webrtc_port = port


def test_explicit_candidates_are_parsed_and_port_appended():
    s = _WS(candidates="10.0.0.5:8556, 192.168.1.20 ,  ")  # bare host gets port
    assert resolve_webrtc_candidates(s) == ["10.0.0.5:8556", "192.168.1.20:8556"]


def test_empty_setting_auto_detects_lan_ips(monkeypatch):
    monkeypatch.setattr(
        "app.net.detect_lan_ipv4s", lambda: ["10.10.1.152", "192.168.1.13"]
    )
    assert resolve_webrtc_candidates(_WS(candidates="")) == [
        "10.10.1.152:8556",
        "192.168.1.13:8556",
    ]


def test_render_injects_candidates_and_preserves_sections(tmp_path, monkeypatch):
    monkeypatch.setattr("app.net.detect_lan_ipv4s", lambda: ["10.0.0.9"])
    base = tmp_path / "go2rtc.base.yaml"
    base.write_text(yaml.safe_dump({
        "api": {"listen": ":1984", "origin": "*"},
        "webrtc": {"listen": ":8556"},
        "log": {"level": "warn"},
    }))
    out = tmp_path / ".go2rtc" / "go2rtc.yaml"
    cands = render_runtime_config(str(base), str(out), _WS(candidates=""))
    assert cands == ["10.0.0.9:8556"]
    loaded = yaml.safe_load(out.read_text())
    assert loaded["webrtc"]["listen"] == ":8556"           # listen preserved
    assert loaded["webrtc"]["candidates"] == ["10.0.0.9:8556"]
    assert loaded["api"] == {"listen": ":1984", "origin": "*"}  # other sections intact
    assert loaded["log"] == {"level": "warn"}


def test_render_omits_candidates_when_none_detected(tmp_path, monkeypatch):
    monkeypatch.setattr("app.net.detect_lan_ipv4s", lambda: [])
    base = tmp_path / "go2rtc.base.yaml"
    base.write_text(yaml.safe_dump({"webrtc": {"listen": ":8556"}}))
    out = tmp_path / "go2rtc.yaml"
    cands = render_runtime_config(str(base), str(out), _WS(candidates=""))
    assert cands == []
    loaded = yaml.safe_load(out.read_text())
    assert "candidates" not in loaded["webrtc"]  # left to go2rtc auto-advertise


def test_committed_base_has_no_hardcoded_candidates():
    # The deploy-specific IPs must never ship in the committed template.
    from pathlib import Path
    base = Path(__file__).resolve().parent.parent.parent / "go2rtc.base.yaml"
    cfg = yaml.safe_load(base.read_text())
    assert "candidates" not in (cfg.get("webrtc") or {})
