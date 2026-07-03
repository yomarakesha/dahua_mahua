# STABLE v4 — hardening + SmartPSS-parity + Hikvision (browser-verified)

**Tag:** `stable-4`  ·  **Previous:** `stable-3` (NVR recorded playback)
**Branch:** `feat/nvr-playback-stream`  ·  **Status:** ✅ Stable / deployed on 10.10.1.152, browser-verified (2026-07-03).

Known-good checkpoint covering the 21 commits after `stable-3`: a full project-wide
hardening pass, the SmartPSS-parity live speedups, and multi-vendor (Dahua +
Hikvision) support. Restore with `git checkout stable-4` then rebuild + redeploy.
Backend **328 tests** / frontend **191 tests**, all green. Every change was
adversarially reviewed (subagent implement → review flow); reviews caught + fixed
~10 real issues before merge.

## What's new since stable-3
- **Observability / log collection** — frontend ring buffer ships console + player
  state + WS close codes to `/api/v1/client-log` on crash (crash ships bypass the
  throttle, beacon-overflow → fetch). React ErrorBoundary + `window.onerror` +
  `unhandledrejection` + Query onError. Backend `RotatingFileHandler`
  (`backend/logs/backend.log`), `X-Request-ID` on 500s, playback `session_id` in
  init/reinit for frontend↔backend correlation. **(verified e2e: a POST to
  /client-log lands in backend.log.)**
- **Live-wall resilience** — stalled-tile self-heal (3 tries, ws-OPEN gated) +
  Retry overlay; vendor reconnect **jitter** (no go2rtc-restart stampede);
  staggered connects on page/patrol flips; hidden-tab teardown after 10s grace;
  WebCodecs bounded retry + non-sticky MSE demotion.
- **Watchdog works in prod** — `source_watch` now polls **go2rtc** (was MediaMTX-
  only, i.e. the IP-ban guard never fired under the prod relay). Conservative
  heuristic + a `source_watch_dial_grace_seconds=20` so a cold re-dial can't
  auto-disable a healthy NVR.
- **Backend hygiene** — `user_cameras` Alembic migration 0003 (fresh Postgres was
  broken), settings `reencode_*` dedupe, reconcile `asyncio.Lock`, `to_thread` for
  blocking probes, **`/readyz`** (db + relay), exec-mode orphan cleanup on NVR
  delete, login timing-oracle fix, NVR ip/port validation (input schemas only).
- **UX** — readable tile labels (12px + `ch{n}` + hover title), WCAG contrast
  (`ink-faint` #8a97a0, 6.5:1), honest "Showing X/Y" (was a fake online count),
  Smooth/Clear tooltips, dialog a11y + Esc, reduced-motion, "NVR local time"
  caption.
- **SmartPSS-parity (live speed)** —
  - **Warm stream pool** (`app/services/warm_pool.py`): keeps a bounded, NvrBudget-
    aware set of **SUBS** warm so open drops from 2.6–5s cold to **~0.5s**.
    `POST /api/v1/live/warm`, subs only, global cap 24 / per-NVR cap 8 / 10s grace,
    in-grace pulls counted against the caps (no page-flip overshoot).
    **Config-gated: `WARM_POOL_ENABLED` (currently ON in the server .env).**
  - **Instant fullscreen** — shows the SUB immediately, cross-fades to the 4MP MAIN
    when its first frame lands; pointerdown preconnect; warm-set reporting.
- **Multi-vendor: Hikvision** — the vendor picker is now in the main Add form (was
  hidden under Advanced) and editable per-row with auto re-Test; Hikvision channel
  auto-detect + camera-IP import via **ISAPI** (`/ISAPI/ContentMgmt/InputProxy/
  channels`). RTSP path was already vendor-correct (`/Streaming/Channels/{ch*100+
  stream}`). Verified: 192.168.20.28 streams on the Hikvision path.

## Deployment gotchas (still true — do not regress)
- Playback: `PLAYBACK_TZ_OFFSET_MINUTES=300`, ffmpeg `-fps_mode vfr`, `-an` (drop
  audio or MSE CHUNK_DEMUXER_ERROR→black), codec `avc1.640032`, cap RTSP endtime
  at now, guard every `SourceBuffer.buffered` read.
- Live media server = **go2rtc** (API :1984, RTSP :8553, WebRTC :8556). MediaMTX is
  legacy/off (`relay="go2rtc"`). The app orchestrates go2rtc for live; the backend
  is its own mini media server only for **playback** (ffmpeg → fMP4 → WS).
- Warm pool caps protect testik's (.39) concurrent-pull limit — verified in review
  under page-flip churn. Watch NVR load if raising the caps.

## Open / next (not in this tag)
- **Main 4MP smoothness = switch the fullscreen main to WebRTC** (drop-late frames,
  like SmartPSS). Research (2026-07-03) concluded: stay on go2rtc, don't migrate
  servers, don't pursue the native SDK — it's a transport change. **Risk to spike
  first:** Chrome WebRTC mandates H.264 Constrained Baseline but our mains are High
  (`avc1.640032`) — go2rtc may pass through or transcode to Baseline. Config:
  `webrtc: candidates: ["10.10.1.152:8556"]`, `ice_servers: []`, open TCP+UDP 8556.
  Plan B: hardened WebCodecs (normalize SPS/PPS avcC server-side).
- WS-engine extraction refactor (#8, maintainability). Playback audio. "Session
  expired" toast on a 401'd write. PR #2 → merge to `main`.

## Restore / redeploy
```powershell
git fetch --all --tags; git checkout stable-4
cd web-react;  npm install;  npm run build
cd ..\backend; .\.venv\Scripts\python -m alembic upgrade head
Restart-Service dahua-backend; Restart-Service dahua-frontend
Restart-Service dahua-go2rtc; Restart-Service dahua-caddy   # only if their config changed
```
Access `https://10.10.1.152:8443`. `/readyz` → `{"db":"ok","relay":"ok"}` when healthy.

## Baselines (unchanged)
stable-3 = NVR recorded playback. stable-2 = smooth 4MP main (UDP), HTTPS, multi-NVR.
See git history.
