# NVR Recorded Playback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator open a Playback page, pick an NVR→camera→day, see a clip-aware timeline of recorded footage, and play/pause/seek/fast-forward it (with audio + frame snapshot).

**Architecture:** A self-contained playback pipeline separate from the live go2rtc path but sharing the NVR's scarce stream/auth budget. Backend exposes a recording **index** (Dahua `mediaFileFind`) and a per-viewer **playback session** (ffmpeg pulling `/cam/playback` RTSP → fMP4 over a persistent WebSocket); a purpose-built frontend MSE player with a backend-authoritative clock renders it.

**Tech Stack:** FastAPI + SQLAlchemy (async) + httpx (Dahua HTTP digest) + ffmpeg (`C:\ffmpeg\bin\ffmpeg.exe` on the server) backend; React + Vite + TypeScript frontend; MSE for rendering.

**Spec:** `docs/superpowers/specs/2026-06-30-nvr-playback-design.md` (read it first).

## Global Constraints

- **Spike gate:** Phase 0 must complete (on-network) and its findings pinned into the spec before Phases 2–3 are expanded to step-level and implemented. Phase 1 may proceed now (uses safe defaults).
- **Never starve live / never trip the IP-ban:** playback shares the NVR's ~stream budget (size TBD-from-spike V9) and failed-auth budget; all NVR access goes through a shared `NvrBudget` and routes auth failures into `app/services/lockouts`.
- **All recorded-time math is NVR-local time** (spec V6); the timeline axis is labeled with the NVR's UTC offset. Internally pass UTC epoch seconds; format to the NVR's local `YYYY_MM_DD_HH_MM_SS` only at the RTSP/`mediaFileFind` boundary.
- **Security:** every endpoint (incl. the WS) authenticates with the existing JWT and enforces `user_can_access_nvr` region scoping; ffmpeg argv is built as a **list** (no shell); `start`/`end`/`speed`/`channel` are strictly validated; NVR passwords never reach client-facing errors/logs.
- **ffmpeg = `settings.reencode_ffmpeg_bin`** (already `C:\ffmpeg\bin\ffmpeg.exe` on the server, `ffmpeg` in dev). Reuse it; do not hardcode a path.
- **Branch:** `feat/nvr-playback`. Frequent commits; backend `pytest` green before each commit.

---

## Phase 0 — Verification spike (run on-network, BEFORE Phases 2–3)

**Deliverable:** a findings note `docs/superpowers/specs/2026-06-30-nvr-playback-spike-findings.md` that pins every `TBD-from-spike` value in the spec. No app code. This is a runbook, not TDD.

Run each probe against **both** NVRs (`192.168.20.15`, `192.168.20.39`) using the WinRM helper (`/tmp/wr.py`) and the backend venv (to decrypt creds), exactly as the live-debugging sessions did. Record raw output in the findings note.

- [ ] **Step 1: Confirm `/cam/playback` RTSP serves recorded footage**

Pull a 1-minute window from earlier today (NVR-local time) for ch1 of each NVR and confirm frames decode:
`ffmpeg -rtsp_transport tcp -i "rtsp://USER:PASS@NVR:554/cam/playback?channel=1&starttime=YYYY_MM_DD_HH_MM_SS&endtime=…" -t 6 -f null -` (use `-loglevel info` so `frame=` prints). Record: frames, resolution, **GOP** (V2), audio codec (V7).

- [ ] **Step 2: Fast-forward reality (V1) — the highest-risk item**

With a raw RTSP probe (NOT ffmpeg's `-i`), send `PLAY` with `Scale: 4.000000` and again with `Rate-Control: no`; packet-capture the exchange. Record whether the NVR fast-plays, sends I-frames-only, or ignores it. Conclusion decides the FF path (Phase 2): `scale` vs server-side decimation.

- [ ] **Step 3: `mediaFileFind` behavior (V8) + recording mode (V3) + sub/main (V4)**

Walk the Dahua media-find sequence over the backend's httpx digest client: `factory.create` → `findFile` (channel, today's range) → repeated `findNextFile&count=100` → `close`. Record: records/day, batch cap, handle timeout, the `Type` field distribution (continuous vs Motion/Alarm), and which stream(s) (`main`/`sub`) appear.

- [ ] **Step 4: Retention (V5), timezone (V6), playback ceiling (V9)**

Find the oldest record per camera (retention). Read each NVR's configured timezone + current clock (`configManager?action=getConfig&name=NTP`/`General` and `global.cgi?action=getCurrentTime`). Open N concurrent `/cam/playback` sessions until refused → the playback ceiling.

- [ ] **Step 5: Write the findings note** and update the spec's `TBD-from-spike` rows. Commit both.

---

## Phase 1 — Backend recording index (writable now)

Pure-logic + HTTP; no media streaming. Gives the frontend the clip data and day-availability. Safe defaults where a spike value is unknown (batch cap → 100; tz → query at runtime).

### Task 1: Dahua `mediaFileFind` response parser → clip spans

**Files:**
- Create: `backend/app/services/playback/__init__.py`
- Create: `backend/app/services/playback/index_parser.py`
- Test: `backend/tests/test_playback_index_parser.py`

**Interfaces:**
- Produces: `parse_find_records(body: str) -> list[FindRecord]` where `FindRecord = dataclass(start: datetime, end: datetime, type: str, stream: str)` (naive datetimes in NVR-local time); `merge_into_clips(records: list[FindRecord], gap_tolerance_s: int = 5) -> list[Clip]` where `Clip = dataclass(start: datetime, end: datetime, type: str, stream: str)` (adjacent same-stream records merged).

- [ ] **Step 1: Write the failing test** for `parse_find_records` against a real Dahua `findNextFile` body sample (captured in Phase 0; until then use the documented format below).

```python
from app.services.playback.index_parser import parse_find_records
SAMPLE = (
    "items[0].Channel=1\r\n"
    "items[0].StartTime=2026-06-29 08:00:00\r\n"
    "items[0].EndTime=2026-06-29 08:30:00\r\n"
    "items[0].Type=dav\r\n"
    "items[0].Flags[0]=Timing\r\n"
    "items[1].StartTime=2026-06-29 08:30:00\r\n"
    "items[1].EndTime=2026-06-29 09:00:00\r\n"
    "items[1].Flags[0]=Event\r\n"
)
def test_parses_records_with_times_and_type():
    recs = parse_find_records(SAMPLE)
    assert len(recs) == 2
    assert recs[0].start.hour == 8 and recs[0].end.minute == 30
    assert recs[0].type == "Timing" and recs[1].type == "Event"
```

- [ ] **Step 2: Run test to verify it fails** — `cd backend && python3 -m pytest tests/test_playback_index_parser.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement `parse_find_records`** — regex over `items[N].<key>=<val>`, group by N, map `Flags[0]`→type, parse `StartTime`/`EndTime` with `datetime.strptime(..., "%Y-%m-%d %H:%M:%S")`, default `stream` from `Flags`/`Type`.
- [ ] **Step 4: Run test to verify it passes.**
- [ ] **Step 5: Write the failing test** for `merge_into_clips` (two adjacent records → one clip; a >tolerance gap → two clips).
- [ ] **Step 6–7: Implement `merge_into_clips`, run green.**
- [ ] **Step 8: Commit** — `git add backend/app/services/playback backend/tests/test_playback_index_parser.py && git commit -m "feat(playback): parse mediaFileFind records into merged clip spans"`.

### Task 2: `mediaFileFind` client (stateful, paginated, always-close)

**Files:**
- Create: `backend/app/services/playback/media_find.py`
- Test: `backend/tests/test_playback_media_find.py` (mock httpx; no real NVR)

**Interfaces:**
- Consumes: `parse_find_records`, `merge_into_clips`.
- Produces: `async find_clips(ip, port, user, pw, *, channel, start: datetime, end: datetime, batch=100) -> list[Clip]` — runs create→findFile→findNextFile(paginate)→close→destroy inside a `try/finally` that **always** closes the handle; raises `MediaFindError` (not a swallowed exception) on failure.

- [ ] **Step 1: Write the failing test** with a mocked httpx `AsyncClient` whose `.get` returns scripted bodies for create/find/findNext(×2, second empty)/close; assert the handle is closed even when `findNextFile` raises (use a side-effect that raises on the 2nd call).
- [ ] **Step 2: Run → fail. Step 3: Implement** using `httpx.AsyncClient(auth=httpx.DigestAuth(...))` (mirror `app/services/discovery.py`), pagination until an empty batch or `<batch` count, `finally: await _close(handle)`.
- [ ] **Step 4: Run → pass. Step 5: Commit.**

### Task 3: Index endpoint + per-day cache + region auth

**Files:**
- Create: `backend/app/routers/playback.py`
- Modify: `backend/app/main.py` (register the router)
- Test: `backend/tests/test_playback_router_index.py`

**Interfaces:**
- Produces: `GET /api/v1/playback/{nvr_id}/{channel}/index?date=YYYY-MM-DD` → `{ "tz_offset_minutes": int, "day_start_epoch": int, "day_end_epoch": int, "clips": [{ "start_epoch": int, "end_epoch": int, "type": str, "stream": str }] }`. Auth: `CurrentUser` + `user_can_access_nvr` (reuse the dependency from `app/routers/nvrs.py`). Cache keyed `(nvr_id, channel, date)` with a short TTL (e.g. 120s) in-process.

- [ ] **Step 1: Write the failing test** (FastAPI `TestClient`, auth + NVR access mocked, `find_clips` patched to return two clips) asserting the JSON shape, epoch conversion via the NVR tz, and that a second call within TTL does not re-invoke `find_clips`.
- [ ] **Step 2: Run → fail. Step 3: Implement** the router: resolve the NVR (DB row → no SSRF), decrypt creds, compute the day's `[start,end)` in NVR-local tz (tz from the NVR clock query, cached; fallback to a configured default), call `find_clips`, convert to epoch, cache.
- [ ] **Step 4: Run → pass. Step 5: Commit.**

### Task 4: Day-availability + retention hint endpoint

**Files:**
- Modify: `backend/app/routers/playback.py`
- Test: extend `backend/tests/test_playback_router_index.py`

**Interfaces:**
- Produces: `GET /api/v1/playback/{nvr_id}/{channel}/availability?month=YYYY-MM` → `{ "days_with_recordings": ["YYYY-MM-DD", …], "oldest_epoch": int|null }` (one wide `find_clips` per month, cached; or per the cheaper Dahua calendar caps query if the spike found one).

- [ ] Steps mirror Task 3 (test shape → implement → green → commit).

**Phase 1 exit:** `pytest backend/tests/test_playback_*` green; the frontend can build the timeline + retention-aware picker against real endpoints (media verified separately in Phase 0/2).

---

## Phase 2 — Backend playback session + streaming (expand to step-level AFTER Phase 0)

Outline below is concrete (files, interfaces, task list, decisions). Each task gets the full failing-test→implement→commit treatment once the spike pins FF (V1), GOP (V2), sub/main (V4), audio (V7), and the playback ceiling (V9).

### Task 5: Input validators + NVR-local time formatting (pure logic — writable now)
**Files:** `backend/app/services/playback/validate.py`, `tests/test_playback_validate.py`.
**Produces:** `parse_speed(v) -> Literal[1,2,4,8]` (reject else), `parse_seek_epoch(v) -> int` (bounded), `fmt_dahua_time(epoch, tz_offset_min) -> str` (`YYYY_MM_DD_HH_MM_SS` in NVR-local). TDD now; these gate injection-safety (§7).

### Task 6: `NvrBudget` shared semaphore + lockout integration
**Files:** `backend/app/services/playback/budget.py`, `tests/test_playback_budget.py`; **modify** `go2rtc_sync.reconcile` to acquire/account against the same budget (or a read-only check).
**Produces:** `async acquire(nvr_id, kind: Literal["live","playback"]) -> Token | None` (returns None when full; playback yields to live), `release(token)`. Size per NVR from spike V9 minus live headroom (setting `playback_max_per_nvr`). TDD the semaphore + priority now; wire to live in the same task.

### Task 7: `PlaybackSession` — ffmpeg lifecycle + fMP4 drain + back-pressure
**Files:** `backend/app/services/playback/session.py`, `tests/test_playback_session.py` (mock the subprocess + a fake pipe).
**Produces:** a session that spawns ffmpeg (argv list; `-rtsp_transport tcp -i <playback_url> -c:v copy [audio: copy|aac] -movflags +frag_keyframe+empty_moov+default_base_moof -f mp4 pipe:1`), a **dedicated drain task** reading the pipe into a **bounded GOP ring** (drop whole GOPs on overflow — never block the read), exposes `anchor_epoch (t0)`, and **controls** `seek(epoch)`/`set_speed(n)` that respawn ffmpeg behind a stable interface and signal `reinit`. Windows: wrap the child in a **Job Object (kill-on-close)**; register in a lifespan-owned set; idle + max-lifetime reaper. **FF baseline = server-side decimation** (e.g. `-vf select='eq(pict_type,I)'`/`setpts` per the spike); use `Scale` only if V1 confirmed.

### Task 8: `WS /playback/{nvr}/{ch}/stream` — auth handshake + protocol
**Files:** modify `backend/app/routers/playback.py`, `tests/test_playback_ws.py`.
**Produces:** the WS endpoint: **validate JWT in the handshake before spawning** (query param/subprotocol), enforce `user_can_access_nvr`, acquire `NvrBudget` (reject with a `{type:"error",reason:"nvr_busy"}` close if full), then bridge `PlaybackSession`: forward binary fMP4, emit `init`/`reinit`/`clock`/`eof`/`gap`/`error` JSON, accept `seek`/`speed`/`pause`/`play`/`keepalive` (debounce seek 250ms). On WS close → release budget + kill session. Sanitize all client-facing errors; audit-log the access.

### Task 9: Snapshot + thumbnail endpoints
**Files:** modify `backend/app/routers/playback.py`.
**Produces:** `GET …/thumb?at=<epoch>&w=160` → a single JPEG (`ffmpeg -ss … -i <playback_url> -frames:v 1 -vf scale=160:-1 -f mjpeg pipe:1`) for scrub-preview, cached; (the in-player PNG snapshot is drawn client-side, Phase 3). Both go through `NvrBudget` with a tiny, short-lived session.

### Task 10: Observability + active-sessions endpoint
**Files:** modify `backend/app/routers/playback.py`, `session.py`.
**Produces:** structured per-session events + ffmpeg-stderr ring captured & logged on non-zero exit; `GET …/playback/sessions` (admin) listing count/nvr/user/uptime.

**Phase 2 exit:** on-network manual checks from the spec §10 (seek-spam → no orphan ffmpeg; 5-min pause through Caddy `:8443`; budget rejection; gap/EOF) pass.

### Caddy note (deploy task within Phase 2)
Add a `:8443` route config for the playback WS: long/disabled idle timeout + `flush_interval -1`; confirm WS upgrade through the proxy. App-level keepalive ping every ~20s covers pauses.

---

## Phase 3 — Frontend (expand to step-level AFTER Phase 0; UI tasks mostly spike-independent)

### Task 11: API hooks + types
`web-react/src/api/playback.ts` — typed `useRecordingIndex(nvr,ch,date)`, `useAvailability(...)`, the WS base URL (reuse `CONFIG`), and the control-message/footage-time types. TDD the footage-time mapping helper (`footageEpoch(t0, ct, baseCt, speed)`) and `dayToEpochRange(date, tzOffset)` incl. a DST day.

### Task 12: `PlaybackPage` + nav route
`web-react/src/features/playback/PlaybackPage.tsx`, route in `src/App.tsx`, nav item. NVR→camera→day selectors; day picker greys empty/over-retention days via `useAvailability`.

### Task 13: clip-aware `Timeline`
`features/playback/Timeline.tsx` — 24h bar of clip segments (colored by type), **Pointer-Events** playhead (ghost-on-drag, **commit on release** + 250ms debounce), **prev/next-clip** buttons, gap snap/auto-skip, `role="slider"` + keyboard (←/→ ±10s, Home/End, PageUp/Down ±5m), thumbnail preview from `…/thumb`, axis labeled "NVR time (UTC±X)". TDD the pure scrub-math (pointer-x → epoch, snap-to-clip).

### Task 14: purpose-built `PlaybackPlayer` (MSE)
`features/playback/PlaybackPlayer.tsx` + `lib/video/playback-mse.ts` — own `MediaSource`/`SourceBuffer`, **no** auto-trim/`setLiveSeekableRange`/currentTime-recentering/auto-reconnect; reuse only the `ondata→appendBuffer` queue pattern from `video-rtc.js`. One persistent WS; rebuild the SourceBuffer on `{reinit}`; VOD buffer trim (wide window around currentTime; catch `QuotaExceededError`); explicit state machine `loading|playing|paused|seeking|end|no_coverage|error` driven by backend signals; backend-owned speed (playbackRate stays 1.0; audio muted when speed>1). TDD the state machine.

### Task 15: snapshot (client-side PNG)
`features/playback/useSnapshot.ts` — draw `<video>` to a canvas → PNG download named with the footage timestamp + camera.

**Phase 3 exit:** end-to-end manual run (on-network): pick camera+day, scrub, prev/next, FF, pause 5min, snapshot — against the real NVRs.

---

## Self-review notes
- **Spec coverage:** §2 spike→Phase 0; §3 index→Phase 1; §3/§4/§5/§6 session+protocol+FF+budget→Phase 2; §3 frontend + §8 edges→Phase 3; §7 security folded into Tasks 3/5/8; §9 observability→Task 10; snapshot→Tasks 9/15.
- **Deliberately deferred to step-level post-spike:** Phases 2–3 task *internals* (exact ffmpeg FF flags, fMP4 init handling specifics, the real `mediaFileFind` body shape) — because they are spike outputs, not guesses. Phases 0–1 are full TDD now.
- **No fabricated values:** any number that depends on hardware is marked `TBD-from-spike` and carries a safe default for Phase 1.
