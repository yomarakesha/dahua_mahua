# Kanagatly VMS — NATIVE (no-Docker) offline install

Run the whole VMS (backend + go2rtc + Caddy TLS + web UI) **directly on the OS**,
no Docker, on a firm's own server with **no internet at install time**. For firms
that can't or won't run Docker. Windows uses **NSSM** services, Linux uses
**systemd** units.

The stack (same as the Docker appliance, just native processes):

| Service | What | Port |
|---|---|---|
| `caddy` | HTTPS ingress (self-signed) → serves the UI + proxies `/api` and `/go2rtc` | **8443** |
| `backend` | FastAPI (uvicorn), SQLite by default | 8000 (localhost) |
| `go2rtc` | live media relay (MSE + WebRTC) | **8556** (WebRTC), 1984 (localhost) |

Everything is bundled: go2rtc, a static **ffmpeg**, **Caddy**, the Python wheels,
and (on Windows) **NSSM**. The target server needs **only a Python 3.12+
interpreter** — nothing else.

---

## 1. Build the bundle (once, on an ONLINE box that MATCHES the target OS/arch)

Build on the **same OS + CPU arch** as the target server (Linux x86_64 → Linux
x86_64, Windows x64 → Windows x64). The build box needs internet, Python 3.12+,
Node/npm (to build the web UI), and (Linux) `curl`/`tar`.

**Linux target:**
```bash
./deploy/native/build-bundle.sh
# → creates ./kanagatly-vms-native/
```

**Windows target:**
```powershell
powershell -ExecutionPolicy Bypass -File deploy\native\build-bundle.ps1
# → creates .\kanagatly-vms-native\
```

The builder downloads the pinned binaries, `pip download`s all wheels
(`--only-binary=:all:` → no compiler needed on the target), `npm ci && npm run
build`s the web UI, and stages the backend source + templates.

> **Cross-OS build** (e.g. build a Windows bundle on Linux): the binaries are
> per-OS, so this is only partly possible. For the wheels, add
> `--platform <tag> --python-version 3.12 --abi cp312 --implementation cp
> --only-binary=:all:` to the `pip download` line (see the comment in the build
> script). Building on a matching box is strongly recommended.

**Binary sources (pinned):**
- **go2rtc** `v1.9.14` — GitHub releases (`AlexxIT/go2rtc`).
- **ffmpeg** — static builds: Linux from [johnvansickle.com](https://johnvansickle.com/ffmpeg/)
  (amd64/arm64; BtbN static builds are an equivalent alternative), Windows from
  [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) (release-essentials).
- **Caddy** `2.8.4` — GitHub releases (`caddyserver/caddy`).
- **NSSM** `2.24` (Windows only) — nssm.cc.

---

## 2. Copy the folder to the firm's server

Copy the whole `kanagatly-vms-native/` folder over (USB, scp, etc.). The server
needs **only Python 3.12+** — no internet, no pip index, no Docker, no Node.

**Prerequisite — Python 3.12+ on the target:**
- **Linux:** `apt install python3.12 python3.12-venv` (or your distro's package).
- **Windows:** install from python.org (any 3.12/3.13; "Add to PATH" or use the
  `py` launcher).
- No Python allowed on the box? Bundle a portable interpreter beside the folder
  and point the installer at it (`PYTHON=/path/to/python ./install.sh`, or
  Windows `-` set `py`/`python` on PATH). Everything else is already bundled.

---

## 3. Install (on the server)

**Linux (systemd), as root:**
```bash
cd kanagatly-vms-native
sudo ./install.sh
#   sudo INSTALL_DIR=/srv/vms ./install.sh     # custom dir (default /opt/kanagatly-vms)
#   sudo HOST_IP=10.0.0.5 ./install.sh         # pin the LAN IP
```

**Windows (NSSM), in an ELEVATED PowerShell:**
```powershell
cd kanagatly-vms-native
powershell -ExecutionPolicy Bypass -File install.ps1
#   ... -InstallDir D:\KanagatlyVMS -HostIp 10.0.0.5
```

`install` will:
- create a **venv** and `pip install --no-index --find-links wheelhouse` (offline),
- place the bundled **go2rtc / ffmpeg / caddy** binaries,
- generate per-install secrets (`JWT_SECRET`, `NVR_SECRET_KEY`, admin password),
- **detect this server's LAN IP** and write both `GO2RTC_WEBRTC_CANDIDATES=<ip>:8556`
  and `REENCODE_FFMPEG_BIN=<bundled ffmpeg>` into `backend/.env`,
- render the runtime `go2rtc.yaml`, run `alembic upgrade head`, ensure the admin,
- register + start the services, wait for `/readyz`, and **print the connect address**:

```
  → Open the desktop app and connect to:  https://<server-ip>:8443
```

**How the host IP is found:** Linux uses `ip route get 1.1.1.1` (default-route
source IP), falling back to `hostname -I`. Windows uses the `Up` adapter that has
an IPv4 default gateway, falling back to the first private-range IPv4. Override
with `HOST_IP=` / `-HostIp`. It's written into `backend/.env` as the go2rtc
WebRTC candidate. (Re-running the installer refreshes it for the current IP.)

**Services registered:**
- Linux systemd units: `kanagatly-go2rtc`, `kanagatly-backend`, `kanagatly-caddy`.
- Windows NSSM services: `dahua-go2rtc`, `dahua-backend`, `dahua-caddy`.

The first admin login **forces a password change**, then the admin creates users
in the app.

---

## Connect the desktop app

Install the **Kanagatly VMS** desktop app, launch it, and enter the address the
installer printed (`https://<server-ip>:8443`). It auto-trusts the self-signed
cert. Everyone on the LAN uses the same address.

## Ports to open (firewall)

- **8443/tcp** — the UI/API (what the desktop app + browsers connect to).
- **8556/tcp + 8556/udp** — go2rtc WebRTC media (the smooth 4 MP main view).

## WebRTC note (important)

The smooth fullscreen main uses **WebRTC**, which must advertise the server's real
LAN IP. The installer sets that automatically (`GO2RTC_WEBRTC_CANDIDATES`). Native
installs run go2rtc as a plain process bound to the host, so WebRTC works directly
(no Docker host-networking caveat); live falls back to **MSE** over `:8443` if
WebRTC can't connect. On a **multi-NIC / VPN box** auto-detect may pick the wrong
interface — set `GO2RTC_WEBRTC_CANDIDATES=<correct-lan-ip>:8556` in `backend/.env`
and re-run the installer (or restart the go2rtc + backend services).

## Timezone

Playback times use `PLAYBACK_TZ_OFFSET_MINUTES` (minutes east of UTC; e.g. `300`
for UTC+5 / Turkmenistan). Set it in `backend/.env` for the firm's timezone — the
backend logs the effective value at startup. Restart the backend after editing.

## Database

Default is **SQLite** at `backend/dss.db` — nothing extra to run. For Postgres,
set `DATABASE_URL=postgresql+asyncpg://…` in `backend/.env` before install (or set
it and re-run `alembic upgrade head` from the venv), then restart the backend.

## Managing the services

**Linux:**
```bash
systemctl status  kanagatly-{go2rtc,backend,caddy}
systemctl restart kanagatly-backend
journalctl -u kanagatly-backend -f
```

**Windows:**
```powershell
Get-Service dahua-go2rtc,dahua-backend,dahua-caddy
Restart-Service dahua-backend
# logs: <InstallDir>\dahua-*.log
```

## Uninstall

**Linux:** `systemctl disable --now kanagatly-{go2rtc,backend,caddy}`, remove
`/etc/systemd/system/kanagatly-*.service`, `systemctl daemon-reload`, then delete
the install dir. **Windows:** `nssm remove dahua-go2rtc confirm` (and `-backend`,
`-caddy`), then delete the install dir.
