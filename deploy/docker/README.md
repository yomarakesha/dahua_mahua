# Kanagatly VMS — offline Docker appliance

Run the whole VMS (backend + go2rtc + Caddy TLS + web UI) on a firm's own server
with **no internet at install**. Works on Linux and on Windows/macOS with Docker.

The stack:

| Service | What | Port |
|---|---|---|
| `caddy` | HTTPS ingress (self-signed) → serves the UI + proxies `/api` and `/go2rtc` | **8443** |
| `backend` | FastAPI + ffmpeg (playback/re-encode), SQLite by default | 8000 (internal) |
| `go2rtc` | live media relay (MSE + WebRTC) | **8556** (WebRTC), 1984 (internal) |
| `postgres` | optional (compose profile `postgres`) | — |

## 1. Build the bundle (once, on an ONLINE machine with Docker)

```bash
./deploy/docker/build-offline-bundle.sh
# → creates ./kanagatly-vms-offline/  (images tarball + compose + install scripts)
```

## 2. Copy the folder to the firm's server

Copy the whole `kanagatly-vms-offline/` folder over (USB, scp, etc.). The server
needs **only Docker** — no internet, no Python, no Node.

## 3. Install (on the server)

```bash
docker load -i kanagatly-vms-images.tar     # load the pre-built images
./install.sh          # Linux
#  or, on Windows PowerShell:
.\install.ps1
```

`install` will:
- generate the per-install secrets (`JWT_SECRET`, `NVR_SECRET_KEY`, admin password) if missing,
- **detect this server's LAN IP** and write `GO2RTC_WEBRTC_CANDIDATES=<ip>:8556`,
- `docker compose up -d`, wait for health, and **print the address to connect to**:

```
  → Open the desktop app and connect to:  https://<server-ip>:8443
```

The first admin login **forces a password change**, then the admin creates users
in the app. (Need to (re)create the admin manually? `docker compose exec backend
python -m app.create_admin`.)

## Connect the desktop app

Install the **Kanagatly VMS** desktop app, launch it, and enter the address the
installer printed (`https://<server-ip>:8443`). It auto-trusts the self-signed
cert. Everyone on the LAN uses the same address.

## Ports to open (firewall)

- **8443/tcp** — the UI/API (what the desktop app + browsers connect to).
- **8556/tcp + 8556/udp** — go2rtc WebRTC media (the smooth 4 MP main view).

## WebRTC note (important)

The smooth fullscreen main uses **WebRTC**, which must advertise the server's real
LAN IP. The installer sets that automatically (`GO2RTC_WEBRTC_CANDIDATES`).
- **Linux:** works out of the box (the compose uses host networking for go2rtc).
- **Windows/macOS Docker Desktop:** host networking is limited, so WebRTC may not
  connect — live simply **falls back to MSE** (still smooth-enough, works over
  the single `:8443` origin). Everything else (playback, grid, UI) is unaffected.
- Multi-NIC / VPN box: if auto-detect picks the wrong IP, set
  `GO2RTC_WEBRTC_CANDIDATES=<correct-lan-ip>:8556` in `.env` and
  `docker compose up -d`.

## Timezone

Playback times use `PLAYBACK_TZ_OFFSET_MINUTES` (minutes from UTC; e.g. `300` for
UTC+5). Set it in `.env` for the firm's timezone — the backend logs the effective
value at startup.

## Database

Default is **SQLite** in a Docker volume (`backend_data`) — nothing extra to run.
For Postgres: set `DATABASE_URL` in `.env` and start with
`docker compose --profile postgres up -d`.

## Licensing

Offline licensing is included. Print this box's fingerprint from the License
screen (or the logs), mint a `.lic` in your License Manager, and upload it in the
app. Enforcement stays **off** until you enable it (`LICENSE_ENFORCEMENT_ENABLED=true`).
