# DSS — Dahua Surveillance System Dashboard

Web-based camera dashboard for managing and viewing Dahua NVR camera feeds via WebRTC.

![License](https://img.shields.io/badge/license-MIT-blue)

## Features

- Live WebRTC streaming from Dahua NVRs (low-latency, on-demand)
- Configurable grid layouts (2x2 up to 64x64)
- NVR management UI — add, edit, delete NVRs from the browser
- Patrol mode — auto-cycle through camera pages
- Custom groups and saved layouts
- Fullscreen view with snapshot capture
- Keyboard-driven workflow
- Dark theme

## Requirements

- **Python 3.8+** (stdlib only, no pip packages)
- **MediaMTX** binary for your platform

## Quick Start

### macOS / Linux

1. Download the [latest MediaMTX release](https://github.com/bluenviron/mediamtx/releases) for your platform:
   - macOS ARM: `mediamtx_v1.16.3_darwin_arm64.tar.gz`
   - macOS Intel: `mediamtx_v1.16.3_darwin_amd64.tar.gz`
   - Linux: `mediamtx_v1.16.3_linux_amd64.tar.gz`

2. Extract the `mediamtx` binary into this directory.

3. Generate the config and start:
   ```bash
   python3 generate_config.py
   chmod +x run.sh
   ./run.sh
   ```

4. Open **http://localhost:8080**

### Windows

1. Download [mediamtx_v1.16.3_windows_amd64.zip](https://github.com/bluenviron/mediamtx/releases/download/v1.16.3/mediamtx_v1.16.3_windows_amd64.zip).

2. Extract `mediamtx.exe` into this directory.

3. Generate the config and start:
   ```
   python generate_config.py
   run.bat
   ```

4. Open **http://localhost:8080**

## Project Structure

```
dss/
├── server.py              # Web server + REST API + MediaMTX process manager
├── run.sh                 # Start script (macOS/Linux)
├── run.bat                # Start script (Windows)
├── generate_config.py     # Generates mediamtx.yml from NVR inventory
├── nvr_inventory.json     # NVR definitions (IPs, channels, credentials)
├── mediamtx               # MediaMTX binary (macOS/Linux, not in repo)
├── mediamtx.exe           # MediaMTX binary (Windows, not in repo)
├── mediamtx.yml           # Auto-generated MediaMTX config
├── mediamtx.yml.default   # Default MediaMTX config reference
├── web/
│   ├── index.html         # Dashboard HTML
│   ├── style.css          # Styles
│   └── app.js             # Dashboard application
└── LICENSE
```

## NVR Management

Click the gear icon (or press `,`) in the toolbar to open NVR Settings:

- **Global Defaults** — RTSP port, username, password, stream type (main/sub)
- **NVR Table** — edit IP, channels, per-NVR password overrides
- **Add / Delete** — add new NVRs or remove existing ones
- **Save & Apply** — writes config, regenerates `mediamtx.yml`, restarts MediaMTX

All changes are applied live without restarting the server.

## API

The built-in server exposes a simple REST API:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/api/inventory` | Returns `nvr_inventory.json` |
| `PUT`  | `/api/inventory` | Validates, saves, regenerates config, restarts MediaMTX |
| `POST` | `/api/restart` | Force restart MediaMTX |

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `1`-`6` | Grid size (2x2 to 64x64) |
| `Arrow Left/Right` | Previous / next page |
| `Space` | Toggle patrol mode |
| `F` | Fullscreen focused camera |
| `S` | Snapshot |
| `/` | Focus search |
| `,` | NVR settings |
| `G` | Create new group |
| `Tab` | Toggle sidebar |
| `?` | Show shortcuts |
| `Esc` | Close modal / exit fullscreen |

## Configuration

Edit `nvr_inventory.json` directly or use the web UI:

```json
{
  "global": {
    "default_port": 554,
    "default_username": "admin",
    "default_password": "yourpassword",
    "default_subtype": 1
  },
  "nvrs": [
    {
      "id": "nvr01",
      "label": "Front Building",
      "ip": "192.168.1.100",
      "channels": 16,
      "group": "dahua"
    }
  ]
}
```

After manual edits, regenerate the config:

```bash
python3 generate_config.py
```

## Ports

| Port | Service |
|------|---------|
| 8080 | Web UI + API |
| 8554 | RTSP server (MediaMTX) |
| 8889 | WebRTC server (MediaMTX) |
| 8888 | HLS server (MediaMTX) |
| 9997 | MediaMTX API |

## License

MIT
