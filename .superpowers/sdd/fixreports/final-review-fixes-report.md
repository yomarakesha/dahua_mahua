# Final Review Fixes — NVR Playback Branch

**Date:** 2026-06-30  
**Branch (worktree):** worktree-agent-a30cbdcc28633b0d8 (based on feat/nvr-playback-stream)

---

## CRITICAL 1 — Frontend `t` param (Contract #2)

**Problem:** `buildPlaybackWsUrl` produced `.../stream?token=...` with no `t=` param. The backend closes with code 4004 if `t` is absent.

**Fixes applied:**
- `web-react/src/features/playback/playback-utils.ts`: Added required `initialSeek: number` param to `buildPlaybackWsUrl`; URL now appends `&t=${initialSeek}`.
- `web-react/src/features/playback/usePlaybackSession.ts`: Passes `opts?.initialSeek ?? 0` as the 4th arg to `buildPlaybackWsUrl`.

**Tests updated/added (`playback-utils.test.ts`):**
- Updated all 3 existing `buildPlaybackWsUrl` call sites to pass the epoch arg.
- Added 2 new tests: `includes t=<initialSeek>` and `t= appears after token= in query string`.

---

## IMPORTANT 2 — Full MIME for codec + `InitMsg.audio` optional

**Problem:** Backend emitted bare `"avc1.42E01E"` in `init.codec`; `MediaSource.addSourceBuffer()` requires the full MIME type `video/mp4; codecs="avc1.42E01E"`. `InitMsg.audio` was required (`boolean`) but the backend never sends it in the current MVP.

**Fixes applied:**
- `backend/app/routers/playback.py`: Changed `_INIT_CODEC = "avc1.42E01E"` to `_INIT_CODEC = 'video/mp4; codecs="avc1.42E01E"'`.
- `web-react/src/features/playback/types.ts`: Changed `audio: boolean` to `audio?: boolean` in `InitMsg`.
- `docs/superpowers/plans/2026-06-30-nvr-playback-contracts.md`: Updated Contract #14 to document the full MIME requirement.

**Test impact:** The WS protocol tests import `_INIT_CODEC` directly so they automatically pick up the new full-MIME value — no test changes needed.

---

## IMPORTANT 3 — Snapshot stderr credential leak (Contract #12)

**Problem:** `snapshot.py` embedded raw ffmpeg stderr (which can contain `rtsp://user:pw@host…`) directly into `SnapshotError`, leaking credentials into logs.

**Fixes applied:**
- `backend/app/services/playback/snapshot.py`:
  - Added `from app.services.playback.session import _redact_url`.
  - Applied `_redact_url()` to stderr text before embedding in `SnapshotError`.
  - Also removed dead `timeout_frames` parameter from `build_snapshot_argv` (see MUST-FIX below).

**Test added (`test_playback_snapshot.py`):**
- `test_grab_frame_stderr_credentials_are_redacted_in_snapshot_error`: stderr with `rtsp://admin:secret@host…` → SnapshotError text contains `***`, not `secret`.

---

## IMPORTANT 4 — Egress-task death monitoring (busy-loop + budget-slot leak)

**Problem:** `_control_loop` waited only on `{recv, clock, frag}`. If `egress` died with the bounded queue full, `_emit_structural(outbound, _EGRESS_STOP)` spun forever on `asyncio.sleep(0.005)`, stalling the WS coroutine and preventing `finally` (budget release) from running → permanent per-NVR slot leak.

**Fixes applied in `backend/app/routers/playback.py`:**
- **`_emit_structural`**: Added optional `egress: asyncio.Task | None = None` keyword parameter. On every spin, checks `egress.done()` (exits immediately if egress is dead). Also added `_max_spins = 400` (~2 s) hard cap as a final safety valve.
- **`_control_loop`**: Changed `asyncio.wait(producers, ...)` to `asyncio.wait({*producers, egress}, ...)` so an egress crash triggers early teardown. Cancels only producer tasks (not egress) in the `pending` set. Passes `egress=egress` to `_emit_structural(outbound, _EGRESS_STOP)`. Skips egress in the "surface exception" loop (its crash silently tears the loop; endpoint `finally` owns all cleanup). The `finally` block always runs.

**Test added (`test_playback_ws_protocol.py`):**
- `test_emit_structural_returns_quickly_when_egress_is_dead`: Full queue + already-done egress task → `_emit_structural` completes within 0.5 s, does not hang.

---

## MUST-FIX Minors

| Item | File | Change |
|------|------|--------|
| Dead `import re` | `backend/app/services/playback/url_builder.py` | Removed |
| Dead `playback_rtsp_default_port` | `backend/app/settings.py` | Removed field; replaced with a comment pointing to `nvr.port` |
| Dead `timeout_frames` param | `backend/app/services/playback/snapshot.py` | Removed from `build_snapshot_argv` signature and docstring |
| `_log_event` in `__all__` | `backend/app/services/playback/session.py` | Removed from `__all__` (function itself kept; used internally) |

---

## Test Results

**Backend (pure unit tests — 144 tests):** All PASSED  
`tests/test_playback_snapshot.py`, `test_playback_ws_protocol.py`, `test_playback_session_unit.py`, `test_playback_url_builder.py`, `test_playback_nvr_budget.py`, `test_playback_index_parser.py`, `test_playback_media_find.py`

**Pre-existing failures (11, unrelated):** NVR_SECRET_KEY environment not set — identical failures on the original `feat/nvr-playback-stream` branch. Not introduced by this diff.

**Frontend Vitest (130 tests):** All PASSED

**TypeScript typecheck:** Clean (0 errors)

---

## Files Changed

### Backend
- `backend/app/routers/playback.py` — `_INIT_CODEC` full MIME, `_emit_structural` dead-drainer guard, `_control_loop` egress in wait set
- `backend/app/services/playback/snapshot.py` — `_redact_url` import, stderr redaction, remove `timeout_frames`
- `backend/app/services/playback/session.py` — remove `_log_event` from `__all__`
- `backend/app/services/playback/url_builder.py` — remove dead `import re`
- `backend/app/settings.py` — remove `playback_rtsp_default_port`
- `backend/tests/test_playback_snapshot.py` — credential-redaction test
- `backend/tests/test_playback_ws_protocol.py` — dead-drainer `_emit_structural` test

### Frontend
- `web-react/src/features/playback/playback-utils.ts` — `initialSeek` param + `&t=` in URL
- `web-react/src/features/playback/usePlaybackSession.ts` — pass `opts?.initialSeek ?? 0` to URL builder
- `web-react/src/features/playback/types.ts` — `audio?: boolean` in `InitMsg`
- `web-react/src/features/playback/playback-utils.test.ts` — update existing tests + 2 new `t=` assertions

### Docs
- `docs/superpowers/plans/2026-06-30-nvr-playback-contracts.md` — Contract #14 updated for full MIME
