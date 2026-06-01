# DSS — Centralised Video Management System

VMS that fans out a single RTSP pull from each NVR channel to many operator
browsers. Solves the Dahua NVR connection-cap problem: regardless of how
many operators are watching, each channel keeps **one** session to the NVR.

```
Operators (browser, WebRTC/WHEP + HLS fallback)
        │
        ▼
   nginx (or python -m http.server for local dev)
        │── /api/*  ─▶  backend  — FastAPI + Postgres
        │                    │
        │                    ▼  (HTTP control on :9997)
        │              mediamtx ── one RTSP pull per active channel ──▶ NVRs
        │
        └── WHEP / HLS  ─▶  mediamtx (:8889 / :8888, fans out to N viewers)
```

Why this works: every MediaMTX path is `sourceOnDemand: yes`. The RTSP
session to the NVR is opened only when the first viewer subscribes and torn
down `sourceOnDemandCloseAfter` after the last viewer leaves. Idle channels
consume zero NVR slots.

## Layout

```
backend/        FastAPI + async SQLAlchemy + Alembic (auth, RBAC, inventory, MediaMTX manager)
web/            Vanilla-JS frontend (login + grid + WebRTC player)
mediamtx.yml    MediaMTX baseline (paths managed dynamically by backend)
mediamtx.exe    MediaMTX binary (Windows)
mediamtx        MediaMTX binary (Linux/macOS)
nvr_inventory.json   Source of truth for the initial seed
docker-compose.yml + Dockerfiles + nginx config
start.ps1       One-shot launcher for local dev (no Docker)
.env.example    Secrets template
```

See `backend/README.md` for the API surface.

## Run locally (no Docker)

Requires Python 3.12 and PostgreSQL 14+ on the host.

```powershell
# 1. Create DB once (PG must be installed):
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -c "CREATE USER dss WITH PASSWORD 'dss';"
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -c "CREATE DATABASE dss OWNER dss;"

# 2. Launch everything:
.\start.ps1
```

`start.ps1` will:
- Create `backend/.venv` and install Python deps on first run.
- Generate `backend/.env` with random `JWT_SECRET` + `NVR_SECRET_KEY` on first run.
- Run `alembic upgrade head` and seed from `nvr_inventory.json` on first run.
- Open three PowerShell windows: MediaMTX, FastAPI backend, static frontend.

Then open <http://localhost:8080>. First login: `admin` / `admin` — you'll
be asked to set a new password immediately.

## Run with docker-compose

```bash
cp .env.example .env   # then edit JWT_SECRET, NVR_SECRET_KEY
docker compose up -d --build
docker compose exec backend python -m app.seed --region-slug central
```

Operator URL: `http://<host>/`.

## Verifying the fan-out

Open the same camera in two browser tabs. Then:

```powershell
curl http://localhost:9997/v3/paths/list | py -m json.tool
```

Look at the path entry: `readers` will be `2`, but `sourceReady` is `true`
with a single source — one connection to the NVR, two operator viewers.
Close both tabs, wait `sourceOnDemandCloseAfter` (30s sub / 60s main),
`sourceReady` flips to `false` and MediaMTX releases the NVR session.

## Adding Hikvision NVRs

The data model has a `Vendor` enum (`dahua` / `hikvision`); the RTSP URL
builder emits the Hikvision path form
(`/Streaming/Channels/{channel*100 + stream}`). Create the NVR via
`POST /api/v1/nvrs` with `"vendor": "hikvision"`.
