"""Query the Dahua NVR for its connected-camera list (channel -> camera IP).

Run from backend/ with the venv python. Read-only; password never printed.
"""
import re
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

NVR_IP = "192.168.20.58"

con = sqlite3.connect(str(BACKEND / "dss.db"))
row = con.execute(
    "SELECT rtsp_username, rtsp_password_encrypted FROM nvrs WHERE ip = ?",
    (NVR_IP,),
).fetchone()
con.close()
if not row:
    sys.exit(f"NVR {NVR_IP} not in dss.db")

from app.crypto import decrypt_password  # noqa: E402
user, pw = row[0], decrypt_password(row[1])

import httpx  # noqa: E402

url = f"http://{NVR_IP}/cgi-bin/configManager.cgi?action=getConfig&name=RemoteDevice"
r = httpx.get(url, auth=httpx.DigestAuth(user, pw), timeout=10)
print(f"HTTP {r.status_code}")
if r.status_code != 200:
    print(r.text[:500])
    sys.exit(1)

# Lines: table.RemoteDevice.uuid:System_CONFIG_NETCAMERA_INFO_<N>.<Key>[...]=val
dev: dict[int, dict] = {}
for line in r.text.splitlines():
    m = re.match(
        r"table\.RemoteDevice\.uuid:System_CONFIG_NETCAMERA_INFO_(\d+)"
        r"\.(\w+)(?:\[\d+\])?(?:\.(\w+))?=(.*)",
        line,
    )
    if not m:
        continue
    idx, key, sub, val = int(m.group(1)), m.group(2), m.group(3), m.group(4)
    d = dev.setdefault(idx, {})
    if key in ("Address", "Enable", "DeviceType", "UserName", "RtspPort"):
        d[key] = val
    if key == "VideoInputs" and sub == "Name":
        d.setdefault("ChName", val)

if not dev:
    print("-- no devices parsed; raw response head: --")
    print("\n".join(r.text.splitlines()[:80]))

print(f"{'slot':>4} {'ch':>3}  {'on':>5}  {'address':<16} {'model':<22} {'name'}")
subnets: dict[str, int] = {}
for idx in sorted(dev):
    d = dev[idx]
    addr = d.get("Address", "")
    if not addr:
        continue
    subnets[".".join(addr.split(".")[:3])] = subnets.get(".".join(addr.split(".")[:3]), 0) + 1
    print(f"{idx:>4} {idx + 1:>3}  {d.get('Enable','?'):>5}  {addr:<16} "
          f"{d.get('DeviceType',''):<22} {d.get('ChName','')}")

print("\nsubnets:")
for s, n in sorted(subnets.items()):
    print(f"  {s}.0/24  -> {n} cameras")
