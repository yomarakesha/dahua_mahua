# STABLE v3 — NVR recorded playback (live browser-verified)

**Tag:** `stable-3`  ·  **Previous:** `stable-2` (UDP 4MP live main, HTTPS, multi-NVR)
**Branch:** `feat/nvr-playback-stream`  ·  **Status:** ✅ Stable / working, browser-verified on 10.10.1.152 (2026-07-01).

Known-good checkpoint: everything in stable-2 PLUS the full **recorded-playback**
feature, validated end-to-end in a real browser. Restore with
`git checkout stable-3` (then rebuild + redeploy — see below).

## What's new since stable-2
- **Recorded playback** — a dedicated Playback page: NVR → camera → day → scrub a
  clip-aware 24h timeline, play/pause/seek, fast-forward 2×/4×/8×, PNG snapshot.
- **Backend** (`app/services/playback/`, `routers/playback.py`): per-camera-authorized
  `/index` + `/availability` (Dahua `mediaFileFind`); a WS `/playback/{nvr}/{ch}/stream`
  that runs one ffmpeg per session (`/cam/playback` → fMP4 over a single serialized
  WS egress), server-side fast-forward, graceful teardown (sends RTSP TEARDOWN so the
  NVR's playback pool isn't leaked), Job-Object process cleanup, `NvrBudget` cap,
  `/thumb`, admin `/sessions`.
- **Frontend** (`web-react/src/features/playback/`): purpose-built VOD MSE player
  (own MediaSource + persistent WS, FIFO append queue, tested state machine),
  clip-aware Timeline, client-side snapshot. Live grid: right-click a tile →
  "Watch in Playback"; single-click NVR shows cameras / double-click expands.
- **Smooth / Clear transport toggle** — Smooth = UDP (near-realtime, default);
  Clear = TCP (clean image, buffers slowly) for careful review on the lossy 4MP NVR.

## Playback hard-won gotchas (do not regress)
- **`PLAYBACK_TZ_OFFSET_MINUTES=300`** in `backend/.env` — the NVR clock is UTC+5; 0
  makes all playback time-mapping 5h off (nothing plays).
- **ffmpeg `-fps_mode vfr`** (NOT `-vsync`, removed in the server's ffmpeg build) —
  else fast-forward aborts instantly.
- **`-an` (drop audio)** — the MSE init MIME is video-only
  (`video/mp4; codecs="avc1.640032"`); an AAC track → Chrome
  `CHUNK_DEMUXER_ERROR` → black. Playback is muted anyway.
- **Codec MIME = `avc1.640032`** (H.264 High L5.0 — the real libx264 avcC), not the
  Baseline `avc1.42E01E`.
- **RTSP endtime must not be in the future** — cap at now, else Dahua sends only the
  init segment and no media.
- **Player MSE**: muted (autoplay), FIFO queue flushed on `sourceopen` (don't drop the
  init segment), seek `currentTime` into the buffered range, guard every
  `SourceBuffer.buffered` read (a detached SB throws → crash).

## Known limitations
- **Old 4MP NVR (.15) playback** is network-limited (~25% UDP loss on the NVR→server
  path → artifacts on Smooth; TCP/Clear is clean but ~0.15× realtime). Same physical
  cause as the live 4MP main — real fix is rack-side. The 1080p testik NVR plays clean.
- **Playback has no audio** (dropped for MSE compat). Follow-up: emit `mp4a.40.2` in
  `init.codec` + an unmute control.
- Very-recent seeks (the actively-recording last minute) may not play on some cameras.

## Restore / redeploy this version
```powershell
# on the server (C:\deploy\dahua_mahua), as admin
git fetch --all --tags
git checkout stable-3
cd web-react;  npm install;  npm run build
cd ..\backend; .\.venv\Scripts\pip install -r requirements.txt   # only if deps changed
Restart-Service dahua-backend
Restart-Service dahua-frontend
Restart-Service dahua-go2rtc   # only if the go2rtc stream set changed
Restart-Service dahua-caddy
```
Access at `https://10.10.1.152:8443` (accept the self-signed cert once). Playback is
under the **Playback** tab. The on-network smoke checklist lives in PR #2's description.

## Stable-2 baseline (unchanged, still true)
4MP main at full frame rate (UDP + MPEG-TS pipe), MSE default, HTTPS via Caddy `:8443`,
multi-NVR with go2rtc auto-restart, `MAIN_STREAM_MODE` switchable. See git history for
`stable-2`.
