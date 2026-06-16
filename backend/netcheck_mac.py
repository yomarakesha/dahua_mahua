#!/usr/bin/env python3
"""macOS port of netcheck.py — measure NVR-relay vs direct-camera RTP loss.

Same idea as netcheck.py (which is Windows/PowerShell-only): launch an ISOLATED
MediaMTX (its own ports, never touching the live DSS instance) that pulls the
streams with sourceOnDemand OFF, then read per-path throughput from the API and
count "RTP packets lost" from MediaMTX's own log. Over TCP, that loss count is
*source-side sequence gaps* — i.e. the NVR/camera skipping frames — so it stays
meaningful even over a jittery viewing path (TCP redelivers in order; slowness
is not loss).

The §3.1 DECISIVE comparison: the SAME camera, pulled two ways —
  A) via the NVR relay  (rtsp://<nvr_ip>/...channel=<ch>&subtype=0)
  B) direct from camera (rtsp://<cam_ip>/...channel=1&subtype=0)
If A loses packets and B is clean, the NVR relay is the bottleneck → Variant A
(pull cameras directly) fixes it.

Stdlib only — no app imports, no venv needed. Credentials come from env/args in
plaintext (this is a diagnostic you run by hand), so it never touches the DB.

USAGE
-----
  NVR_PASS='post2525...' python3 netcheck_mac.py \
      --nvr-ip 192.168.20.15 --cam-ip 192.168.20.101 --user admin --ch 1

  # or all via env:
  NETCHECK_NVR_IP=192.168.20.15 NETCHECK_CAM_IP=192.168.20.101 \
  NETCHECK_NVR_USER=admin NETCHECK_NVR_PASS='post2525...' python3 netcheck_mac.py

  python3 netcheck_mac.py --selftest      # validate setup, no NVR traffic
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent          # repo root
MTX = ROOT / "mediamtx"                                  # macOS/Linux binary
WORK = ROOT / ".netcheck"
RESULT = ROOT / "netcheck-mac-result.md"
API = "127.0.0.1:9998"          # isolated — does NOT touch the DSS instance (9997)
RTSP_PORT = 8555                # isolated — does NOT touch DSS RTSP (8554)
WARMUP = 6                      # let sources connect before counting
MEASURE = int(os.environ.get("NETCHECK_MEASURE", "20"))

REPORT: list[str] = []


def out(line: str = "") -> None:
    print(line, flush=True)
    REPORT.append(line)


def mask(url: str) -> str:
    return re.sub(r"//([^:]+):[^@]+@", r"//\1:***@", url)


def build_rtsp_url(ip, port, channel, *, vendor, subtype, username, password) -> str:
    """Mirror of app.services.rtsp_probe.build_rtsp_url (inlined to stay stdlib-only)."""
    v = (vendor or "dahua").lower()
    if v == "hikvision":
        stream = 1 if subtype == 0 else 2
        path = f"/Streaming/Channels/{channel * 100 + stream}"
    else:
        path = f"/cam/realmonitor?channel={channel}&subtype={subtype}"
    auth = f"{quote(username, safe='')}:{quote(password, safe='')}@" if username and password else ""
    return f"rtsp://{auth}{ip}:{port}{path}"


# ── network preflight (macOS) ────────────────────────────────────────────────

def egress_iface(ip: str) -> str | None:
    try:
        r = subprocess.run(["route", "-n", "get", ip], capture_output=True, text=True, timeout=5)
        m = re.search(r"interface:\s*(\S+)", r.stdout)
        return m.group(1) if m else None
    except Exception:  # noqa: BLE001
        return None


def ping_stats(ip: str, count: int = 20) -> str:
    """Return a short 'loss=.. avg=..ms jitter(stddev)=..ms' summary."""
    try:
        r = subprocess.run(["ping", "-c", str(count), "-i", "0.2", ip],
                           capture_output=True, text=True, timeout=count + 10)
        loss = re.search(r"([\d.]+)% packet loss", r.stdout)
        rtt = re.search(r"= [\d.]+/([\d.]+)/[\d.]+/([\d.]+) ms", r.stdout)
        loss_s = loss.group(1) if loss else "?"
        avg = rtt.group(1) if rtt else "?"
        jit = rtt.group(2) if rtt else "?"
        return f"loss={loss_s}% avg={avg}ms jitter={jit}ms"
    except Exception as e:  # noqa: BLE001
        return f"<ping error: {e}>"


def eth_rx_bytes(iface: str | None) -> int | None:
    """Cumulative received bytes for an interface, via `netstat -ib` (Ibytes col)."""
    if not iface:
        return None
    try:
        r = subprocess.run(["netstat", "-ibn"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            f = line.split()
            # Link# row carries the cumulative counters; columns:
            # Name Mtu Network Address Ipkts Ierrs Ibytes Opkts Oerrs Obytes Coll
            if len(f) >= 7 and f[0] == iface and f[2].startswith("<Link"):
                return int(f[6])
    except Exception:  # noqa: BLE001
        return None
    return None


# ── isolated MediaMTX driver ─────────────────────────────────────────────────

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
        "webrtc: no", "hls: no", "rtmp: no", "srt: no", "metrics: no", "playback: no",
        "paths:",
    ]
    for name, src in paths.items():
        lines += [f"  {name}:", f'    source: "{src}"',
                  "    sourceOnDemand: no", "    rtspTransport: tcp"]
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")


def api_items() -> list[dict]:
    with urllib.request.urlopen(f"http://{API}/v3/paths/list?itemsPerPage=500", timeout=5) as r:
        return json.load(r).get("items", [])


def wait_api(deadline: float) -> bool:
    while time.time() < deadline:
        try:
            api_items()
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


def _slug(label: str) -> str:
    return re.sub(r"\W+", "_", label).strip("_") or "phase"


def measure(label: str, paths: dict[str, str], iface: str | None) -> dict:
    WORK.mkdir(exist_ok=True)
    cfg = WORK / f"mac_cfg_{_slug(label)}.yml"
    logf = WORK / f"mac_log_{_slug(label)}.log"
    logf.write_text("", encoding="utf-8")
    write_cfg(paths, logf, cfg)

    proc = subprocess.Popen([str(MTX), str(cfg)], cwd=str(ROOT),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    res = {"label": label, "ready": 0, "want": len(paths),
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
        e0, t0 = eth_rx_bytes(iface), time.time()
        time.sleep(MEASURE)
        items = api_items()
        e1, t1 = eth_rx_bytes(iface), time.time()
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


def cfg_from_args() -> dict:
    p = argparse.ArgumentParser(description="macOS NVR relay-vs-direct RTP-loss check.")
    p.add_argument("--nvr-ip", default=os.environ.get("NETCHECK_NVR_IP"))
    p.add_argument("--cam-ip", default=os.environ.get("NETCHECK_CAM_IP"))
    p.add_argument("--user", default=os.environ.get("NETCHECK_NVR_USER", "admin"))
    p.add_argument("--password", default=os.environ.get("NETCHECK_NVR_PASS"))
    p.add_argument("--ch", type=int, default=int(os.environ.get("NETCHECK_CAM_CH", "1")))
    p.add_argument("--nvr-port", type=int, default=int(os.environ.get("NETCHECK_NVR_PORT", "554")))
    p.add_argument("--cam-port", type=int, default=int(os.environ.get("NETCHECK_CAM_PORT", "554")))
    p.add_argument("--vendor", default=os.environ.get("NETCHECK_NVR_VENDOR", "dahua"))
    p.add_argument("--label", default=os.environ.get("NETCHECK_LABEL", "mac-run"))
    p.add_argument("--selftest", action="store_true")
    return vars(p.parse_args())


def main() -> None:
    a = cfg_from_args()
    if not MTX.exists():
        raise SystemExit(f"mediamtx binary not found at {MTX} (need the macOS/Linux build)")
    if not a["nvr_ip"] or not a["password"]:
        raise SystemExit("need --nvr-ip and --password (or NETCHECK_NVR_IP / NETCHECK_NVR_PASS)")

    ch = a["ch"]
    url_a = build_rtsp_url(a["nvr_ip"], a["nvr_port"], ch,
                           vendor=a["vendor"], subtype=0, username=a["user"], password=a["password"])
    url_b = None
    if a["cam_ip"]:
        url_b = build_rtsp_url(a["cam_ip"], a["cam_port"], 1,
                               vendor=a["vendor"], subtype=0, username=a["user"], password=a["password"])

    iface = egress_iface(a["nvr_ip"])

    if a["selftest"]:
        print(f"mediamtx:   {MTX}  (exists={MTX.exists()})")
        print(f"egress iface to NVR: {iface}")
        print(f"NVR  A url: {mask(url_a)}")
        print(f"CAM  B url: {mask(url_b) if url_b else '(no --cam-ip → relay-only)'}")
        print(f"user={a['user']}  pw_len={len(a['password'])}  (incl. any dots)")
        print(f"ping NVR {a['nvr_ip']}: {ping_stats(a['nvr_ip'], 8)}")
        if a["cam_ip"]:
            print(f"ping CAM {a['cam_ip']}: {ping_stats(a['cam_ip'], 8)}")
        print("selftest OK — re-run without --selftest to measure (VPN OFF).")
        return

    out(f"# DSS netcheck (macOS) — label={a['label']}")
    out(f"NVR: {a['nvr_ip']}:{a['nvr_port']} vendor={a['vendor']} egress_iface={iface}")
    out("")
    out("## Network path")
    out(f"- ping NVR {a['nvr_ip']}: {ping_stats(a['nvr_ip'])}")
    if a["cam_ip"]:
        cam_ping = ping_stats(a["cam_ip"])
        out(f"- ping CAM {a['cam_ip']}: {cam_ping}")
        jit = re.search(r"jitter=([\d.]+)ms", cam_ping)
        if jit and float(jit.group(1)) > 10:
            out(f"  - ⚠️ camera path jitter {jit.group(1)}ms is high — RTP-loss below is still "
                f"valid (sequence gaps = source drops), but throughput/Mbps may be understated "
                f"from this vantage. For Mbps trust the server-side run.")
    out("")
    out("## §3.1 DECISIVE — same main stream: via NVR vs direct from camera")
    out(f"- A via NVR (ch{ch}): `{mask(url_a)}`")
    if url_b:
        out(f"- B direct camera:    `{mask(url_b)}`")
    out("")

    a_res = measure(f"A via-NVR ch{ch}", {"ab_nvr": url_a}, iface)
    b_res = measure("B direct-cam", {"ab_cam": url_b}, iface) if url_b else None

    out("| path | ready | Mbps | RTP packets lost |")
    out("|---|---|---|---|")

    def row(r: dict, name: str) -> None:
        rd = f"{r['ready']}/{r['want']}"
        out(f"| {name} | {rd} | {r['mbps'] if r['mbps'] is not None else '?'} "
            f"| {r['lost'] if r['lost'] is not None else '?'} {('— ' + r['note']) if r['note'] else ''}|")

    row(a_res, f"A via-NVR ch{ch}")
    if b_res:
        row(b_res, "B direct-cam")
    out("")

    # verdict
    if b_res and a_res["lost"] is not None and b_res["lost"] is not None:
        if a_res["lost"] > 0 and b_res["lost"] == 0:
            out(f"**Verdict:** NVR relay lost {a_res['lost']} pkts, direct camera lost 0 → "
                f"the NVR relay is the bottleneck. Variant A (pull this camera direct) fixes it.")
        elif a_res["lost"] == 0 and b_res["lost"] == 0:
            out("**Verdict:** both clean (0 lost) over this window — this channel's relay is fine. "
                "Re-run on a known-bad NVR (e.g. the one from the original netcheck) to compare.")
        else:
            out(f"**Verdict:** relay lost {a_res['lost']}, direct lost {b_res['lost']} — "
                f"not the clean split; inspect MediaMTX logs in {WORK}.")

    RESULT.write_text("\n".join(REPORT) + "\n", encoding="utf-8")
    out("")
    out(f"_Saved to {RESULT}_")


if __name__ == "__main__":
    main()
