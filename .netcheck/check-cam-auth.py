"""Check whether the NVR's RTSP credentials also work on the camera (HTTP digest)."""
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

CAM_IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.23.11"

con = sqlite3.connect(str(BACKEND / "dss.db"))
row = con.execute(
    "SELECT rtsp_username, rtsp_password_encrypted FROM nvrs WHERE ip = '192.168.20.58'"
).fetchone()
con.close()

from app.crypto import decrypt_password  # noqa: E402
import httpx  # noqa: E402

user, pw = row[0], decrypt_password(row[1])
url = f"http://{CAM_IP}/cgi-bin/magicBox.cgi?action=getDeviceType"
try:
    r = httpx.get(url, auth=httpx.DigestAuth(user, pw), timeout=8)
    print(f"HTTP {r.status_code}: {r.text.strip()[:120]}")
    print("CREDS OK" if r.status_code == 200 else "CREDS MISMATCH (or non-Dahua endpoint)")
except Exception as e:
    print(f"ERROR: {e}")
