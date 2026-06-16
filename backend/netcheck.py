"""Unattended NVR / network measurement for the DSS packet-loss diagnosis.

WHY: we need to tell apart two VPN-independent causes of "RTP packets lost":
  (a) the NVR's own output capacity (it drops frames when asked for too many
      simultaneous streams), vs
  (b) the 100 Mbps Ethernet link saturating.
The fix differs: (a) → fewer concurrent streams / NVR settings; (b) → gigabit.

HOW: launch an ISOLATED MediaMTX (alt ports, own log) that pulls 1 → N main
streams from the NVR with sourceOnDemand OFF, and record per phase:
  - aggregate bitrate (bytesReceived delta from MediaMTX's own API),
  - total "RTP packets lost" (parsed from MediaMTX's own log),
  - Ethernet RX throughput,
  - how many sources actually became ready.

RUN THIS WITH THE VPN OFF. Launch via ../run-netcheck.ps1 (preferred) or:
    backend/.venv/Scripts/python.exe netcheck.py
Results are written to ../netcheck-result.md (password is masked).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent

# Resolve everything against backend/ so .env + ./dss.db load exactly as the
# app sees them, regardless of how the script was launched.
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

# Re-exec under the backend venv if launched with another interpreter (e.g.
# `py netcheck.py`) — the venv has cryptography/httpx; the global Python may not.
_VENV_PY = BACKEND / ".venv" / "Scripts" / "python.exe"
if _VENV_PY.exists() and Path(sys.executable).resolve() != _VENV_PY.resolve():
    # subprocess (not os.execv — unreliable on Windows: detaches, loses stdout).
    sys.exit(subprocess.run(
        [str(_VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]]
    ).returncode)

# Windows console may be cp1251 which can't encode →/§ — never let printing
# kill the run (the report file itself is always written as UTF-8).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MTX = ROOT / "mediamtx.exe"
RESULT = ROOT / "netcheck-result.md"
WORK = ROOT / ".netcheck"
NVR_IP = os.environ.get("NETCHECK_NVR_IP", "192.168.20.58")
API = "127.0.0.1:9998"          # isolated — does NOT touch the DSS instance (9997)
RTSP_PORT = 8555
ETH = "Ethernet"
IFACE_IDX: int | None = None    # egress interface to the NVR, detected at runtime

# Channels to exercise and the phases (label, subtype 0=main/1=sub, channels).
CH = [1, 2, 3, 4, 5, 6, 7, 8]
PHASES = [
    ("1 main", 0, CH[:1]),
    ("2 main", 0, CH[:2]),
    ("4 main", 0, CH[:4]),
    ("8 main", 0, CH[:8]),
    ("8 sub (baseline)", 1, CH[:8]),
]
WARMUP = 6        # let sources connect before we start counting
MEASURE = 30      # measurement window per phase (seconds)

REPORT: list[str] = []


def out(line: str = "") -> None:
    print(line, flush=True)
    REPORT.append(line)


def ps(cmd: str, timeout: int = 30) -> str:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return (r.stdout or "").strip()
    except Exception as e:  # noqa: BLE001
        return f"<ps-error: {e}>"


def mask(url: str) -> str:
    return re.sub(r"//([^:]+):[^@]+@", r"//\1:***@", url)


# ── credentials ──────────────────────────────────────────────────────────────

def load_nvr() -> dict:
    # Env override — point at ANY NVR without it being seeded in the DB.
    env_user = os.environ.get("NETCHECK_NVR_USER")
    env_pass = os.environ.get("NETCHECK_NVR_PASS")
    if env_user and env_pass:
        return {
            "id": f"env-{NVR_IP}", "ip": NVR_IP,
            "port": int(os.environ.get("NETCHECK_NVR_PORT", "554")),
            "user": env_user, "password": env_pass,
            "vendor": os.environ.get("NETCHECK_NVR_VENDOR", "dahua"),
        }
    db = BACKEND / "dss.db"
    if not db.exists():
        db = ROOT / "dss.db"
    con = sqlite3.connect(str(db))
    try:
        row = con.execute(
            "SELECT id, ip, port, rtsp_username, rtsp_password_encrypted, vendor "
            "FROM nvrs WHERE ip = ?", (NVR_IP,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        raise SystemExit(f"NVR {NVR_IP} not found in {db}")
    from app.crypto import decrypt_password
    return {
        "id": row[0], "ip": row[1], "port": row[2], "user": row[3],
        "password": decrypt_password(row[4]),
        "vendor": (row[5] or "dahua"),
    }


def url_for(nvr: dict, ch: int, subtype: int) -> str:
    from app.services.rtsp_probe import build_rtsp_url
    return build_rtsp_url(
        nvr["ip"], nvr["port"], ch, vendor=nvr["vendor"], subtype=subtype,
        username=nvr["user"], password=nvr["password"],
    )


# ── helpers for the isolated MediaMTX ────────────────────────────────────────

def write_cfg(paths: dict[str, str], logfile: Path, cfg: Path) -> None:
    lines = [
        "logLevel: warn",
        "logDestinations: [file]",
        f"logFile: {logfile.as_posix()}",
        "api: yes",
        f"apiAddress: {API}",
        "rtsp: yes",
        f"rtspAddress: :{RTSP_PORT}",
        "rtspTransports: [tcp]",
        "webrtc: no",
        "hls: no",
        "rtmp: no",
        "srt: no",
        "metrics: no",
        "playback: no",
        "paths:",
    ]
    for name, src in paths.items():
        lines += [
            f"  {name}:",
            f'    source: "{src}"',
            "    sourceOnDemand: no",
            "    rtspTransport: tcp",
        ]
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")


def api_items() -> list[dict]:
    with urllib.request.urlopen(
        f"http://{API}/v3/paths/list?itemsPerPage=500", timeout=5
    ) as r:
        return json.load(r).get("items", [])


def wait_api(deadline: float) -> bool:
    while time.time() < deadline:
        try:
            api_items()
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


def find_iface_index(ip: str) -> int | None:
    """InterfaceIndex of the egress interface to `ip` (ASCII-safe number — the
    InterfaceAlias may be non-ASCII/Cyrillic and mangles through the console)."""
    s = ps(f"(Find-NetRoute -RemoteIPAddress {ip} -ErrorAction SilentlyContinue | "
           f"Select-Object -First 1).InterfaceIndex")
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return None


def _adapter_sel() -> str:
    return f"-InterfaceIndex {IFACE_IDX}" if IFACE_IDX else f"-Name '{ETH}'"


def eth_rx() -> int | None:
    v = ps(f"(Get-NetAdapter {_adapter_sel()} | Get-NetAdapterStatistics).ReceivedBytes")
    try:
        return int(v)
    except ValueError:
        return None


def _slug(label: str) -> str:
    return re.sub(r"\W+", "_", label).strip("_") or "phase"


def _measure(label: str, paths: dict[str, str], streams: int) -> dict:
    cfg = WORK / f"cfg_{_slug(label)}.yml"
    logf = WORK / f"log_{_slug(label)}.log"
    logf.write_text("", encoding="utf-8")
    write_cfg(paths, logf, cfg)

    proc = subprocess.Popen(
        [str(MTX), str(cfg)], cwd=str(ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    res = {"label": label, "streams": streams, "ready": 0,
           "mbps": None, "eth_mbps": None, "lost": None, "note": ""}
    try:
        if not wait_api(time.time() + 15):
            res["note"] = "MediaMTX API never came up (port busy / launch failed)"
            return res
        time.sleep(WARMUP)
        try:
            items0 = {i["name"]: i.get("bytesReceived", 0) for i in api_items()}
        except Exception as e:  # noqa: BLE001
            res["note"] = f"sample0 failed: {e}"
            return res
        e0, t0 = eth_rx(), time.time()
        time.sleep(MEASURE)
        items = api_items()
        e1, t1 = eth_rx(), time.time()
        dt = max(0.001, t1 - t0)

        res["ready"] = sum(1 for i in items if i.get("ready"))
        delta = sum(i.get("bytesReceived", 0) - items0.get(i["name"], 0) for i in items)
        res["mbps"] = round(delta * 8 / dt / 1e6, 1)
        if e0 is not None and e1 is not None:
            res["eth_mbps"] = round((e1 - e0) * 8 / dt / 1e6, 1)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(1)

    text = logf.read_text(errors="replace")
    res["lost"] = sum(int(m) for m in re.findall(r"(\d+) RTP packets lost", text))
    return res


def run_phase(label: str, subtype: int, chans: list[int], nvr: dict) -> dict:
    paths = {f"t_ch{ch}": url_for(nvr, ch, subtype) for ch in chans}
    return _measure(label, paths, len(chans))


def link_diag() -> None:
    """Physical-link health (§3.5): a 100 Mbps half-duplex / errored link drops
    packets even at low utilisation — the prime suspect when loss appears far
    below the link cap. NVR5232-EI supports gigabit, so 100 Mbps is itself a flag."""
    out("## Physical link (§3.5)")
    sel = _adapter_sel()
    speed = ps(f"(Get-NetAdapter {sel}).LinkSpeed")
    duplex = ps(f"(Get-NetAdapter {sel}).FullDuplex")
    out(f"- link speed: {speed}   full-duplex: {duplex}")
    stats = ps(
        f"$s=Get-NetAdapter {sel} | Get-NetAdapterStatistics; "
        "\"rxErrors=$($s.ReceivedPacketErrors) rxDiscarded=$($s.ReceivedDiscardedPackets) "
        "txErrors=$($s.OutboundPacketErrors) txDiscarded=$($s.OutboundDiscardedPackets)\""
    )
    out(f"- NIC counters (cumulative — non-zero/growing = bad cable/port/duplex): {stats}")
    out()


def run_ab(nvr: dict) -> dict | None:
    """§3.1 DECISIVE: same physical camera, two paths — via the NVR relay vs
    straight from the camera. Gated on NETCHECK_CAM_IP (camera must be reachable;
    set a working secondary IP in the camera subnet first, VPN off)."""
    cam_ip = os.environ.get("NETCHECK_CAM_IP")
    if not cam_ip:
        out("## §3.1 DECISIVE (skipped)")
        out("- Set `NETCHECK_CAM_IP` (and `NETCHECK_CAM_CH` = that camera's channel on "
            "the NVR) to run the decisive camera-vs-NVR comparison.")
        out()
        return None

    from app.services.rtsp_probe import build_rtsp_url

    cam_ch = int(os.environ.get("NETCHECK_CAM_CH", "1"))
    cam_port = int(os.environ.get("NETCHECK_CAM_PORT", "554"))
    cam_user = os.environ.get("NETCHECK_CAM_USER", nvr["user"])
    cam_pass = os.environ.get("NETCHECK_CAM_PASS", nvr["password"])

    url_a = url_for(nvr, cam_ch, 0)                       # via NVR, main
    url_b = build_rtsp_url(                               # direct camera, ch1 main
        cam_ip, cam_port, 1, vendor=nvr["vendor"], subtype=0,
        username=cam_user, password=cam_pass,
    )
    out("## §3.1 DECISIVE — same main stream: via NVR vs direct from camera")
    out(f"- A via NVR (ch{cam_ch}): `{mask(url_a)}`")
    out(f"- B direct camera:       `{mask(url_b)}`")
    out()
    a = _measure(f"A via-NVR ch{cam_ch}", {"ab_nvr": url_a}, 1)
    b = _measure("B direct-cam", {"ab_cam": url_b}, 1)
    out("| path | ready | Mbps | RTP packets lost |")
    out("|---|---|---|---|")
    for r in (a, b):
        out(f"| {r['label']} | {r['ready']}/1 | {r['mbps']} | "
            f"{r['lost']} {('— ' + r['note']) if r['note'] else ''} |")
    out()
    return {"a": a, "b": b}


# ── main ─────────────────────────────────────────────────────────────────────

def selftest() -> None:
    """Verify interpreter + deps + DB decrypt without running the load phases."""
    print(f"interpreter: {sys.executable}")
    import cryptography  # noqa: F401
    print("cryptography: OK")
    nvr = load_nvr()
    print(f"NVR: {nvr['id']} user={nvr['user']} pw_len={len(nvr['password'])}")
    print(f"URL: {mask(url_for(nvr, 1, 0))}")
    print(f"mediamtx.exe: {'OK' if MTX.exists() else 'MISSING'}")
    print("selftest OK — ready to run (turn VPN OFF, then run without --selftest)")


def main() -> None:
    if "--selftest" in sys.argv:
        selftest()
        return
    WORK.mkdir(exist_ok=True)
    run_label = os.environ.get("NETCHECK_LABEL", "").strip()
    out(f"# DSS netcheck — {datetime.now().isoformat(timespec='seconds')}"
        f"{(' — run=' + run_label) if run_label else ''}")
    if run_label:
        out(f"_Contention run label: **{run_label}** (compare peak vs night, §3.6)._")
    out()
    if not MTX.exists():
        out(f"FATAL: mediamtx.exe not found at {MTX}")
        RESULT.write_text("\n".join(REPORT) + "\n", encoding="utf-8")
        return

    nvr = load_nvr()
    out(f"NVR: {nvr['id']} {nvr['ip']}:{nvr['port']} vendor={nvr['vendor']}")
    out(f"Sample URL (main ch1): `{mask(url_for(nvr, 1, 0))}`")
    out()

    # ── network boundary ──
    out("## Network path (VPN should be OFF)")
    global IFACE_IDX
    IFACE_IDX = find_iface_index(NVR_IP)
    egress = ps(f"$r=Find-NetRoute -RemoteIPAddress {NVR_IP} -ErrorAction SilentlyContinue | "
                f"Select-Object -First 1; \"ifIndex=$($r.InterfaceIndex) srcIP=$($r.IPAddress)\"")
    out(f"- egress to NVR: {egress}")
    # On-link wired = the egress source IP shares the NVR's /24. Otherwise traffic
    # leaves via a gateway (Wi-Fi/VPN) and the link reading below is NOT the camera LAN.
    nvr_net = NVR_IP.rsplit(".", 1)[0] + "."
    if f"srcIP={nvr_net}" not in egress:
        out(f"- **WARNING: egress source IP is NOT in the NVR subnet ({nvr_net}x)** → "
            "traffic is routed via a gateway (Wi-Fi/VPN?), not the wired camera LAN. "
            "Set a working secondary IP on Ethernet (and/or disable Wi-Fi) so the test "
            "runs over the wired path — otherwise link/loss readings are invalid.")
    link = ps(f"(Get-NetAdapter {_adapter_sel()}).LinkSpeed")
    out(f"- egress link speed: {link}")
    pinginfo = ps(
        f"$r=Test-Connection {NVR_IP} -Count 4 -ErrorAction SilentlyContinue; "
        f"$avg=($r|Measure-Object ResponseTime -Average).Average; "
        f"\"recv=$($r.Count)/4 avg=$([int]$avg)ms\""
    )
    out(f"- ping: {pinginfo}")
    srcs = ps(
        "Get-NetTCPConnection -RemotePort 554 -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty LocalAddress -Unique"
    )
    out(f"- existing :554 conn source IPs: {srcs or '(none)'}")
    out()

    # ── physical link + decisive camera-vs-NVR test ──
    link_diag()
    ab = run_ab(nvr)

    # ── load phases ──
    out("## Load phases (isolated MediaMTX, sourceOnDemand off, TCP)")
    out()
    out("| phase | streams | ready | aggregate Mbps | Ethernet RX Mbps | RTP packets lost |")
    out("|---|---|---|---|---|---|")
    results = []
    for label, subtype, chans in PHASES:
        r = run_phase(label, subtype, chans, nvr)
        results.append(r)
        out(f"| {r['label']} | {r['streams']} | {r['ready']}/{r['streams']} | "
            f"{r['mbps']} | {r['eth_mbps']} | {r['lost']} {('— ' + r['note']) if r['note'] else ''} |")

    # ── interpretation hints (for the assistant to read later) ──
    out()
    out("## Read-me (interpretation)")

    # §3.1 decisive verdict (Variant A vs B fork)
    if ab and ab["a"]["lost"] is not None and ab["b"]["lost"] is not None:
        la, lb = ab["a"]["lost"], ab["b"]["lost"]
        if lb == 0 and la > 0:
            out(f"- **§3.1: direct-camera CLEAN ({lb}) but via-NVR lost {la}** → NVR RELAY "
                "is the bottleneck → Variant A (pull cameras directly) would fix it.")
        elif la > 0 and lb > 0:
            out(f"- **§3.1: BOTH lose** (NVR {la}, camera {lb}) → camera/codec/network, NVR "
                "is innocent → Variant B: tune camera codec/GOP/bitrate, not architecture.")
        elif la == 0 and lb == 0:
            out("- **§3.1: both clean** → loss not reproduced on a single stream → scale up "
                "(§3.2) and/or check contention at peak hours (§3.6).")

    main_phases = [r for r in results if "main" in r["label"]]
    worst = max((r for r in main_phases if r["lost"] is not None),
                key=lambda r: r["lost"], default=None)
    if worst and worst["lost"] and worst["lost"] > 0 and worst["eth_mbps"] is not None:
        if worst["eth_mbps"] >= 85:
            out(f"- Loss appears with Ethernet RX ≈ {worst['eth_mbps']} Mbps (near the "
                f"100 Mbps cap) → **LINK SATURATION**. Gigabit NIC would help.")
        else:
            out(f"- Loss appears while Ethernet RX is only ≈ {worst['eth_mbps']} Mbps "
                f"(well under 100) → **NVR OUTPUT LIMIT**. Gigabit won't help; reduce "
                f"concurrent streams / lower stream bitrate / raise NVR remote-bandwidth.")
    elif sum(r["ready"] for r in results) == 0:
        out("- **INVALID RUN: 0 sources connected** — MediaMTX never reached the NVR, so "
            "'0 packets lost' is meaningless (no data flowed). See the egress WARNING above: "
            "the wired path to the NVR subnet is down. Fix reachability (working secondary IP "
            "on Ethernet; `ping 192.168.20.58` must succeed) and re-run.")
    elif all((r["lost"] == 0) for r in main_phases if r["lost"] is not None):
        out("- No RTP loss in any phase → the loss is NOT reproduced without the VPN. "
            "Strong evidence the VPN/userspace path was the cause after all.")
    one = next((r for r in main_phases if r["streams"] == 1), None)
    if one and one["lost"]:
        out(f"- Even a SINGLE main stream lost {one['lost']} packets → per-stream NVR/"
            "channel issue (bitrate/codec), not aggregate capacity.")
    out()
    out("_Tip: compare 'aggregate Mbps' growth vs where 'RTP packets lost' first jumps._")

    RESULT.write_text("\n".join(REPORT) + "\n", encoding="utf-8")
    print(f"\nReport written to {RESULT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        REPORT.append(f"\nFATAL: {type(e).__name__}: {e}")
        RESULT.write_text("\n".join(REPORT) + "\n", encoding="utf-8")
        raise
