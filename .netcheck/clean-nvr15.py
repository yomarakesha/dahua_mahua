"""One-off cleanup for nvr-192-168-20-15 on this site.

The dev dss.db predates the uq_camera_nvr_channel constraint, so repeated
imports left duplicate rows per channel plus stale cross-site IPs (192.168.23.x
from the old site). The cameras here sit behind the NVR PoE and are NOT
directly reachable, so Variant A (main pulled from the camera IP) is wrong for
this NVR: the dead _main streams make the watchdog disable the whole NVR.

Fix (DSS data only; nothing on the NVR/cameras is touched):
  * keep one camera row per REAL channel (a channel that the NVR's RemoteDevice
    import gave a 192.168.20.x IP),
  * clear its IP -> main falls back to the reachable NVR relay,
  * drop duplicate rows and phantom/stale channels,
  * re-enable the NVR.
"""
import shutil
import sqlite3
import sys
from pathlib import Path

DB = Path(r"C:\Users\yomarakesha\Desktop\projects\dss\backend\dss.db")
NVR = "nvr-192-168-20-15"

# Belt-and-suspenders backup including WAL sidecar (backend is stopped).
for ext in ("", "-wal", "-shm"):
    p = Path(str(DB) + ext)
    if p.exists():
        shutil.copy2(p, str(p) + ".clean-bak")

con = sqlite3.connect(str(DB))
cur = con.cursor()

before = cur.execute("SELECT COUNT(*) FROM cameras WHERE nvr_id=?", (NVR,)).fetchone()[0]
real = sorted(
    r[0] for r in cur.execute(
        "SELECT DISTINCT channel FROM cameras WHERE nvr_id=? AND ip LIKE '192.168.20.%'",
        (NVR,),
    )
)
print(f"before rows: {before}")
print(f"real channels ({len(real)}): {real}")

if not real:
    print("ABORT: no 192.168.20.x cameras found — refusing to delete everything.")
    sys.exit(1)

for ch in real:
    keep = cur.execute(
        "SELECT id FROM cameras WHERE nvr_id=? AND channel=? AND ip LIKE '192.168.20.%' "
        "ORDER BY id LIMIT 1",
        (NVR, ch),
    ).fetchone()[0]
    cur.execute(
        "DELETE FROM cameras WHERE nvr_id=? AND channel=? AND id<>?", (NVR, ch, keep)
    )
    cur.execute("UPDATE cameras SET ip=NULL, enabled=1 WHERE id=?", (keep,))

qmarks = ",".join("?" * len(real))
cur.execute(
    f"DELETE FROM cameras WHERE nvr_id=? AND channel NOT IN ({qmarks})", (NVR, *real)
)
cur.execute("UPDATE nvrs SET enabled=1 WHERE id=?", (NVR,))
con.commit()

rows = cur.execute(
    "SELECT channel, ip, enabled FROM cameras WHERE nvr_id=? ORDER BY channel", (NVR,)
).fetchall()
print(f"after rows: {len(rows)}")
for ch, ip, en in rows:
    print(f"  ch{ch:<3} ip={ip!s:<16} enabled={en}")
con.close()
