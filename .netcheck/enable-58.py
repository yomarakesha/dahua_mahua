"""Re-enable NVR .58 and all its cameras in the DB for offline path-gen
validation. The source watchdog (only runs inside the live backend) disabled
them because cameras are unreachable right now; with no backend running this
flip sticks. Harmless: reconcile only writes MediaMTX *config*, no traffic."""
import sqlite3

con = sqlite3.connect("backend/dss.db")
con.execute("UPDATE nvrs SET enabled=1 WHERE id='nvr-192-168-20-58'")
con.execute("UPDATE cameras SET enabled=1 WHERE nvr_id='nvr-192-168-20-58'")
con.commit()
print("nvrs:", list(con.execute("SELECT id, enabled FROM nvrs")))
print("cams .58 enabled:", list(con.execute(
    "SELECT enabled, COUNT(*) FROM cameras WHERE nvr_id='nvr-192-168-20-58' GROUP BY enabled"
)))
