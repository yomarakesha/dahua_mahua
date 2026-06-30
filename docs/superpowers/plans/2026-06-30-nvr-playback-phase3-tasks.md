# Phase 3 — Frontend Playback Page: Task Specs (Tasks 11–15)

> **Grounding note.** All patterns are derived from reading the real source files listed below;
> no interfaces are invented.  Codebase references are to `web-react/src/` unless stated.

## Grounding findings (read before touching code)

### Routing & nav (`App.tsx`, `components/AppShell.tsx`)
- React Router v6.  All authenticated pages are nested under `RequireAuth → AppShell` in `App.tsx`.
- The `index` route (`path=""`) renders `LiveWall`.  New routes add `<Route path="X" element={...} />` at the same level.
- `AppShell.tsx` keeps a `NAV: NavItem[]` array (`{ to, label, Icon, adminOnly? }`).  Each item renders as a `<NavLink>` with active/inactive Tailwind classes; the "Live" link uses `end={to === "/"}` to avoid matching children.
- The Playback route goes in `NAV` after "Live" (before admin-gated items) — **no `adminOnly`**.

### Feature directory conventions (`features/live/`)
- One directory per feature: page + sub-components + hooks, no barrel index.
- Page component is the default export; sub-components are named exports.
- Custom hooks (`useClock.ts`) live in the feature folder, not `lib/`.

### API calling conventions (`api/client.ts`, `api/hooks.ts`, `api/types.ts`)
- `http.get<T>(path, query?)` — `path` is relative to `CONFIG.backendBase` (`/api/v1`).
- Auth: `getToken()` from `localStorage`, appended as `Authorization: Bearer ...`.
- Hooks use TanStack Query (`useQuery`).  Query keys are on the exported `qk` object.
- Types mirroring backend schemas live in `api/types.ts`.

### WS URL derivation (`lib/config.ts`)
```
CONFIG.backendBase:
  HTTPS (Caddy :8443):  window.location.origin + "/api/v1"
  HTTP  (dev :8000):    "http://" + host + ":8000/api/v1"
```
The playback WS lives at `{backendBase}/playback/{nvrId}/{channel}/stream`.
Convert `http→ws` / `https→wss` via `CONFIG.backendBase.replace(/^http/, "ws")`.
Token goes as a query param (`?token=...`) because `WebSocket` constructor does not accept
custom headers.

### MSE `appendBuffer` queue pattern (video-rtc.js lines 477–569)
The pattern to **reuse** (without the live-edge trimmings):
```
const sb = ms.addSourceBuffer(codecString)   // codecString from init msg
sb.mode = "segments"
let buf: ArrayBuffer | null = null           // pending fragment while sb.updating

sb.addEventListener("updateend", () => {
  if (!sb.updating && buf !== null) {
    const toAppend = buf
    buf = null
    try { sb.appendBuffer(toAppend) } catch { /* QuotaExceededError → trim then retry */ }
  }
})

ws.onmessage = (ev) => {
  if (typeof ev.data === "string") { handleJson(JSON.parse(ev.data)); return }
  const data = ev.data as ArrayBuffer
  if (sb.updating) { buf = data }            // queue at most one; drop older
  else { try { sb.appendBuffer(data) } catch { /* handle */ } }
}
```
**Do NOT copy from video-rtc.js:**
- `setLiveSeekableRange` / live-edge buffer trim (will invert VOD seeking).
- `playbackRate` manipulation (playback keeps `video.playbackRate === 1.0`).
- `currentTime` re-centering / "stall = error" polling.
- Auto-reconnect loop (reconnect = explicit seek from the page, not automatic).

### Existing MSE player components
- `components/video/MsePlayer.tsx` — wraps `<dss-mse>` custom element (go2rtc VideoRTC).
  **Not reused by the VOD player.**
- `components/video/WebCodecsPlayer.tsx` — WebCodecs canvas path.
  **Not reused by the VOD player.**
- `lib/video/webcodecs-engine.ts` — hardware decode engine.
  **Not reused** (live-only, no seek API).

### Test runner
**Vitest** (`vitest` in devDependencies; `vite.config.ts` `test.environment = "jsdom"`,
`test.globals = true`, `setupFiles = ["./src/test/setup.ts"]`).
Existing test: `features/auth/LoginPage.test.tsx` (uses `describe/it/expect` from vitest globals,
`@testing-library/react`).  Run with `npm test` (maps to `vitest`).

Pure-logic utilities go in `.test.ts` files alongside the source, not in `src/test/`.

---

## Task 11 — API Types + Playback Hooks

**Goal:** Define the TypeScript types for Phase-1 API responses and WS messages, add TanStack
Query hooks for the two HTTP endpoints, and implement + unit-test the pure-logic utilities
(footage-time mapping, day→epoch, state-machine reducer).

### Files created / modified

| Operation | Path |
|-----------|------|
| Modify | `web-react/src/api/types.ts` |
| Modify | `web-react/src/api/hooks.ts` |
| Create  | `web-react/src/features/playback/types.ts` |
| Create  | `web-react/src/features/playback/playback-utils.ts` |
| Create  | `web-react/src/features/playback/playback-utils.test.ts` |

### Exact interfaces

#### `api/types.ts` additions

```ts
/** One merged clip span from GET /playback/{nvr_id}/{ch}/index */
export interface RecordingClip {
  start_epoch: number;   // UTC epoch seconds (inclusive)
  end_epoch: number;     // UTC epoch seconds (exclusive)
  type: string;          // e.g. "dav" (container type from NVR)
  stream: string;        // "Main" (always Main per spike V4)
}

/** Full response from GET /playback/{nvr_id}/{ch}/index?date=YYYY-MM-DD */
export interface RecordingIndex {
  tz_offset_minutes: number;   // NVR local = UTC + tz_offset_minutes
  day_start_epoch: number;     // epoch of 00:00:00 NVR-local
  day_end_epoch: number;       // epoch of 00:00:00 NVR-local next day
  clips: RecordingClip[];
}

/** Response from GET /playback/{nvr_id}/{ch}/availability?month=YYYY-MM */
export interface RecordingAvailability {
  days_with_recordings: string[];  // sorted ["YYYY-MM-DD", ...]
  oldest_epoch: number | null;     // epoch of oldest clip start, null if empty month
}
```

#### `features/playback/types.ts` (WS protocol + state machine)

```ts
// ── Client → server control messages ──────────────────────────────────────────

export type SeekMsg     = { seek: number };           // footage epoch (UTC seconds)
export type SpeedMsg    = { speed: 1 | 2 | 4 | 8 };
export type PauseMsg    = { pause: true };
export type PlayMsg     = { play: true };
export type StreamMsg   = { stream: "main" };         // always "main" (no sub recorded)
export type KeepaliveMsg = { keepalive: true };

export type ClientMsg = SeekMsg | SpeedMsg | PauseMsg | PlayMsg | StreamMsg | KeepaliveMsg;

// ── Server → client typed JSON signals ────────────────────────────────────────
// Binary fMP4 fragments are NOT represented here (handled as ArrayBuffer in ws.onmessage).

export interface InitMsg {
  type: "init";
  t0: number;        // footage epoch of the first frame in this segment
  codec: string;     // codec string for addSourceBuffer, e.g. 'video/mp4; codecs="avc1.4d0028"'
  audio: boolean;    // whether audio track is present
}

export interface ReinitMsg {
  type: "reinit";
  t0: number;        // footage epoch of the first frame after seek/speed change
}

export interface ClockMsg {
  type: "clock";
  wall_ts: number;   // server monotonic wall time (seconds) for drift correction
                     // NOTE: see Ambiguity #1 below — confirm whether this is footage epoch
                     //       or a monotonic counter; the spec says "playhead resync" but the
                     //       field name is wall_ts (wall clock), not footage_ts.
}

export interface EofMsg {
  type: "eof";
}

export interface GapMsg {
  type: "gap";
  next: number | null;   // epoch of next clip start, null if no more clips
}

export interface ErrorMsg {
  type: "error";
  reason: string;    // sanitized human-readable message
}

export type ServerMsg = InitMsg | ReinitMsg | ClockMsg | EofMsg | GapMsg | ErrorMsg;

// ── Player state machine ───────────────────────────────────────────────────────

export type PlayerState =
  | "loading"       // WS open, waiting for first init + fMP4 data
  | "playing"       // video.play() active, media advancing
  | "paused"        // user-requested pause (video.pause() called)
  | "seeking"       // seek sent, waiting for reinit
  | "end"           // eof received and confirmed (no more clips)
  | "no_coverage"   // seek target has no clips (distinct from error)
  | "error";        // unrecoverable (NVR error, MSE append error, WS closed unexpectedly)

// What drives each transition (for the reducer):
//   loading    → playing:      first fMP4 chunk appended + video.play() succeeds
//   playing    → paused:       user sends {pause}
//   paused     → playing:      user sends {play}
//   playing    → seeking:      user commits a seek (drag release or skip)
//   seeking    → loading:      reinit received (MSE rebuild started)
//   loading    → playing:      (same as above; re-enters via reinit)
//   playing    → end:          eof received + gap.next === null
//   playing    → no_coverage:  gap received into a region with no clips (see Ambiguity #2)
//   any        → error:        {type:"error"} received OR MSE QuotaExceeded unrecoverable
//                               OR WS closed without eof (and not user-paused/seeking)

// ── Footage-time mapping ───────────────────────────────────────────────────────
// Playhead = t0 + (video.currentTime - baseCt)
// where t0 and baseCt are captured at each init/reinit.
// The clock heartbeat replaces this with: playhead = clockMsg.wall_ts (if that's footage epoch)
// — see Ambiguity #1.

export interface FootageAnchor {
  t0: number;          // footage epoch captured from init/reinit
  baseCt: number;      // video.currentTime at the moment t0 was captured
  speed: 1 | 2 | 4 | 8;
}
```

#### `features/playback/playback-utils.ts` — pure, testable logic

```ts
/**
 * Compute the current footage epoch from the MSE anchor.
 * Speed is already applied server-side; video.currentTime advances at wall-clock rate.
 * playbackRate on the <video> element is always 1.0.
 */
export function footageEpoch(anchor: FootageAnchor, currentTime: number): number {
  return anchor.t0 + (currentTime - anchor.baseCt);
}

/**
 * Given a footage epoch, return which clip it falls in (or null if in a gap).
 */
export function findClipAt(clips: RecordingClip[], epoch: number): RecordingClip | null { ... }

/**
 * Snap a requested seek epoch to the nearest covered time.
 * If epoch is inside a clip → return epoch unchanged.
 * If epoch is in a gap → return start of the next clip (snap forward).
 * If epoch is before the first clip → return first clip start.
 * If epoch is after the last clip → return null (indicates eof / no_coverage).
 */
export function snapToNearest(clips: RecordingClip[], epoch: number): number | null { ... }

/**
 * Return the day boundaries as UTC epoch seconds for a YYYY-MM-DD string in the
 * NVR's timezone.  Mirrors backend day_to_epochs().
 *
 * tz_offset_minutes: NVR local = UTC + tz_offset_minutes (same sign convention as backend).
 *
 * IMPORTANT: This is a pure calendar conversion — it does NOT handle DST transitions
 * within the NVR's tz (the NVR uses a fixed UTC offset, not a DST-aware zone).
 * A "DST day" as the spec says is handled by the BROWSER's local clock DST, not by the NVR.
 * The NVR day boundaries use the fixed offset only.
 */
export function dayToEpochs(
  dateStr: string,           // "YYYY-MM-DD"
  tzOffsetMinutes: number,   // from RecordingIndex.tz_offset_minutes
): [dayStart: number, dayEnd: number] { ... }

/** Format a UTC epoch as HH:MM:SS in NVR-local time (for axis labels). */
export function epochToNvrTimeStr(epoch: number, tzOffsetMinutes: number): string { ... }
```

### Query key additions to `api/hooks.ts`

```ts
qk.recordingIndex   = (nvrId: string, channel: number, date: string) =>
  ["playback", "index", nvrId, channel, date] as const;

qk.recordingAvail   = (nvrId: string, channel: number, month: string) =>
  ["playback", "availability", nvrId, channel, month] as const;
```

### Hook signatures

```ts
export function useRecordingIndex(
  nvrId: string,
  channel: number,
  date: string,        // "YYYY-MM-DD"
  enabled = true,
): UseQueryResult<RecordingIndex> {
  return useQuery({
    queryKey: qk.recordingIndex(nvrId, channel, date),
    queryFn: () =>
      http.get<RecordingIndex>(`/playback/${nvrId}/${channel}/index`, { date }),
    enabled: enabled && !!nvrId && channel > 0 && /^\d{4}-\d{2}-\d{2}$/.test(date),
    staleTime: 120_000,    // matches backend 120s cache
  });
}

export function useRecordingAvailability(
  nvrId: string,
  channel: number,
  month: string,       // "YYYY-MM"
  enabled = true,
): UseQueryResult<RecordingAvailability> {
  return useQuery({
    queryKey: qk.recordingAvail(nvrId, channel, month),
    queryFn: () =>
      http.get<RecordingAvailability>(`/playback/${nvrId}/${channel}/availability`, { month }),
    enabled: enabled && !!nvrId && channel > 0 && /^\d{4}-\d{2}$/.test(month),
    staleTime: 120_000,
  });
}
```

### Steps (TDD-first for the pure logic, then hooks)

**Step 11a — Pure-logic tests first (vitest)**
Write `playback-utils.test.ts` before the implementation.

Test cases for `footageEpoch`:
- `footageEpoch({ t0: 1000, baseCt: 0, speed: 1 }, 5)` → `1005`
- `footageEpoch({ t0: 1000, baseCt: 3, speed: 1 }, 5)` → `1002`
- Anchor at non-zero baseCt (to prove baseCt subtraction not addition)

Test cases for `dayToEpochs`:
- UTC+5 (300 min): `dayToEpochs("2026-01-01", 300)` → `[2025-12-31T19:00:00Z, 2026-01-01T19:00:00Z]` in epoch numbers
- UTC+0: `dayToEpochs("2026-06-30", 0)` → `[1751241600, 1751328000]`
- UTC-6 (-360 min): `dayToEpochs("2026-03-08", -360)` → dayStart = 2026-03-08T06:00:00Z in epoch
- Note: spec says "DST day" test — the NVR uses a fixed offset, so this tests the offset
  arithmetic, not DST ambiguity.  The day returned is always exactly 86400 s when the NVR
  offset is fixed.  State this explicitly in the test comment.

Test cases for `snapToNearest`:
- Epoch inside clip → returns same epoch unchanged
- Epoch in gap between two clips → returns start of next clip
- Epoch before first clip → returns first clip start
- Epoch after last clip → returns null
- Empty clips array → returns null

Test cases for `findClipAt`:
- Within clip → returns that clip
- At exact start epoch (inclusive) → returns clip
- At exact end epoch (exclusive) → returns null (gap)
- In gap → returns null

Test cases for `epochToNvrTimeStr`:
- UTC+5 offset, epoch for 12:30:00 NVR-local → "12:30:00"
- UTC-3, midnight NVR-local

**Step 11b — Implement `playback-utils.ts`** (make tests pass)

**Step 11c — Add types to `api/types.ts`** (append, no existing code touched)

**Step 11d — Add `features/playback/types.ts`** (create new file)

**Step 11e — Add hooks to `api/hooks.ts`** (append `qk` keys and two `useQuery` hooks)

**Step 11f — `npm test` must pass**

### Integration points
- HTTP endpoints: `GET {CONFIG.backendBase}/playback/{nvrId}/{channel}/index?date=...`
  and `.../availability?month=...`
- Auth: standard `Authorization: Bearer {token}` via `http.get()` — no special handling needed.

### Ambiguities
**Ambiguity A1 — `ClockMsg.wall_ts` semantics:** Spec §4 says the `clock` heartbeat carries
`wall_ts` and is used for "playhead resync."  But it's called `wall_ts`, not `footage_ts`.
Two interpretations: (a) it's the current footage epoch at the server → client can set
`playhead = wall_ts` directly; (b) it's a server wall-clock reading used to correct for
Caddy/WS latency.  **Likely (a)** given the "playhead resync" language.  Flag for Phase-2
backend implementer to confirm the exact type before PlaybackPlayer consumes it.

---

## Task 12 — PlaybackPage + Nav Route

**Goal:** Scaffold the Playback page with NVR → camera → date selectors and wire it into
the router and nav bar.  Player and Timeline are rendered as placeholders here; they land
in Tasks 13/14.

### Files created / modified

| Operation | Path |
|-----------|------|
| Modify | `web-react/src/App.tsx` |
| Modify | `web-react/src/components/AppShell.tsx` |
| Create  | `web-react/src/features/playback/PlaybackPage.tsx` |

### Exact route change (`App.tsx`)

Add inside the `AppShell` wrapper, after the index route:
```tsx
import PlaybackPage from "@/features/playback/PlaybackPage";
// ...
<Route path="playback" element={<PlaybackPage />} />
```
No `RequireAuth` wrapper needed — the parent `RequireAuth` already gates all children.

### Exact nav addition (`AppShell.tsx`)

Add to the `NAV` array after the Live entry:
```ts
{ to: "/playback", label: "Playback", Icon: FilmIcon },
```
Add `end` prop: `end={false}` (default) — the path is never a prefix of another route so
no special matching needed.  Unlike `/`, it does not need `end={to === "/"}` treatment.

Add `FilmIcon` to `components/icons.tsx` (a clapperboard or film strip SVG; follow the
existing `Ic` pattern).

**Do NOT set `adminOnly`** — operators need access to playback footage.

### `PlaybackPage.tsx` component

```tsx
// Props: none (route page).
// State:
//   selectedNvrId: string | null
//   selectedCamId: string | null    // Camera.id
//   selectedDate:  string           // "YYYY-MM-DD" in NVR-local date
//   seekTarget:    number | null    // footage epoch, set by Timeline commit
//   speed:         1 | 2 | 4 | 8
```

**Data sources:**
- `useNvrs()` — existing hook; drives NVR selector.
- `useCameras()` — existing hook; filter to `cam.nvr_id === selectedNvrId && cam.enabled`.
- `useRecordingAvailability(nvrId, channel, month)` — drives day picker disabled-days.
- `useRecordingIndex(nvrId, channel, date)` — passes clips + day bounds to Timeline.

**Layout (high-level; exact Tailwind TBD):**
```
┌── header bar ─────────────────────────────────────────────────────────────┐
│  [NVR ▾]   [Camera ▾]   [Date picker]         [1× 2× 4× 8×]  [📷 snapshot]│
└──────────────────────────────────────────────────────────────────────────┘
┌── player area (flex-1) ───────────────────────────────────────────────────┐
│   <PlaybackPlayer> (Task 14 placeholder: <div>player here</div> for now)  │
└──────────────────────────────────────────────────────────────────────────┘
┌── timeline (fixed height ~80px) ─────────────────────────────────────────┐
│   <Timeline> (Task 13 placeholder for now)                                │
└──────────────────────────────────────────────────────────────────────────┘
```

**NVR selector:** `<select>` (or custom dropdown matching AppShell style) over `nvrs`.
Changing NVR resets camera and date.

**Camera selector:** `<select>` over `cameras.filter(c => c.nvr_id === selectedNvrId && c.enabled)`,
sorted by `channel`.  Shows `cam.display_name` + `ch{cam.channel}`.  No sub-stream toggle
(recording is main-only per spike V4).

**Date picker:** Native `<input type="date">` is acceptable for MVP.  Constraints:
- Min date: derived from `availability.oldest_epoch` (convert to "YYYY-MM-DD" in NVR tz).
- Max date: today in NVR-local tz (derived from `tz_offset_minutes`).
- Days without recordings should be greyed out if the browser supports the `list` datalist
  approach; a note in the UX is sufficient for MVP (the API call will return empty clips,
  the Timeline will show "no coverage").

**Speed selector:** Buttons `1× 2× 4× 8×`.  The active speed is highlighted.  Changing
speed while playing sends `{speed: N}` to the PlaybackPlayer (via prop).

**Snapshot button:** Calls `useSnapshot` (Task 15).  Present but disabled until PlaybackPlayer
is mounted and playing.

**Retention-aware hint:** Show `"Oldest recording: {date}"` under the date picker when
`availability.oldest_epoch` is not null.

**Month navigation for availability:** When the date picker month changes (user arrow-keys
or manual input), fire `useRecordingAvailability` for the new month.  Track `viewMonth`
state (`"YYYY-MM"`) separately from `selectedDate`.

### UX constraints (verbatim from spec §3/§8)
- Day picker: "days-with-recordings / oldest-available hint (retention-aware)."
- No sub-stream toggle; no quality toggle in this page (main-only).
- Distinct `no_coverage` vs `error` states propagated from player.

### Steps

1. Add `FilmIcon` to `components/icons.tsx`.
2. Add route to `App.tsx`.
3. Add nav entry to `AppShell.tsx`.
4. Scaffold `PlaybackPage.tsx` with selectors and placeholder children.
5. Wire `useNvrs`, `useCameras`, `useRecordingAvailability` into selectors.
6. Wire `useRecordingIndex` and pass `indexData` + `seekTarget` + `speed` down as props to
   placeholder children.
7. Manual smoke: navigate to `/playback`, verify the tab appears, selectors render, a
   recorded day shows clips in the index response (check Network tab).

---

## Task 13 — Clip-aware Timeline

**Goal:** Build a 24-hour bar that shades recorded clips, supports a draggable playhead
with commit-on-release seeking, prev/next-clip stepping, gap handling, and keyboard
navigation.

### Files created / modified

| Operation | Path |
|-----------|------|
| Create  | `web-react/src/features/playback/Timeline.tsx` |
| Create  | `web-react/src/features/playback/Timeline.test.ts` |

### Props interface

```tsx
interface TimelineProps {
  /** Day boundaries from RecordingIndex (UTC epoch seconds). */
  dayStartEpoch: number;
  dayEndEpoch: number;

  /** Merged clip spans for the day. */
  clips: RecordingClip[];

  /** NVR timezone offset (minutes, same sign as backend). */
  tzOffsetMinutes: number;

  /** Current playhead position (footage epoch).  Drives the playhead marker. */
  playheadEpoch: number | null;

  /** Fired when the user COMMITS a seek (drag release or click).
   *  Caller debounces or passes through; Timeline does not debounce internally —
   *  it fires once on pointerup, which is the commit event. */
  onSeek: (epoch: number) => void;

  /** Player state — Timeline uses it to hide/show the ghost and handle no_coverage. */
  playerState: PlayerState;

  /** Prev/next-clip stepping: Timeline exposes these for the PlaybackPage toolbar,
   *  but also renders its own prev/next buttons at the bar edges. */
  onPrevClip?: () => void;
  onNextClip?: () => void;
}
```

### Internal state

```ts
type DragState =
  | { dragging: false }
  | { dragging: true; ghostEpoch: number };   // ghost position during drag
```

### Layout & rendering

The bar is a fixed-height `div` (≈56px) with `position: relative; overflow: hidden`.

**Clip segments:** For each clip in `clips`, compute `left%` and `width%` relative to
`[dayStartEpoch, dayEndEpoch)`.  Render a colored `<div>` (continuous recording uses the
accent color; event clips could use amber — for now all are accent since V3 shows continuous).

**Gap regions:** Everything between clips (and before first / after last) is the default
dark background — no explicit element needed.

**Axis:** Render hour labels at 0, 3, 6, 9, 12, 15, 18, 21 hours.  Each label position is
`((nvrHour * 3600) / dayDurationSeconds) * 100 + "%"`.  `dayDurationSeconds =
dayEndEpoch - dayStartEpoch` (handles 23h/25h DST days correctly).  Labels formatted with
`epochToNvrTimeStr` from `playback-utils.ts`.

**Playhead marker:** Absolute vertical line at `epochToPercent(playheadEpoch)`.  Only
shown when `playheadEpoch !== null` and `!dragState.dragging`.

**Ghost playhead:** During drag, show a semi-transparent version at `ghostEpoch`.
Additionally show a thumbnail tooltip (spec: "drag preview uses the thumbnail endpoint" —
see Ambiguity B1 below about the thumbnail endpoint).

**Prev/next clip buttons:** Two small `<button>` elements at the left/right edges of the
bar.  Logic:
```
onPrevClip: find clip whose end_epoch < playheadEpoch, take the last one, seek to its start.
onNextClip: find clip whose start_epoch > playheadEpoch, take the first one, seek to its start.
```
These are also exposed via the `onPrevClip`/`onNextClip` props so PlaybackPage's toolbar
can trigger them.

### Pointer event handling (commit-on-release + drag)

Use `onPointerDown`, `onPointerMove`, `onPointerUp` on the bar container.
Use `setPointerCapture` so drag continues outside the element.

```
onPointerDown(e):
  e.currentTarget.setPointerCapture(e.pointerId)
  dragState = { dragging: true, ghostEpoch: percentToEpoch(e) }

onPointerMove(e):
  if dragState.dragging:
    dragState.ghostEpoch = percentToEpoch(e)
    // No seek fired yet — ghost only

onPointerUp(e):
  if dragState.dragging:
    const epoch = snapToNearest(clips, ghostEpoch)
    if epoch !== null:
      onSeek(epoch)
    else:
      // ghost fell past all clips — fire onSeek with last clip end or show toast
    dragState = { dragging: false }
```

The spec says "commit-on-release + 250ms debounce" — the 250ms debounce is applied in
**`PlaybackPage`** on the `onSeek` callback (because the debounce gates the WS message,
not the UI update).  Timeline itself fires once on pointerup (no internal debounce).

**Gap snap behavior (from spec §8):** "scrub release into a gap → snap to nearest covered
time (toast)."  Use `snapToNearest` from `playback-utils.ts`.  If the snapped epoch
differs from the drag position, display a small toast or inline "snapped" label.

### Keyboard navigation (`role="slider"`)

The Timeline bar element:
```tsx
<div
  role="slider"
  aria-valuemin={dayStartEpoch}
  aria-valuemax={dayEndEpoch}
  aria-valuenow={playheadEpoch ?? dayStartEpoch}
  aria-valuetext={playheadEpoch ? epochToNvrTimeStr(playheadEpoch, tzOffsetMinutes) : "no position"}
  tabIndex={0}
  onKeyDown={handleKeyDown}
>
```

Key bindings:
- `ArrowLeft` / `ArrowRight` → move playhead ±10 seconds, call `onSeek`.
- `Home` → seek to first clip start.
- `End` → seek to last clip end (or last clip start if end is ambiguous).
- `PageUp/PageDown` → prev/next clip.

### Unit tests for the pure logic helpers (Timeline.test.ts)

Tests for `snapToNearest`, `findClipAt`, `epochToNvrTimeStr`, and `dayToEpochs` should
live in `playback-utils.test.ts` (Task 11).  `Timeline.test.ts` covers the derived
percentage math:

```ts
// percentToEpoch and epochToPercent round-trip
// epochToPercent(midpoint of clip) returns ~50% for a clip spanning the whole day
// Axis label positions for a 24-hour day at UTC+0
```

These are pure functions exported from `Timeline.tsx` for testability (or from a helper).

### Steps

1. Write `Timeline.test.ts` with pure-function tests.
2. Extract and export `epochToPercent(epoch, dayStart, dayEnd)` and
   `percentToEpoch(pct, dayStart, dayEnd)` from `Timeline.tsx`.
3. Implement `Timeline.tsx` with clip rendering, axis, playhead.
4. Add pointer-event drag logic.
5. Add keyboard `role="slider"` handling.
6. `npm test` must pass for Timeline unit tests.
7. Manual: render Timeline in PlaybackPage with real index data; verify drag commits
   seek, ghost shows during drag, prev/next clip buttons work.

### UX constraints (verbatim from spec)
- "Commit-on-release seek" — no intermediate seeks during drag.
- "250ms debounce" — applied by PlaybackPage on the `onSeek` prop.
- "Ghost playhead during drag."
- "Thumbnail preview on drag NOT live decode" — see Ambiguity B1.
- "`role=slider` + keyboard."
- "Axis in NVR-local time."
- "Distinct `no_coverage` vs `error` states" — Timeline disables drag when `playerState === "error"`.
- "Gap snap/auto-skip."

### Ambiguities

**Ambiguity B1 — Thumbnail endpoint:** Spec §3 says "drag preview uses the thumbnail
endpoint, never the live decode" and the architecture diagram shows `GET /thumb`.  The
spec §4 also references `GET .../thumb`.  But Phase-1 only implemented `/index` and
`/availability` — `/thumb` is **not yet in `playback.py`** (it appears only in the
architecture sketch).  The thumbnail preview is therefore **deferred for this task**:
- During drag, show the ghost playhead and the time label (`epochToNvrTimeStr`) as text.
- Do NOT attempt live decode.
- Thumbnail image previews can be added in a fast-follow once Phase-2 `/thumb` is available.
Flag this clearly in a `// TODO(Phase-3 follow-up): thumbnail endpoint` comment.

**Ambiguity B2 — Timeline height and position:** Spec says "fixed height ~80px" in §3
but does not spec the exact layout breakpoint between player and timeline.  Use `h-16`
(64px) to `h-20` (80px) and leave adjustable via Tailwind.

---

## Task 14 — Purpose-built PlaybackPlayer (MSE)

**Goal:** Build a VOD-only MSE player component that owns its own `MediaSource` and a
persistent WebSocket.  Must NOT reuse `dss-mse`/`VideoRTC`/`WebCodecsEngine`.  Reuses
only the `ondata → appendBuffer` queue pattern documented in the grounding section.

### Files created / modified

| Operation | Path |
|-----------|------|
| Create  | `web-react/src/features/playback/PlaybackPlayer.tsx` |
| Create  | `web-react/src/features/playback/usePlaybackSession.ts` |
| Create  | `web-react/src/features/playback/playback-utils.test.ts` | (extended from Task 11)

### WS URL derivation

```ts
// In usePlaybackSession.ts (or playback-utils.ts):
export function buildPlaybackWsUrl(
  nvrId: string,
  channel: number,
  token: string,
): string {
  // Replace http:// with ws:// and https:// with wss://
  const wsBase = CONFIG.backendBase.replace(/^http/, "ws");
  return `${wsBase}/playback/${nvrId}/${channel}/stream?token=${encodeURIComponent(token)}`;
}
```

With Caddy on `:8443` (HTTPS), this becomes:
`wss://10.10.1.152:8443/api/v1/playback/{nvrId}/{channel}/stream?token=...`

Caddy routes `/api/*` → backend, so this hits the FastAPI WS endpoint directly.

### `usePlaybackSession` hook

```ts
interface PlaybackSessionOptions {
  nvrId: string;
  channel: number;
  /** Initial seek target (footage epoch).  Sent as {seek:N} after WS open. */
  initialSeek: number;
  /** Called when a typed JSON signal arrives from the server. */
  onSignal: (msg: ServerMsg) => void;
  /** Called when a binary fMP4 fragment arrives. */
  onData: (data: ArrayBuffer) => void;
  /** Called on unrecoverable WS close. */
  onClose: () => void;
}

interface PlaybackSession {
  send: (msg: ClientMsg) => void;
  close: () => void;
}

export function usePlaybackSession(
  opts: PlaybackSessionOptions | null,  // null = not connected
): PlaybackSession | null
```

Implementation notes:
- Opens `new WebSocket(buildPlaybackWsUrl(nvrId, channel, getToken() ?? ""))`.
- `ws.binaryType = "arraybuffer"`.
- On open: send `{ seek: initialSeek }`.
- On message: if `typeof ev.data === "string"` → parse JSON → `opts.onSignal()`; else → `opts.onData(ev.data)`.
- On close (unexpected): call `opts.onClose()`.
- Keepalive: `setInterval(() => send({ keepalive: true }), 30_000)` (30s, under Caddy's idle timeout).
- Teardown (`close()`): clear keepalive interval, `ws.close()`.
- Re-creation (when `opts` changes identity): destroy old session, create new.

### `PlaybackPlayer.tsx`

```tsx
interface PlaybackPlayerProps {
  nvrId: string;
  channel: number;
  /** The footage epoch the player should start from. When this changes the player
   *  sends a seek message (via usePlaybackSession). */
  seekTarget: number | null;
  /** Playback speed (backend-owned). When changed, sends {speed:N}. */
  speed: 1 | 2 | 4 | 8;
  /** For snapshot: ref to the <video> element. */
  videoRef?: React.RefObject<HTMLVideoElement>;
  /** Notifies parent of state changes (for disabling snapshot button, etc.). */
  onStateChange?: (state: PlayerState) => void;
  /** Notifies parent of current playhead position (for Timeline). */
  onPlayhead?: (epoch: number) => void;
}
```

**Internal state:**
```ts
const [playerState, setPlayerState] = useState<PlayerState>("loading");
const [anchor, setAnchor] = useState<FootageAnchor | null>(null);
const msRef = useRef<MediaSource | null>(null);
const sbRef = useRef<SourceBuffer | null>(null);
const pendingRef = useRef<ArrayBuffer | null>(null);   // appendBuffer queue
```

**MSE lifecycle:**

`rebuildMse(codec: string)` — called on each `init` or `reinit` signal:
1. Revoke old `URL.createObjectURL` if any.
2. `const ms = new MediaSource()`.
3. `video.src = URL.createObjectURL(ms)`.
4. On `ms.sourceopen`: `const sb = ms.addSourceBuffer(codec); sb.mode = "segments"`.
5. Attach `updateend` listener (appendBuffer queue drain).
6. `msRef.current = ms; sbRef.current = sb`.

`appendData(data: ArrayBuffer)`:
```ts
const sb = sbRef.current;
if (!sb || !msRef.current || msRef.current.readyState !== "open") return;
if (sb.updating) { pendingRef.current = data; return; }
try {
  sb.appendBuffer(data);
} catch (e) {
  if (e instanceof DOMException && e.name === "QuotaExceededError") {
    trimBuffer(sb, video.currentTime);   // remove oldest 30s, then retry
  } else {
    setPlayerState("error");
  }
}
```

`trimBuffer(sb, ct)`:
- Remove buffered ranges older than `ct - 30` seconds.
- On `updateend` of the remove, retry the last pending data.

**Signal handling:**
```
init  → rebuild MSE with new codec, capture anchor { t0, baseCt: video.currentTime, speed }
         video.play(), setPlayerState("loading")
reinit → rebuild MSE, capture anchor, setPlayerState("seeking" → "loading")
clock  → update anchor.t0 with heartbeat (see Ambiguity A1)
eof    → setPlayerState("end") if no next clip; setPlayerState("no_coverage") if in gap
gap    → if gap.next: auto-skip seek; if gap.next === null: setPlayerState("end") or "no_coverage"
error  → setPlayerState("error")
```

**Playhead ticking:** `useEffect` with `requestAnimationFrame` loop while `playerState === "playing"`:
```ts
const tick = () => {
  if (anchor && videoRef.current) {
    const epoch = footageEpoch(anchor, videoRef.current.currentTime);
    onPlayhead?.(epoch);
  }
  rafRef.current = requestAnimationFrame(tick);
};
```
Cancel RAF on unmount and on pause/seek/error.

**Speed changes:** When `speed` prop changes and `playerState !== "loading"`:
```ts
session.send({ speed: newSpeed });
setAnchor(null); // wait for reinit to refresh anchor
setPlayerState("seeking");
```

**Audio muting (spec §4/§5):**
```ts
useEffect(() => {
  if (videoRef.current) videoRef.current.muted = speed > 1;
}, [speed]);
```
Always mute when speed > 1; restore when back to 1×.

**`playbackRate` invariant:** Never set `video.playbackRate` to anything other than 1.0.
The component may explicitly set it to 1.0 on `init`/`reinit` as a guard.

**Seek prop handling:** `useEffect` on `seekTarget`:
```ts
if (seekTarget !== null && session) {
  session.send({ seek: seekTarget });
  setPlayerState("seeking");
}
```

**Pause/play UI buttons:** `onPause` → `session.send({ pause: true })` + `video.pause()`;
`onPlay` → `session.send({ play: true })` + `video.play()`.
`±10s skip` → compute `footageEpoch(anchor, video.currentTime) ± 10`, call `session.send({ seek: epoch })`.

**State machine (summary):**

```
"loading"     → wait for init + first data append + video.play() resolves → "playing"
"playing"     → pause button → send {pause} + video.pause() → "paused"
"paused"      → play button  → send {play}  + video.play()  → "playing"
"playing"     → seek committed → send {seek} → "seeking"
"seeking"     → reinit received → rebuildMse → "loading"
"loading"     → init received + data arriving → "playing"
"playing"     → gap.next !== null → auto-skip (send seek) → "seeking"
"playing"     → eof or gap.next === null after last clip → "end"
"playing"     → {type:"error"} or WS closed unexpectedly → "error"
"error"       → terminal; user must click retry (re-mount or re-seek)
"no_coverage" → shown when gap with no next clip on an otherwise covered day
```

**Status overlays (per spec §8):**
- `"loading"` + `"seeking"` → spinner (same pattern as `MsePlayer.tsx` "connecting" badge).
- `"error"` → error badge + "Retry" button.
- `"no_coverage"` → distinct "No footage" badge (neutral, not red error).
- `"end"` → "End of recording" indicator.
- `"paused"` → play overlay (standard video pause indicator).

### Steps

1. Extend `playback-utils.test.ts` with `buildPlaybackWsUrl` tests:
   - HTTPS origin → `wss://` prefix
   - HTTP origin → `ws://` prefix
   - Token encoded correctly
2. Implement `buildPlaybackWsUrl` in `playback-utils.ts`.
3. Implement `usePlaybackSession.ts` (no MSE, pure WS lifecycle).
4. Implement `PlaybackPlayer.tsx` — MSE lifecycle, signal handling, state machine.
5. Connect `PlaybackPlayer` into `PlaybackPage.tsx` (replace placeholder).
6. `npm test` must pass.
7. **Manual smoke tests (on-network, per spec §10):**
   - Open /playback, select camera, pick a recorded day, confirm video plays.
   - Seek by dragging Timeline → confirm video jumps to new time, playhead updates.
   - Pause/play → confirm video pauses and resumes.
   - Speed 2× → confirm audio mutes, video advances faster (server-side).
   - Let video reach clip end → confirm `"end"` state shown.
   - 5-minute idle pause → confirm WS stays alive through Caddy idle timeout (keepalive pings).

### UX constraints
- "`playbackRate` stays 1.0" (spec §4/§5) — never touch `video.playbackRate`.
- "Audio muted when speed > 1" (spec §4).
- "Rebuilds the SourceBuffer on `reinit`" (spec §3).
- "VOD buffer management (trim a wide window around `currentTime`, handle `QuotaExceededError`)" (spec §3).
- "No auto-reconnect" — reconnect = explicit user seek.
- "State machine driven by backend signals, not `currentTime` stopped" (spec §3).
- "One persistent WS; controls are messages" (spec §3).

### Ambiguities

**Ambiguity C1 — `no_coverage` trigger:** Spec says both "gap/eof → typed signal → snap to
next clip / `end of recording` state" (§4) and "no coverage" as a distinct state (§3/§8).
It is unclear whether `no_coverage` is: (a) a frontend state when the index returns zero
clips for the selected day, or (b) triggered by a backend signal.  Most likely (a) — if
`clips.length === 0` from `/index`, PlaybackPage never opens the WS and just shows
"no_coverage" without ever entering `"loading"`.  If (b), the backend sends
`{type:"error", reason:"no_coverage"}` or a `{type:"gap", next:null}` immediately.
**Recommendation:** treat `clips.length === 0` in PlaybackPage as `no_coverage` (never
open WS); treat `gap.next === null` at runtime as `"end"`.  Flag for Phase-2.

**Ambiguity C2 — `QuotaExceededError` retry:** After trimming old buffer, the spec says
"trim oldest ranges" then retry.  The retry may itself throw if the data is too large.
Implement a single retry; if still throwing, enter `"error"` state.

---

## Task 15 — Snapshot (client-side PNG)

**Goal:** Implement `useSnapshot`, which draws the current `<video>` frame to a canvas and
triggers a PNG download named with the NVR-local footage timestamp.

### Files created / modified

| Operation | Path |
|-----------|------|
| Create  | `web-react/src/features/playback/useSnapshot.ts` |

### Hook signature

```ts
/**
 * Returns a `takeSnapshot` function.  When called:
 *   1. Draws videoEl.currentTime frame to a hidden canvas.
 *   2. canvas.toBlob("image/png") → download as
 *      "snapshot_{camDisplayName}_{NVR-local-datetime}.png"
 *   3. Returns the blob URL (for optional preview) or null on failure.
 */
export function useSnapshot(
  videoRef: React.RefObject<HTMLVideoElement>,
  anchor: FootageAnchor | null,
  tzOffsetMinutes: number,
  camName: string,
): {
  takeSnapshot: () => Promise<string | null>;
  isAvailable: boolean;   // false if video not playing or no anchor
}
```

### Implementation

```ts
async function takeSnapshot(): Promise<string | null> {
  const video = videoRef.current;
  if (!video || video.readyState < 2 || !anchor) return null;

  const canvas = document.createElement("canvas");
  canvas.width  = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.drawImage(video, 0, 0);

  const footageTs = footageEpoch(anchor, video.currentTime);
  const localStr  = formatNvrDatetime(footageTs, tzOffsetMinutes);  // "YYYY-MM-DD_HH-MM-SS"
  const filename  = `snapshot_${camName.replace(/[^a-z0-9]/gi, "_")}_${localStr}.png`;

  return new Promise((resolve) => {
    canvas.toBlob((blob) => {
      if (!blob) { resolve(null); return; }
      const url = URL.createObjectURL(blob);
      const a   = document.createElement("a");
      a.href     = url;
      a.download = filename;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      resolve(url);
    }, "image/png");
  });
}
```

**`formatNvrDatetime(epoch, tzOffset):`**
```ts
export function formatNvrDatetime(epoch: number, tzOffsetMinutes: number): string {
  const localMs = (epoch + tzOffsetMinutes * 60) * 1000;
  const d = new Date(localMs);
  // Format as YYYY-MM-DD_HH-MM-SS using UTC getters (since we manually offset):
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth()+1)}-${pad(d.getUTCDate())}` +
         `_${pad(d.getUTCHours())}-${pad(d.getUTCMinutes())}-${pad(d.getUTCSeconds())}`;
}
```

Add `formatNvrDatetime` to `playback-utils.ts` and test it.

**`isAvailable`:**
```ts
const isAvailable = !!(
  videoRef.current &&
  videoRef.current.readyState >= 2 &&
  anchor
);
```

### Connection to PlaybackPage

The snapshot button in `PlaybackPage.tsx` toolbar:
```tsx
const { takeSnapshot, isAvailable } = useSnapshot(videoRef, anchor, tzOffsetMinutes, cam.display_name);
// ...
<button disabled={!isAvailable} onClick={() => void takeSnapshot()}>
  Snapshot
</button>
```

`videoRef` is a `useRef<HTMLVideoElement>` created in PlaybackPage and passed to
`PlaybackPlayer` via the `videoRef` prop.  `anchor` is lifted to PlaybackPage state
(exposed via `onAnchorChange` callback from PlaybackPlayer).

### Unit tests

Add to `playback-utils.test.ts`:
- `formatNvrDatetime(1751241600, 0)` → `"2026-06-30_00-00-00"` (UTC epoch for 2026-06-30T00:00:00Z)
- `formatNvrDatetime(1751241600, 300)` → `"2026-06-30_05-00-00"` (UTC+5)
- `formatNvrDatetime(1751241600, -360)` → `"2026-06-29_18-00-00"` (UTC-6)

### Manual test (per spec §10)
- "Snapshot correctness" — play a short clip, click snapshot, verify PNG opens with the
  correct frame and the filename contains the right NVR-local timestamp.

### UX constraints
- Filename includes NVR-local timestamp (not browser-local clock).
- Button disabled when `!isAvailable`.
- Audio presence irrelevant (snapshot is video frame only).

---

## Cross-cutting integration summary

### API / WS URLs (all through Caddy `:8443` in prod)
| Endpoint | URL pattern |
|----------|-------------|
| Recording index | `GET {CONFIG.backendBase}/playback/{nvrId}/{channel}/index?date=YYYY-MM-DD` |
| Availability | `GET {CONFIG.backendBase}/playback/{nvrId}/{channel}/availability?month=YYYY-MM` |
| Playback WS | `buildPlaybackWsUrl(nvrId, channel, token)` → `wss://…/api/v1/playback/{nvrId}/{channel}/stream?token=…` |

### Auth
- HTTP endpoints: `Authorization: Bearer {token}` via `http.get()` (existing pattern).
- WS: token as `?token=` query param (spec §7: "query param or Sec-WebSocket-Protocol").
  `getToken()` from `@/api/client`.

### Test runner
- **Vitest** (`npm test`).  jsdom environment, globals.  `@testing-library/react` available.
- Pure-logic tests (`.test.ts`) do not need `@testing-library/react`.
- DOM/MSE/WS tests are impractical in jsdom (no real MediaSource, no real WebSocket).
  Mark as `// manual` per spec §10 and test manually on-network.

### Dependency on Phase-1 and Phase-2
- Phase-1 done: `/index` and `/availability` endpoints exist in `backend/app/routers/playback.py`.
- Phase-2 NOT done: WS `/stream` endpoint and `PlaybackSession` service not yet built.
  `usePlaybackSession` and `PlaybackPlayer` can be scaffolded and unit-tested for state
  management, but the manual smoke tests require Phase-2 to be running.
- Thumbnail endpoint (`/thumb`) is architecture-planned but not in Phase-1.
  Timeline drag preview falls back to text time label (see Ambiguity B1).

---

## All ambiguities (consolidated)

| # | Location | Spec text | Issue | Recommended resolution |
|---|----------|-----------|-------|------------------------|
| A1 | Task 11 / PlaybackPlayer | `{type:"clock", wall_ts}` "playhead resync" | Is `wall_ts` the current footage epoch (so `playhead = wall_ts`) or a wall-clock monotonic? | Assume footage epoch; confirm with Phase-2 backend implementer before wiring |
| A2 | Task 11 / types.ts | `{stream:"sub"\|"main"}` in client→server protocol | Sub is never recorded (spike V4); this control is meaningless.  Should the frontend omit it entirely or send `{stream:"main"}` on connect? | Omit sub toggle from UI; always send `{stream:"main"}` on WS open or omit it (backend ignores it) |
| B1 | Task 13 / Timeline | "thumbnail preview on drag NOT live decode" | `/thumb` endpoint not in Phase-1; not yet buildable | Show time string label during drag; add thumbnail in fast-follow when Phase-2 /thumb lands |
| B2 | Task 13 / Timeline | Timeline height unspecified | 56–80px range mentioned | Use `h-16` (64px) as default; adjust in review |
| C1 | Task 14 / PlayerState | `no_coverage` trigger | From zero clips in /index response, or from WS signal? | Zero clips → never open WS → show `no_coverage` in PlaybackPage; `gap.next===null` at runtime → `"end"` |
| C2 | Task 14 / QuotaExceededError | "trim oldest ranges" | Single retry or multiple? | Single retry; still failing → `"error"` state |
