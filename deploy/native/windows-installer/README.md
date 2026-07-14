# Kanagatly VMS Server — Windows installer (`.exe`)

A single **double-click installer** for the Kanagatly VMS **server**. The firm
needs **NOTHING preinstalled** — no Python, no Node, no Git, no manual
PowerShell. Everything the server needs is bundled inside the `.exe`.

Download `KanagatlyVMS-Server-Setup.exe` from the rolling GitHub Release
**`server-latest`**, copy it to the server box, and run it.

## What the installer does

1. **Bundles a self-contained runtime.** Inside the payload:
   - an **embeddable CPython 3.12** with every `backend/requirements.txt`
     dependency pre-installed into `python\Lib\site-packages` (relocatable — the
     runtime finds itself via a `._pth` file, not a hard-coded build path);
   - `bin\`: `go2rtc.exe` (v1.9.14), static `ffmpeg.exe` + `ffprobe.exe`
     (gyan.dev), `caddy.exe` (2.8.4), `nssm.exe` (2.24);
   - the pre-built web UI in `www\`;
   - the backend source (`backend\app`, `backend\alembic`, `alembic.ini`);
   - the runtime templates `Caddyfile` + `go2rtc.base.yaml`.
2. **Installs** to `C:\Program Files\Kanagatly VMS Server`.
3. Runs `postinstall.ps1` **elevated** (using the *bundled* `python.exe`, never a
   system Python), which:
   - detects the box's **LAN IP**;
   - generates `backend\.env` **secrets** on first run — `JWT_SECRET`,
     `NVR_SECRET_KEY` (Fernet), `BOOTSTRAP_ADMIN_*`, `REENCODE_FFMPEG_BIN`,
     `GO2RTC_WEBRTC_CANDIDATES=<lan-ip>:8556`, `PLAYBACK_TZ_OFFSET_MINUTES`,
     `DATABASE_URL` (SQLite) — and **keeps an existing `.env` on reinstall**;
   - renders the runtime `go2rtc.yaml` (`python -m app.services.go2rtc_config`);
   - runs `alembic upgrade head` and `python -m app.create_admin`;
   - **bakes** the web root + HTTPS port into the `Caddyfile`;
   - registers + starts the **three NSSM services** — `dahua-go2rtc`,
     `dahua-backend`, `dahua-caddy`;
   - waits for `/readyz` and prints the **connect URL**.

Re-running the installer is **idempotent**: secrets are preserved, only the
WebRTC candidate / binaries / services are refreshed.

## Ports

| Port   | Service | Purpose |
|--------|---------|---------|
| `8443` | Caddy   | HTTPS ingress (self-signed `tls internal`). One secure origin for the SPA, `/api/*`, and `/go2rtc/*`. **This is the port the desktop app connects to.** |
| `8556` | go2rtc  | WebRTC (UDP/TCP) low-latency transport. Advertised as the ICE candidate `<lan-ip>:8556`. |

Internal-only (behind Caddy, not exposed to clients): backend `:8000`,
go2rtc API `:1984`, go2rtc RTSP `:8553`.

## WebRTC / ICE candidates

go2rtc must advertise **only** the server's viewer-facing LAN IP as an ICE
candidate. Otherwise it auto-advertises every local IP (including the camera
network), and browsers stall on dead candidates. The installer sets
`GO2RTC_WEBRTC_CANDIDATES=<detected-lan-ip>:8556` in `.env`; pass a specific IP
by re-running `postinstall.ps1 -HostIp <ip>` if the box is multi-homed.

## Timezone (playback)

`PLAYBACK_TZ_OFFSET_MINUTES` is the NVR clock offset, in minutes **east of UTC**.
Default `0`. For **Turkmenistan / UTC+5 set it to `300`**, then restart the
`dahua-backend` service. Recorded playback timestamps depend on this being right.

## How the desktop app connects

Launch the Kanagatly VMS **desktop** app (separate `desktop-latest` release),
enter the server URL shown by the installer — `https://<lan-ip>:8443` — and log
in as `admin` with the generated password (printed by the installer and stored
in `backend\.env`). You'll be forced to change the password on first login.

The server is **LAN-only** by design; do not expose `:8443` to the public
internet.

## Managing the services

```powershell
Get-Service dahua-go2rtc, dahua-backend, dahua-caddy
# logs:
Get-Content "C:\Program Files\Kanagatly VMS Server\dahua-backend.log" -Tail 50
```

Uninstall from **Apps & features** (or `unins000.exe`) — it stops and removes all
three services first.

## Building the installer

CI does everything on `windows-latest`
(`.github/workflows/windows-installer.yml`): build the frontend, download the
embeddable Python + all binaries, pip-install deps into the embedded runtime
(`build-payload.ps1`), then `iscc installer.iss`. The resulting
`KanagatlyVMS-Server-Setup.exe` is uploaded as a build artifact **and** to the
rolling `server-latest` GitHub Release. Trigger via **Actions → Build Windows
server installer → Run workflow**, or by pushing changes under
`deploy/native/windows-installer/**`.
