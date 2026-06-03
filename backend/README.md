# DSS Backend

FastAPI service for the centralised VMS:

- JWT auth + RBAC (admin / operator, regions M2M)
- NVR/Camera/Region CRUD with at-rest encryption of RTSP passwords (Fernet)
- RTSP digest-auth probe + IP-level lockout tracking
- MediaMTX HTTP control client — dynamic path management (sub + main)
- Stream URL handout (`/streams/{camera_id}`) that never leaks NVR creds

## Layout

```
app/
  main.py              FastAPI entrypoint (lifespan + routers)
  settings.py          pydantic-settings configuration
  db.py / models.py    async SQLAlchemy
  security.py          Argon2 + JWT (HS256)
  crypto.py            Fernet wrapper for NVR password
  rate_limit.py        in-memory login throttler
  deps.py              FastAPI dependencies / RBAC guards
  routers/             one file per resource (auth, regions, users, nvrs, cameras, streams, events, mediamtx)
  services/            rtsp_probe, mediamtx_api, path_sync, lockouts, nvr_events, mediamtx_proc
alembic/               migrations
seed.py                idempotent importer for legacy nvr_inventory.json
```

## Run locally

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Postgres needs to be reachable at DATABASE_URL.
export JWT_SECRET=$(openssl rand -hex 48)
export NVR_SECRET_KEY=$(python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')

alembic upgrade head
python -m app.seed --inventory ../nvr_inventory.json --region-slug central
uvicorn app.main:app --reload --port 8000
```

OpenAPI docs at <http://localhost:8000/docs>.

## Run inside docker-compose

See the project-root `docker-compose.yml`. The backend container runs
`alembic upgrade head` on startup, then `uvicorn`.

## Key endpoints

```
POST /api/v1/auth/login                 — exchange username/password for JWT
POST /api/v1/auth/change-password
GET  /api/v1/auth/me

GET  /api/v1/cameras                    — list cameras visible to caller
GET  /api/v1/nvrs                       — list NVRs (RBAC-filtered, no creds)
POST /api/v1/nvrs                       — admin: register NVR + cameras
POST /api/v1/nvrs/{id}/test             — admin: RTSP digest probe
GET  /api/v1/nvrs/health                — TCP reachability for all visible NVRs

GET  /api/v1/streams/{camera_id}        — WHEP + HLS URLs for one camera
POST /api/v1/streams/{camera_id}/end    — best-effort session close (telemetry)

POST /api/v1/mediamtx/reconcile         — admin: push DB → MediaMTX path config
GET  /api/v1/mediamtx/health
```
