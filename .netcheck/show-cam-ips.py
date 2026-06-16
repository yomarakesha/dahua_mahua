import sqlite3

con = sqlite3.connect("backend/dss.db")
rows = list(con.execute(
    "SELECT channel, ip FROM cameras WHERE nvr_id='nvr-192-168-20-58' ORDER BY channel"
))
print(len(rows), "cams")
for ch, ip in rows:
    print(f"ch{ch:02d} -> {ip}")
