# NVR Recorded Playback — Design Spec (MVP)

**Status:** Draft for review. Architecture revised after a four-role adversarial review
(backend/SRE, CCTV-product, frontend/UX, security). **Gated on a verification spike**
(see §2) that must run against the real NVRs before implementation begins.

## 1. Goal & scope

Let an operator **investigate a moment**: open a dedicated **Playback** page, pick an
**NVR → camera → day**, see a **timeline of recorded clips**, jump to any covered time,
and **play / pause / seek / fast-forward (2×/4×/8×)** the footage, with **audio** where
present and a **frame snapshot** for evidence.

**In scope (MVP):**
- Dedicated Playback page (new nav tab), single camera at a time.
- Day picker with a "days-with-recordings / oldest-available" hint (retention-aware).
- Clip-aware 24h timeline: shaded where footage exists, **prev/next-clip stepping**,
  draggable playhead with **commit-on-release** seek, gaps handled explicitly.
- Player controls: play, pause, ±10s skip, speed 1×/2×/4×/8× (backend-owned), audio.
- **Snapshot**: save the current frame as PNG with the NVR-local timestamp.
- Purpose-built player + backend playback-session service.

**Out of scope (fast-follows):** clip *video* export (MP4 of a range), multi-camera
synchronized playback, motion/event search beyond what the recording index gives us,
bookmarks/cases.

**Non-negotiable constraints (from review):** playback must not starve live, trip the
source watchdog, or risk the NVR firmware's failed-auth IP-ban. See §6.

## 2. Verification gate (run the spike BEFORE building)

Every reviewer independently required this — several findings can invalidate the
architecture. Run against **both** NVRs (`192.168.20.15` 4MP direct-IP cams,
`192.168.20.39` 1080p cams behind the NVR) with raw RTSP / `ffprobe` / Wireshark, not
assumptions. Each row notes how the design adapts.

| # | Verify | If NOT as hoped → design adapts to |
|---|--------|-----------------------------------|
| V1 | Does fast-play work via RTSP `Scale` + `Rate-Control: no`? Does ffmpeg even send it? | **Backend-owned speed is the baseline anyway** (server-side I-frame-stride / frame-decimation). FF is never the browser's `playbackRate`. |
| V2 | Recorded-stream **GOP** length per camera | Seeks are GOP-aligned; playhead **snaps to the actual returned keyframe**. Larger GOP = coarser seek; acceptable. |
| V3 | **Continuous vs event/motion** recording per camera | Timeline renders **discrete clips**; event-only → prev/next-clip is the primary nav, playhead snaps to clips. |
| V4 | **Sub vs main** stream recorded per camera | Default scrub/FF on **sub** (cheap) where recorded; **main** for snapshot/evidence. If main-only, use main. |
| V5 | **Retention depth** per camera/NVR | Day picker disables/flags empty days; "oldest available" shown. |
| V6 | Each NVR's **timezone + current clock** | All timeline math in **NVR-local time**; axis labeled "NVR time (UTC±X)". |
| V7 | Audio **codec** (AAC vs G.711/G.726) | If not AAC: **transcode to AAC** in the playback ffmpeg (G.711 can't go through MSE raw). |
| V8 | `mediaFileFind` **batch cap + handle timeout** | Index client paginates and **guarantees `close`**; per-NVR concurrency = 1; cache per day. |
| V9 | **Max concurrent remote-playback streams** per NVR | Sets the per-NVR playback cap (§6). Likely small (1–4). |

The spike produces a short findings note that pins these numbers; the spec's "TBD-from-spike"
values get filled in before implementation.

### 2a. Spike findings — VERIFIED 2026-06-30 (NVR `192.168.20.15`, ch1, 4MP cams)

Run live against the old NVR over the wired path. These are measured, not assumed.

| # | Finding | Impact on design |
|---|---------|------------------|
| **V3** | **Continuous recording.** `Flags[0]=Timing`, back-to-back 1-hour file segments, zero gaps (e.g. 12:00→13:00→…→17:00→17:27 open). | Timeline is one continuous span per day, not sparse event clips. `merge_into_clips` collapses the hour-segments into a single clip — correct. |
| **V4** | **MAIN-ONLY recording.** Every record is `VideoStream=Main` (4 MP). Filtering `condition.VideoStream=Sub`/`=2` is **ignored** — the NVR always returns Main. **There is no recorded sub-stream to scrub.** | ❌ Kills the "scrub the cheap sub, snapshot the main" plan (V4 fallback row). **Playback MUST use the 4 MP main.** |
| **V1/delivery** | **4 MP main playback is delivery-limited** (same wall as live): RTSP-**TCP = 0.22× realtime** (reliable, all frames) — far too slow to play; RTSP-**UDP = 0.89× realtime but ~25 % frame loss** (153 of 205 frames → corruption). | Client-side fast-forward by "pull everything, play faster" is **impossible** — we can't even deliver 1× over TCP. The session service must pull over **UDP and re-mux/re-encode** (same engine as the live main, conceals UDP loss) to reach ~realtime. FF **must** be server-side (NVR `Scale` or backend frame-decimation) — confirms §5's backend-owned-speed decision. |
| **protocol** | `factory.create` → `result=<id>`; **`findFile` returns a bare `OK` body** (not `result=true`); `findNextFile` items carry `StartTime`/`EndTime`/`Type=dav` (container)/`Flags[0]` (Timing)/**`VideoStream`** (Main/Sub). `condition.Channel` is **1-based** in the query (`Channel=1`), but results report **0-based** `Channel=0`. | Phase-1 code corrected: `_is_ok()` now accepts `OK`; parser reads stream from `VideoStream`, not `Type`. Callers pass 1-based channel. |

**Still unmeasured** (deferred, not blocking Phase 1): V1 whether the NVR honours RTSP `Scale` for true fast-play (ffmpeg can't send it — needs a custom RTSP `PLAY` or backend decimation); V2 exact GOP length; V5 retention depth; V6/V7/V9 on the new NVR; concurrent-playback ceiling. Pin these when building the Phase-2 session service.

**Net architectural consequence:** the playback session is essentially the **live-main pipeline pointed at `/cam/playback`** — UDP pull → re-mux/re-encode → MSE — because the only recorded stream is the same hard-to-deliver 4 MP main. There is no cheap proxy stream; "smooth scrub on the sub" is off the table.

## 3. Architecture (revised)

A self-contained playback pipeline. **It shares the NVR's scarce stream/auth budget with
the live path (§6)** — that coupling is explicit, not ignored.

```
Browser                          Backend (FastAPI)                 NVR (Dahua)
PlaybackPage                     routers/playback.py
 ├─ Timeline ──HTTP──────────────► GET /index ──► RecordingIndex ──► mediaFileFind (HTTP digest)
 │   (clips, retention)                              (cache, close-guaranteed)
 ├─ thumbnails ─HTTP──────────────► GET /thumb ──► snapshot ffmpeg ─► /cam/playback (1 frame)
 └─ PlaybackPlayer ─WS(persistent)─► WS /stream ──► PlaybackSession ─► /cam/playback RTSP
       (MSE, backend clock)            control msgs    (ffmpeg → fMP4)     (TCP, NVR-local time)
```

**Backend** (new `app/services/playback/` + `app/routers/playback.py`):
- `RecordingIndex` — wraps Dahua `mediaFileFind` as a **stateful, paginated, always-`close`d**
  client; merges file records into clip spans `[start,end,type,stream]`; **caches per
  (nvr,channel,day)** for minutes. Used by both `/index` and the day-availability hint.
- `PlaybackSession` — owns **one ffmpeg** pulling `/cam/playback?channel=N&starttime=…&endtime=…`,
  muxing **fMP4 to a stdout pipe**; a dedicated drain task forwards fragments over a
  **persistent WS**, with a **bounded ring buffer that drops whole GOPs** if the client
  falls behind (never blocks the demuxer). Controls (seek/speed) respawn ffmpeg **behind
  the stable socket** and emit a `reinit`. Owns a **wall-clock anchor `T0`** so the client
  can map media time → footage time. Speed = backend frame-decimation/I-frame-stride.
- `NvrBudget` — a **shared per-NVR semaphore** (sized from V9) that **both** go2rtc reconcile
  and playback acquire; playback is the **lower-priority tenant** (rejected with a clear
  "NVR busy" rather than triggering the watchdog). Playback auth failures feed the existing
  `lockouts` accounting.
- Subprocess ownership: a registry tied to app **lifespan**; on Windows each ffmpeg runs in a
  **Job Object (kill-on-close)** so the whole tree dies with the parent; idle + max-lifetime reaper.

**Frontend** (new `web-react/src/features/playback/`):
- `PlaybackPage.tsx` — nav tab "Playback"; NVR → camera → day selectors (day picker
  retention-aware).
- `Timeline.tsx` — clip-aware 24h bar (segments colored by type), **prev/next-clip**
  buttons, a **Pointer-Events** draggable playhead with a **ghost during drag + commit on
  release**, gap snap/auto-skip, `role="slider"` + keyboard, axis labeled in NVR time.
  Drag preview uses the **thumbnail endpoint**, never the live decode.
- `PlaybackPlayer.tsx` — a **purpose-built MSE player** (does NOT reuse `dss-mse`/
  `video-rtc.js`): own `MediaSource`, **no auto-trim/`setLiveSeekableRange`/currentTime
  re-centering/auto-reconnect**; VOD buffer management (trim a wide window around
  currentTime, handle `QuotaExceededError`); rebuilds the SourceBuffer on `reinit`; explicit
  state machine `loading|playing|paused|seeking|end|no_coverage|error` driven by **backend
  signals**, not "currentTime stopped". One persistent WS; controls are messages.
- `useSnapshot` — draws the `<video>` current frame to a canvas → PNG download, with the
  footage timestamp.

**Why not reuse the live player or go2rtc:** the live MSE controller is a *live-edge* machine
whose buffer-trim, seekable-range, currentTime/playbackRate rewriting, reconnect, and
"stall = signal lost" logic are all **inverted** for seekable VOD. go2rtc can't drive
per-session seek/speed and its exec streams need a config-restart to register (would thrash
live). Playback gets its own thin pipeline; it **reuses only** the low-level
`ondata → appendBuffer` queue pattern.

## 4. Control protocol (WS) & data flow

One **persistent** WebSocket per session. Client → server control JSON; server → client a
mix of binary fMP4 fragments and typed JSON signals.

**Client → server:** `{seek: <footage_epoch>}`, `{speed: 1|2|4|8}`, `{pause}`, `{play}`,
`{stream: "sub"|"main"}`, `{keepalive}`.
**Server → client:** binary fMP4; `{type:"init", t0:<epoch>, codec, audio}` (new init
follows — rebuild SourceBuffer); `{type:"reinit", t0}` on seek/speed; `{type:"clock",
wall_ts}` heartbeat (playhead resync); `{type:"eof"}`; `{type:"gap", next:<epoch>|null}`;
`{type:"error", reason}` (sanitized).

**Footage-time mapping:** the playhead is `t0 + (video.currentTime - baseCt)` (speed already
applied server-side). Never inferred from `currentTime` alone across respawns; the `clock`
heartbeat corrects drift.

**Flow:** pick camera+day → `GET /index` → render clips + retention hint → click/drag to time
T (release) → WS `{seek:T}` → backend respawns ffmpeg at the keyframe ≤ T, sends `reinit`+t0
→ player rebuilds MSE, plays → playhead snaps to the real keyframe time. **Speed** = `{speed}`
→ backend decimates → footage advances faster, `playbackRate` stays 1.0, mapping divides by
speed, **audio muted while speed>1**. **Pause** = `{pause}` + client pauses `<video>`;
keepalive pings keep the WS alive through Caddy/idle. **Gap/EOF** → typed signal → snap to
next clip / "end of recording" state.

## 5. Fast-forward (the riskiest piece)

**Decision:** speed is **backend-owned**, browser `playbackRate` stays 1.0 (per V1).
Baseline implementation = server-side **I-frame-stride / frame-decimation** in the playback
ffmpeg (e.g. select keyframes / drop frames to ~realtime data rate at higher logical speed).
If the spike (V1) shows Dahua honors `Scale`+`Rate-Control: no` *and* the rate is manageable,
we may use it for 2×; **decimation remains the 4×/8× path** because raw fast-play floods MSE.
Speed changes are **debounced** (each is an ffmpeg respawn) and audio is muted.

## 6. Resource safety (must-have, not optional)

- **Shared per-NVR budget:** `NvrBudget` semaphore (size = V9 minus live headroom). Playback
  acquires before opening a session; if none free → reject with "NVR busy, close a live tile."
  Both live reconcile and playback respect it.
- **Hard global playback cap** (e.g. 4 concurrent ffmpegs) independent of and below the live budget.
- **No scrub storm:** commit-on-release + 250ms debounce ⇒ a drag = one respawn. Reuse a single
  authenticated session and PAUSE/seek where firmware allows, rather than full teardown+reauth
  per seek (V-dependent).
- **IP-ban guard:** playback auth failures route into `lockouts`; the failed-auth budget is
  treated as shared across live + playback + probe.
- **Back-pressure:** bounded fragment ring; drop whole GOPs when the WS lags; the demuxer is
  always drained (never let a slow socket block ffmpeg's RTSP read).
- **Subprocess lifecycle:** lifespan-owned registry; Windows **Job Object** kill-on-close;
  idle + max-lifetime reaper; verify no orphan ffmpeg on FastAPI restart.
- **Recording integrity:** because scrubbing adds NVR disk-seek load that can drop live
  recording, caps + debounce double as a recording-integrity safeguard.

## 7. Security

- **WS auth:** validate the JWT in the handshake (query param or `Sec-WebSocket-Protocol`)
  **before** spawning ffmpeg; reject otherwise. `/index` and `/thumb` use the same auth as the
  rest of the API.
- **Authorization:** enforce `user_can_access_nvr` (region scoping) on `/index`, `/thumb`,
  and `/stream`. Recorded footage is more sensitive than live.
- **No injection:** `{nvr}` resolves to a DB row (no arbitrary host → no SSRF); ffmpeg argv is
  built as a **list** (no shell); `start`/`end` validated against a strict regex → parsed
  datetime; `speed` whitelisted to `{1,2,4,8}`; `channel` an int bounded by the camera count.
- **Credential hygiene:** NVR password never appears in client-facing errors or logs; ffmpeg
  stderr is captured server-side only.
- **Abuse limits:** rate-limit session creation per user; the global cap (§6) bounds DoS.
- **Audit:** log who pulled which nvr/channel/time-range (recorded-footage access trail).

## 8. Error & edge handling

- **Gaps:** scrub release into a gap → snap to nearest covered time (toast). Playback hitting
  a clip end → auto-skip to next clip, mark the discontinuity.
- **No coverage / future / before-retention:** distinct UI state ("no footage"), not "error".
- **NVR busy (budget full):** explicit, actionable message.
- **Pause vs EOF vs signal-lost:** disambiguated by backend signals + user pause state, never
  by "currentTime stopped advancing".
- **MSE failures:** explicit `reinit` rebuild; `QuotaExceededError` handled by trimming oldest
  ranges; unrecoverable append errors surface to the error state (not swallowed).
- **Timezone/DST:** day → explicit `[startEpoch,endEpoch)` in NVR-local tz; the bar renders
  that range (handles 23/25h DST days), not a hardcoded 86400s.

## 9. Observability

Per-session structured events (start/seek/speed/stop, fragments+bytes sent, NVR session
duration, ffmpeg exit code); ffmpeg stderr ring-buffered and logged on non-zero exit; an
**active-playback-sessions** endpoint (count per NVR/user, uptime) mirroring go2rtc's stream
list. Distinguish "no recording in range" from "NVR error".

## 10. Testing

- **Unit (no NVR):** playback URL builder (incl. tz formatting), `mediaFileFind` response
  parser + clip-merge, control-protocol state machine, `start/speed/channel` validators,
  footage-time mapping math, day→epoch-range (incl. a DST day).
- **Integration/manual (post-spike, on-network):** seek-spam (20 fast scrubs → no orphan
  ffmpeg, player recovers), 5-minute pause through Caddy `:8443`, FF data-rate, budget
  rejection when the NVR is full, gap/EOF behavior, snapshot correctness.
- ffmpeg/NVR interactions aren't unit-testable; rely on the spike + manual smoke + observability.

## 11. Open items to settle during the spike

Values marked **TBD-from-spike** (FF mechanism specifics, GOP, recording mode per camera,
sub/main, retention, tz, audio codec, mediaFileFind caps, playback-stream ceiling) get pinned
into this doc before the implementation plan is finalized. The architecture above is chosen so
that **none of these outcomes force a rewrite** — they tune parameters and the FF path, not the
shape.
