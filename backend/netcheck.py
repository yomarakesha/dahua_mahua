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

MTX = ROOT / "mediamtx.exe"
RESULT = ROOT / "netcheck-result.md"
WORK = ROOT / ".netcheck"
NVR_IP = "192.168.20.58"
API = "127.0.0.1:9998"          # isolated — does NOT touch the DSS instance (9997)
RTSP_PORT = 8555
ETH = "Ethernet"

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


def eth_rx() -> int | None:
    v = ps(f"(Get-NetAdapterStatistics -Name '{ETH}').ReceivedBytes")
    try:
        return int(v)
    except ValueError:
        return None


def run_phase(label: str, subtype: int, chans: list[int], nvr: dict) -> dict:
    paths = {f"t_ch{ch}": url_for(nvr, ch, subtype) for ch in chans}
    cfg = WORK / f"cfg_{label.split()[0]}_{subtype}.yml"
    logf = WORK / f"log_{label.split()[0]}_{subtype}.log"
    logf.write_text("", encoding="utf-8")
    write_cfg(paths, logf, cfg)

    proc = subprocess.Popen(
        [str(MTX), str(cfg)], cwd=str(ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    res = {"label": label, "streams": len(chans), "ready": 0,
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
    out(f"# DSS netcheck — {datetime.now().isoformat(timespec='seconds')}")
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
    route = ps(f"Find-NetRoute -RemoteIPAddress {NVR_IP} | Select-Object -First 1 "
               f"InterfaceAlias,IPAddress | ConvertTo-Json -Compress")
    out(f"- route → `{route}`")
    link = ps(f"(Get-NetAdapter -Name '{ETH}').LinkSpeed")
    out(f"- Ethernet link speed: {link}")
    pinginfo = ps(
        f"$r=Test-Connection {NVR_IP} -Count 10 -ErrorAction SilentlyContinue; "
        f"$avg=($r|Measure-Object ResponseTime -Average).Average; "
        f"\"recv=$($r.Count)/10 avg=$([int]$avg)ms\""
    )
    out(f"- ping: {pinginfo}")
    srcs = ps(
        "Get-NetTCPConnection -RemotePort 554 -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty LocalAddress -Unique"
    )
    out(f"- existing :554 conn source IPs: {srcs or '(none)'}")
    out()

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
