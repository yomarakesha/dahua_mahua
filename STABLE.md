# STABLE v2 — smooth 4MP main (UDP), HTTPS, multi-NVR

**Tag:** `stable-2`  ·  **Previous stable:** `stable-4mp-no-freeze` (v1)
**Status:** ✅ Stable / working. Deployed on 10.10.1.152, `feat/realtime-transport` @ c62670e.

Known-good checkpoint after the realtime-transport work. Restore with
`git checkout stable-2` (then rebuild + redeploy — see below).

## What's working
- **4MP main at full frame rate.** Direct mains pull over **RTSP-UDP** into go2rtc via an
  **MPEG-TS stdout pipe** — these Dahua cams collapse to ~2 fps over TCP but deliver ~22 fps
  over UDP; the pipe avoids the loopback RTSP-republish that throttled it.
- **Player: MSE by default** (buffered TCP to the browser — stable, carries audio).
  WebCodecs is an opt-in header toggle (hardware decode + drop-late, video-only).
- **HTTPS** via a Caddy reverse proxy on `:8443` (one secure origin; required for WebCodecs,
  no mixed-content/CORS). Legacy `http://…:8080` still works (MSE only).
- **Multi-NVR.** Both NVRs served. NVR enable/add/disable now **auto hard-restarts go2rtc**
  so changes actually load (go2rtc's API reload doesn't re-init the stream registry).
- **Switchable main pull strategy** via `MAIN_STREAM_MODE` (no code edits).

## Architecture
- Relay: go2rtc. Subs re-encoded to a 0.5 s GOP; direct mains via `MAIN_STREAM_MODE`.
- TLS: Caddy service `dahua-caddy` → `https://10.10.1.152:8443` (`tls internal`), proxies
  `/api/*`→backend :8000, `/go2rtc/*`→go2rtc :1984, `/*`→static :8080.
- Services (NSSM, LocalSystem): `dahua-backend`, `dahua-go2rtc`, `dahua-frontend`, `dahua-caddy`.

## Deployed server config (`backend/.env`, non-secret keys)
```
RELAY=go2rtc
REENCODE_ENABLED=true
REENCODE_QUALITIES=sub                 # subs re-encoded (0.5s GOP); mains via MAIN_STREAM_MODE
REENCODE_VCODEC=auto                   # → libx264 (no GPU on this host)
REENCODE_PRESET=veryfast
REENCODE_KEYFRAME_SECONDS=0.5
REENCODE_FFMPEG_BIN=C:\ffmpeg\bin\ffmpeg.exe
REENCODE_MAXRATE_KBPS=3000
REENCODE_MAIN_SCALE=                   # EMPTY = native resolution (never downscale below 4MP)
GO2RTC_CONFIG_PATH=C:\deploy\dahua_mahua\.go2rtc\go2rtc.yaml
MAIN_STREAM_MODE=copy_pipe             # UDP copy → mpegts pipe (sharp). reencode_pipe = conceal loss
GO2RTC_RESTART_CMD=powershell -NoProfile -Command "Restart-Service dahua-go2rtc"
```

`MAIN_STREAM_MODE` options: `native` | `copy_pipe` (deployed) | `reencode_pipe` |
`reencode_rtsp` | `copy_rtsp` — defined in `backend/app/settings.py`.

## Restore / redeploy this version
```powershell
# on the server (C:\deploy\dahua_mahua), as admin
git fetch --all --tags
git checkout stable-2
cd web-react;  npm install;  npm run build                 # frontend
cd ..\backend; .\.venv\Scripts\pip install -r requirements.txt   # only if deps changed
Restart-Service dahua-backend
Restart-Service dahua-go2rtc       # required: go2rtc must reload its stream registry
Restart-Service dahua-caddy
```
Access at `https://10.10.1.152:8443` (accept the self-signed cert once per browser).

## Known limitation
Old-NVR 4MP mains show artifacts under the **camera segment's ~2% UDP packet loss** —
physical (switch/cabling/PoE), proven upstream of the server NIC (0 NIC RX drops). Mitigated
by `MAIN_STREAM_MODE=reencode_pipe` (ffmpeg conceals loss); the real fix is rack-side. The
new NVR (1080p) is clean. See memory `main-bottleneck-is-camera-delivery`.
