#!/usr/bin/env python3
"""Kanagatly VMS — vendor-side License Manager panel.

A LOCAL (localhost-only) web panel the VENDOR runs on their OWN machine to
CREATE and MONITOR licensed VMS clients, replacing the hand-run
``backend/tools/issue_license.py`` CLI. It holds the Ed25519 PRIVATE signing
key and NEVER exposes it over the API. It is NOT shipped to customers.

Run it:
    python license-manager/app.py
    # -> serves http://127.0.0.1:7070

It reuses the app's crypto so panel-minted licenses verify byte-identically in
the VMS: we add ``backend/`` to sys.path and ``from app import licensing``.
Every mint is sanity-verified with ``licensing.verify_license`` before storing,
exactly like ``issue_license.py`` does.

Storage: SQLite at license-manager/data/clients.db (gitignored).
Keys:    license-manager/vendor-keys/{private,public}_key.pem (gitignored),
         auto-generated on first run if missing.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ── Reuse the VMS app's licensing crypto ─────────────────────────────────────
# license-manager/app.py -> license-manager -> repo root -> backend
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app import licensing  # noqa: E402  (import after sys.path tweak, by design)

# ── Paths ────────────────────────────────────────────────────────────────────
DATA_DIR = _HERE / "data"
DB_PATH = DATA_DIR / "clients.db"
KEYS_DIR = _HERE / "vendor-keys"
PRIVATE_KEY_PATH = KEYS_DIR / "private_key.pem"
PUBLIC_KEY_PATH = KEYS_DIR / "public_key.pem"
STATIC_DIR = _HERE / "static"

HOST = "127.0.0.1"  # localhost ONLY — this process holds the private key.
PORT = 7070


# ── Keypair management ───────────────────────────────────────────────────────
def ensure_keypair() -> None:
    """Generate + persist a vendor keypair on first run if it's missing."""
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    if PRIVATE_KEY_PATH.exists() and PUBLIC_KEY_PATH.exists():
        return
    priv, pub = licensing.generate_keypair()
    PRIVATE_KEY_PATH.write_bytes(licensing.private_key_pem(priv))
    try:
        PRIVATE_KEY_PATH.chmod(0o600)
    except OSError:
        pass
    PUBLIC_KEY_PATH.write_bytes(licensing.public_key_pem(pub))
    print(f"[license-manager] generated a new vendor keypair in {KEYS_DIR}")
    print("[license-manager] KEEP private_key.pem SAFE — never commit or share it.")


def load_private_key() -> "licensing.Ed25519PrivateKey":
    return licensing.load_private_key(PRIVATE_KEY_PATH.read_bytes())


def public_key_pem_text() -> str:
    return PUBLIC_KEY_PATH.read_text(encoding="utf-8")


# ── SQLite registry ──────────────────────────────────────────────────────────
def db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with closing(db()) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,
                company       TEXT NOT NULL DEFAULT '',
                hardware_id   TEXT NOT NULL,
                issued        TEXT NOT NULL,
                expires       TEXT,               -- NULL = perpetual
                features      TEXT NOT NULL DEFAULT '[]',   -- JSON array
                max_cameras   INTEGER NOT NULL DEFAULT 0,
                max_nvrs      INTEGER NOT NULL DEFAULT 0,
                notes         TEXT NOT NULL DEFAULT '',
                license_json  TEXT NOT NULL DEFAULT '',      -- last minted blob
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            )
            """
        )


# ── Status computation ───────────────────────────────────────────────────────
def _today() -> date:
    return datetime.now(timezone.utc).date()


def compute_status(expires: str | None) -> tuple[str, int | None]:
    """Return (status, days_left). status ∈ perpetual|active|expiring-soon|expired."""
    if expires in (None, "", "null"):
        return "perpetual", None
    try:
        exp = date.fromisoformat(str(expires))
    except (ValueError, TypeError):
        return "expired", None
    days_left = (exp - _today()).days
    if days_left < 0:
        return "expired", days_left
    if days_left < 30:
        return "expiring-soon", days_left
    return "active", days_left


def row_to_client(row: sqlite3.Row) -> dict[str, Any]:
    status, days_left = compute_status(row["expires"])
    try:
        features = json.loads(row["features"])
    except (json.JSONDecodeError, TypeError):
        features = []
    return {
        "id": row["id"],
        "name": row["name"],
        "company": row["company"],
        "hardware_id": row["hardware_id"],
        "hardware_id_short": (row["hardware_id"] or "")[:12],
        "issued": row["issued"],
        "expires": row["expires"],
        "features": features,
        "max_cameras": row["max_cameras"],
        "max_nvrs": row["max_nvrs"],
        "notes": row["notes"],
        "status": status,
        "days_left": days_left,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# ── Minting ──────────────────────────────────────────────────────────────────
def mint_license(
    *,
    customer: str,
    hardware_id: str,
    issued: str,
    expires: str | None,
    features: list[str],
    max_cameras: int,
    max_nvrs: int,
    site_id: str = "",
) -> str:
    """Sign a license and SANITY-VERIFY it against our public key before returning."""
    priv = load_private_key()
    fields = {
        "customer": customer,
        "site_id": site_id,
        "issued": issued,
        "expires": expires if expires not in ("", "null") else None,
        "features": list(features),
        "max_cameras": int(max_cameras),
        "max_nvrs": int(max_nvrs),
        "hardware_id": hardware_id,
    }
    signed = licensing.sign_license(fields, priv)
    blob = json.dumps(signed, indent=2, sort_keys=True)

    # Mirror issue_license.py: verify what we just signed, binding to the fingerprint.
    status = licensing.verify_license(
        blob,
        pubkey=licensing.public_key_pem(priv.public_key()),
        fingerprint=hardware_id,
    )
    if not status.valid:
        raise HTTPException(status_code=500, detail=f"self-verification failed: {status.reason}")
    return blob


# ── API models ───────────────────────────────────────────────────────────────
class ClientIn(BaseModel):
    name: str = Field(min_length=1)
    company: str = ""
    hardware_id: str = Field(min_length=1)
    expires: str | None = None  # ISO date; None/"" = perpetual
    features: list[str] = Field(default_factory=list)
    max_cameras: int = 0
    max_nvrs: int = 0
    notes: str = ""


class ClientUpdate(BaseModel):
    name: str | None = None
    company: str | None = None
    expires: str | None = None  # ISO date; "" -> perpetual
    perpetual: bool = False
    features: list[str] | None = None
    max_cameras: int | None = None
    max_nvrs: int | None = None
    notes: str | None = None


# ── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="Kanagatly License Manager", docs_url=None, redoc_url=None)


@app.get("/api/pubkey", response_class=PlainTextResponse)
def get_pubkey() -> str:
    return public_key_pem_text()


@app.get("/api/clients")
def list_clients() -> JSONResponse:
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM clients ORDER BY created_at DESC").fetchall()
    return JSONResponse([row_to_client(r) for r in rows])


@app.get("/api/clients/{client_id}")
def get_client(client_id: int) -> JSONResponse:
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "client not found")
    out = row_to_client(row)
    out["license_json"] = row["license_json"]
    return JSONResponse(out)


@app.post("/api/clients")
def create_client(body: ClientIn) -> JSONResponse:
    issued = _today().isoformat()
    expires = body.expires if body.expires not in ("", "null") else None
    blob = mint_license(
        customer=body.company or body.name,
        hardware_id=body.hardware_id.strip(),
        issued=issued,
        expires=expires,
        features=body.features,
        max_cameras=body.max_cameras,
        max_nvrs=body.max_nvrs,
    )
    now = datetime.now(timezone.utc).isoformat()
    with closing(db()) as conn, conn:
        cur = conn.execute(
            """INSERT INTO clients
               (name, company, hardware_id, issued, expires, features,
                max_cameras, max_nvrs, notes, license_json, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                body.name,
                body.company,
                body.hardware_id.strip(),
                issued,
                expires,
                json.dumps(body.features),
                body.max_cameras,
                body.max_nvrs,
                body.notes,
                blob,
                now,
                now,
            ),
        )
        client_id = cur.lastrowid
        row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    out = row_to_client(row)
    out["license_json"] = blob
    return JSONResponse(out, status_code=201)


@app.put("/api/clients/{client_id}")
def update_client(client_id: int, body: ClientUpdate) -> JSONResponse:
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "client not found")

    name = body.name if body.name is not None else row["name"]
    company = body.company if body.company is not None else row["company"]
    notes = body.notes if body.notes is not None else row["notes"]
    max_cameras = body.max_cameras if body.max_cameras is not None else row["max_cameras"]
    max_nvrs = body.max_nvrs if body.max_nvrs is not None else row["max_nvrs"]
    if body.features is not None:
        features = body.features
    else:
        try:
            features = json.loads(row["features"])
        except (json.JSONDecodeError, TypeError):
            features = []

    if body.perpetual:
        expires: str | None = None
    elif body.expires is not None:
        expires = body.expires if body.expires not in ("", "null") else None
    else:
        expires = row["expires"]

    # RE-MINT with the (possibly) new expiry / features. issued stays the original.
    blob = mint_license(
        customer=company or name,
        hardware_id=row["hardware_id"],
        issued=row["issued"],
        expires=expires,
        features=features,
        max_cameras=max_cameras,
        max_nvrs=max_nvrs,
    )
    now = datetime.now(timezone.utc).isoformat()
    with closing(db()) as conn, conn:
        conn.execute(
            """UPDATE clients SET name=?, company=?, expires=?, features=?,
               max_cameras=?, max_nvrs=?, notes=?, license_json=?, updated_at=?
               WHERE id=?""",
            (
                name,
                company,
                expires,
                json.dumps(features),
                max_cameras,
                max_nvrs,
                notes,
                blob,
                now,
                client_id,
            ),
        )
        row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    out = row_to_client(row)
    out["license_json"] = blob
    return JSONResponse(out)


@app.delete("/api/clients/{client_id}")
def delete_client(client_id: int) -> JSONResponse:
    with closing(db()) as conn, conn:
        cur = conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "client not found")
    return JSONResponse({"deleted": client_id})


@app.get("/api/clients/{client_id}/license.lic")
def download_license(client_id: int) -> Response:
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if row is None or not row["license_json"]:
        raise HTTPException(404, "no license for this client")
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in (row["name"] or "license"))
    filename = f"{safe or 'license'}.lic"
    return Response(
        content=row["license_json"] + "\n",
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Static UI (mounted last so /api/* wins).
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def main() -> None:
    ensure_keypair()
    init_db()
    import uvicorn

    url = f"http://{HOST}:{PORT}"
    print(f"[license-manager] Kanagatly License Manager running at {url}")
    print("[license-manager] localhost only — this process holds the vendor PRIVATE key.")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
