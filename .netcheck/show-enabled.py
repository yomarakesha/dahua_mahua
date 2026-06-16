import sqlite3

con = sqlite3.connect("backend/dss.db")
print("nvrs:", list(con.execute("SELECT id, enabled FROM nvrs")))
print("cameras by enabled:", list(con.execute(
    "SELECT nvr_id, enabled, COUNT(*) FROM cameras GROUP BY nvr_id, enabled"
)))
