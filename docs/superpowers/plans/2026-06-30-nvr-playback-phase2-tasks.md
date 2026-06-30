# Phase 2 Backend Playback — TDD Task Specs

**Branch:** `feat/nvr-playback`  
**Produced:** 2026-06-30  
**Source of truth:** `docs/superpowers/specs/2026-06-30-nvr-playback-design.md`  
**Executor note:** These tasks are ordered — Task N+1 may depend on artifacts from Task N.

---

## Grounding: Real interfaces found

Before any task instructions, these are the exact signatures and patterns the specs
are grounded in (read from the actual files — do not invent alternatives).

### Auth & deps (`backend/app/deps.py`)
```python
CurrentUser = Annotated[User, Depends(get_current_user)]
# get_current_user: extracts Bearer token from Authorization header, calls decode_token, loads User

def user_can_access_nvr(user: User, nvr: Nvr) -> bool:
    # admin → True; nvr.region_id None → False; operator: region_id in user.regions

def user_can_access_camera(user: User, camera: Camera) -> bool:
    # admin → True; operator: camera.id in user.cameras
```

**WS auth gap:** `CurrentUser` relies on the `Authorization` header, which browsers
cannot set on a native WebSocket. The WS endpoint must extract the token from a
`?token=` query param (standard pattern for browser WS) and call `decode_token`
directly. See Task 8.

### Security (`backend/app/security.py`)
```python
def decode_token(token: str) -> dict[str, Any]:
    """Raises jwt.PyJWTError subclasses on failure (expired, invalid)."""
```

### Settings (`backend/app/settings.py`)
Pattern: fields declared in `class Settings(BaseSettings)` with `field: type = default`.
`get_settings()` is `@lru_cache` — override in tests with `monkeypatch.setattr`.
Existing relevant settings:
- `reencode_ffmpeg_bin: str = "ffmpeg"` — also used for playback ffmpeg binary
- `playback_tz_offset_minutes: int = 0`

### Models (`backend/app/models.py`)
```python
class Nvr(Base):
    id: Mapped[str]           # String PK e.g. "nvr01"
    ip: Mapped[str]           # e.g. "192.168.20.15"
    port: Mapped[int]         # RTSP port (default 554) — NOT the HTTP CGI port
    rtsp_username: Mapped[str]
    rtsp_password_encrypted: Mapped[str]   # Fernet-encrypted
    enabled: Mapped[bool]
    region_id: Mapped[uuid.UUID | None]

class Camera(Base):
    id: Mapped[uuid.UUID]
    nvr_id: Mapped[str]
    channel: Mapped[int]      # 1-based
    enabled: Mapped[bool]

class Lockout(Base):
    ip: Mapped[str]           # PK
    banned_at: Mapped[datetime]
    cooldown_seconds: Mapped[int]
```

### Crypto (`backend/app/crypto.py`)
```python
def decrypt_password(token: str) -> str:  # raises RuntimeError on key mismatch
```

### Lockouts (`backend/app/services/lockouts.py`)
```python
async def get_active_lockout(ip: str) -> Lockout | None: ...
async def record_lockout(ip: str, cooldown_seconds: int = 1800) -> None: ...
```

### Phase-1 playback services
```python
# media_find.py
async def find_clips(
    ip: str, port: int, user: str, pw: str,
    *, channel: int, start: datetime, end: datetime, batch: int = 100
) -> list[Clip]:
    # port is HTTP CGI port (80), NOT RTSP port

# index_parser.py
@dataclass(slots=True)
class Clip:
    start: datetime; end: datetime; type: str; stream: str

# playback router helpers (already in backend/app/routers/playback.py)
def nvr_naive_to_epoch(dt: datetime, tz_offset_minutes: int) -> int: ...
def day_to_epochs(date_str: str, tz_offset_minutes: int) -> tuple[int, int]: ...
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
```

### Live ffmpeg pipeline (`backend/app/services/go2rtc_reencode.py`)
The live pipeline calls ffmpeg as a space-delimited string embedded in a go2rtc
`exec:` source (go2rtc splits on spaces, no shell). For playback we use
`asyncio.create_subprocess_exec` directly with a **list** (no shell, no go2rtc).

Key flags from live pipeline that carry over to playback:
- UDP pull: `-rtsp_transport udp`
- Re-encode: `-c:v {vcodec} -force_key_frames expr:gte(t,n_forced*{kf}) -bf 0 -pix_fmt yuv420p`
- Settings accessor: `settings.reencode_ffmpeg_bin`, `settings.reencode_keyframe_seconds`

Playback changes from live:
- Output mux: **fMP4** (`-f mp4 -movflags frag_keyframe+empty_moov+default_base_moof pipe:1`)
  not MPEG-TS (live uses `-f mpegts -`)
- Audio: transcode to AAC (`-c:a aac`) by default (handles G.711/G.726 which MSE
  cannot ingest raw — V7 still unmeasured on the new NVR)
- RTSP URL: `/cam/playback?channel=N&starttime=...&endtime=...` with underscore format

### Subprocess ownership pattern (`backend/app/services/mediamtx_proc.py`)
Uses `subprocess.Popen` + daemon threads for stdout/stderr pump. Playback uses
`asyncio.create_subprocess_exec` with `asyncio.subprocess.PIPE` for stdout (binary
fMP4 stream) and stderr (logged on non-zero exit), drained by asyncio Tasks.

### Test conventions
- `pytest.ini`: `asyncio_mode = auto`, `testpaths = tests`
- `conftest.py`: adds `backend/` to `sys.path` only — no fixtures
- Pattern: `FastAPI()` with no lifespan + `dependency_overrides` for auth and session
- In-memory SQLite via `create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)`
- `pytest_asyncio.fixture(scope="module", autouse=True)` for DB setup
- `TestClient` (sync) for HTTP tests; `monkeypatch.setattr` for network calls
- No integration tests (NVR, ffmpeg) in the test suite — manual/spike checklist only

---

## Settings additions (all tasks)

Add these to `class Settings` in `backend/app/settings.py` under a new
`# ── Playback session ──` section. All have safe no-op or conservative defaults.

```python
# ── Playback session ────────────────────────────────────────────────────────
# Per-NVR concurrent playback session cap. V9 (max concurrent NVR playback
# streams) is still unmeasured — set conservatively until verified on-network.
# Set to 0 to disable the per-NVR budget (unrestricted, not recommended).
playback_nvr_budget: int = 2

# Global cap on concurrent playback ffmpeg processes across all NVRs and users.
# Independent of the per-NVR budget; the first limit hit rejects the session.
playback_global_cap: int = 4

# Seconds a session can be in PAUSED state before the idle reaper closes it.
playback_idle_timeout_seconds: int = 300

# Hard maximum session lifetime (seconds) regardless of activity.
playback_max_lifetime_seconds: int = 3600

# Ring buffer capacity in chunks (each chunk = one fMP4 fragment, typically
# one GOP). When the buffer is full, the oldest chunk is dropped to keep the
# demuxer from blocking on a slow WS client.
playback_ring_buffer_chunks: int = 30

# Server-side heartbeat interval for {type:"clock"} messages (seconds).
playback_clock_interval_seconds: float = 2.0

# Rate-limit: max playback session OPEN attempts per user per minute.
playback_rate_limit_per_minute: int = 10
```

---

## Task 5 — Playback URL builder, datetime formatting, input validators

**Goal:** Pure functions for building the RTSP playback URL (with underscore time
format) and validating all inbound parameters; no network, fully unit-testable.

### Files created/modified
- **NEW** `backend/app/services/playback/url_builder.py`
- **MODIFY** `backend/app/settings.py` (Settings additions above)

### Exact interfaces

```python
# backend/app/services/playback/url_builder.py

import re
from datetime import datetime

__all__ = [
    "PlaybackUrlError",
    "build_playback_url",
    "validate_channel",
    "validate_speed",
    "validate_footage_epoch",
    "SPEED_WHITELIST",
]

SPEED_WHITELIST: frozenset[int] = frozenset({1, 2, 4, 8})
_RTSP_TIME_FMT = "%Y_%m_%d_%H_%M_%S"   # underscore format, VERIFIED spike finding


class PlaybackUrlError(ValueError):
    """Raised when a playback URL cannot be built due to bad inputs."""


def build_playback_url(
    ip: str,
    rtsp_port: int,
    user: str,
    pw: str,
    channel: int,          # 1-based, caller-validated
    start: datetime,       # naive NVR-local datetime
    end: datetime,         # naive NVR-local datetime
) -> str:
    """Build the RTSP playback URL for a Dahua NVR.

    Time format: YYYY_MM_DD_HH_MM_SS (underscores, verified against
    192.168.20.15 on 2026-06-30 — NOT the dash/colon mediaFileFind format).
    Does NOT include &subtype= (NVR ignores it and records main-only).
    Channel is 1-based.

    The URL goes to ffmpeg argv (list, no shell) — special characters in
    user/pw are passed verbatim; the caller must ensure no '@' or ':' in
    the password that would break the URL authority (flag: see ambiguities).
    """
    s = start.strftime(_RTSP_TIME_FMT)
    e = end.strftime(_RTSP_TIME_FMT)
    return (
        f"rtsp://{user}:{pw}@{ip}:{rtsp_port}/cam/playback"
        f"?channel={channel}&starttime={s}&endtime={e}"
    )


def validate_channel(channel: int, max_channel: int = 64) -> int:
    """Raise PlaybackUrlError if channel is out of valid range [1, max_channel]."""
    if not (1 <= channel <= max_channel):
        raise PlaybackUrlError(
            f"channel must be between 1 and {max_channel}, got {channel}"
        )
    return channel


def validate_speed(speed: int) -> int:
    """Raise PlaybackUrlError if speed not in {1, 2, 4, 8}."""
    if speed not in SPEED_WHITELIST:
        raise PlaybackUrlError(
            f"speed must be one of {sorted(SPEED_WHITELIST)}, got {speed}"
        )
    return speed


def validate_footage_epoch(epoch: int | float) -> int:
    """Raise PlaybackUrlError if epoch is not a positive integer-like number."""
    try:
        v = int(epoch)
    except (TypeError, ValueError):
        raise PlaybackUrlError(f"footage_epoch must be an integer, got {epoch!r}")
    if v <= 0:
        raise PlaybackUrlError(f"footage_epoch must be positive, got {v}")
    return v


def epoch_to_nvr_local(epoch: int, tz_offset_minutes: int) -> datetime:
    """Convert a UTC epoch to a naive NVR-local datetime (inverse of nvr_naive_to_epoch).

    This is the pure inverse needed to build the RTSP URL from a seek target.
    The caller has a UTC epoch (from the client's {seek: <epoch>}); the ffmpeg
    URL requires NVR-local naive time.
    """
    from datetime import timezone, timedelta
    utc_dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    local_dt = utc_dt + timedelta(minutes=tz_offset_minutes)
    return local_dt.replace(tzinfo=None)  # strip tz → naive NVR-local
```

### Settings keys to add (no new ones for this task beyond the section above)

`reencode_ffmpeg_bin` and `reencode_keyframe_seconds` are reused from live settings
(no duplication needed).

### WS message shapes (from spec §4, for reference in Tasks 8+)

**Client → server** (parsed as JSON text frames):
```json
{"seek": 1719734400}         // footage_epoch (int, UTC)
{"speed": 2}                 // int in {1,2,4,8}
{"pause": true}              // no payload needed; key presence is the signal
{"play": true}               // resume after pause
{"stream": "main"}           // "sub"|"main" — V4: always "main"; kept for protocol compat
{"keepalive": true}          // client heartbeat; server just resets idle timer
```

**Server → client** (JSON text frames OR binary bytes frames):
```json
// binary: raw fMP4 fragment bytes (sent as bytes WebSocket frame)
{"type": "init",   "t0": 1719734400, "codec": "avc1.42E01E", "audio": true}
{"type": "reinit", "t0": 1719734520}
{"type": "clock",  "wall_ts": 1719734460}
{"type": "eof"}
{"type": "gap",    "next": 1719738000}   // next: epoch of next clip start, or null
{"type": "error",  "reason": "NVR busy — close a live tile"}  // sanitized, no creds
```

`t0` is the UTC footage epoch at the keyframe where ffmpeg actually started decoding
(the keyframe at or before the requested seek point — may differ from `seek` value
by up to one GOP ≈ 0.5s after re-encode).

### TDD steps

**Step 5.1 — URL builder unit tests (write first, all red)**
File: `backend/tests/test_playback_url_builder.py`

Assertions to cover:
1. `build_playback_url("192.168.20.15", 554, "admin", "secret", 1,
   datetime(2026,6,30,16,0,0), datetime(2026,6,30,17,0,0))`
   must return exactly:
   `"rtsp://admin:secret@192.168.20.15:554/cam/playback?channel=1&starttime=2026_06_30_16_00_00&endtime=2026_06_30_17_00_00"`
2. Time format must use **underscores** (not dashes or colons) — this is the spike-verified
   Dahua format. Regex assert: `re.search(r"starttime=\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}", url)`.
3. `channel=5` appears as `channel=5` in the URL.
4. `subtype` must NOT appear anywhere in the URL (V4: main-only recording).
5. `validate_speed(1)` → 1; `validate_speed(2)` → 2; `validate_speed(4)` → 4;
   `validate_speed(8)` → 8.
6. `validate_speed(3)` → raises `PlaybackUrlError`.
7. `validate_channel(1)` → 1; `validate_channel(64)` → 64.
8. `validate_channel(0)` → raises `PlaybackUrlError`.
9. `validate_channel(65)` → raises `PlaybackUrlError`.
10. `validate_footage_epoch(1719734400)` → 1719734400 (int).
11. `validate_footage_epoch(0)` → raises `PlaybackUrlError`.
12. `validate_footage_epoch(-1)` → raises `PlaybackUrlError`.
13. `epoch_to_nvr_local(0, 0)` → `datetime(1970, 1, 1, 0, 0, 0)` (naive).
14. `epoch_to_nvr_local(0, 60)` → `datetime(1970, 1, 1, 1, 0, 0)` (UTC+1 offset).
15. `epoch_to_nvr_local(1751234400, 300)` → verify the offset math is correct
    (parametrize: offset 0, ±60, ±300, ±720).
16. DST note: `epoch_to_nvr_local` uses a fixed integer offset and has no DST
    awareness (by design — NVR tz offset is a fixed constant). Assert this explicitly:
    same offset before/after a hypothetical DST boundary → no change in math.

**Step 5.2 — Implement `url_builder.py`** → all 16 assertions green.

**Step 5.3 — Add settings** to `backend/app/settings.py` under the new section.
No test needed — Settings uses pydantic-settings; the field declarations are
self-documenting and validated at startup.

**Step 5.4 — Commit**
`feat(playback): URL builder + validators + Phase-2 settings`

### Integration points
- `build_playback_url` is called by `PlaybackSession` (Task 7) with
  `nvr.ip`, `nvr.port` (RTSP port — 554 default), decrypted credentials,
  `channel` from the WS route param, and start/end datetimes derived from the
  client's `{seek: epoch}` via `epoch_to_nvr_local`.
- `validate_channel`, `validate_speed`, `validate_footage_epoch` are called in the
  WS control-message handler (Task 8) before any ffmpeg operation.
- `epoch_to_nvr_local` is the inverse of `nvr_naive_to_epoch` already in
  `backend/app/routers/playback.py`; both must use the same offset from
  `settings.playback_tz_offset_minutes`.

### Global constraints (spec §7)
- `{nvr}` resolves to a DB row — no arbitrary host, no SSRF.
- ffmpeg argv is a **list** (no `shell=True`).
- `start`/`end` validated as `datetime` objects before reaching `build_playback_url`
  (validated in Task 8's WS handler from the `{seek}` epoch).
- `speed` whitelisted to `{1, 2, 4, 8}` — anything else raises, never reaches ffmpeg.
- `channel` bounded by an integer range — not taken from user input directly (comes
  from the DB Camera row in Task 8).

### Ambiguities / spec conflicts
None in this task. The underscore time format is spike-verified.

---

## Task 6 — NvrBudget shared semaphore + lockout integration

**Goal:** A module-level per-NVR capacity guard that playback acquires before spawning
ffmpeg, and that reports "NVR busy" immediately (no blocking) when at cap. Also wire
playback auth failures into the existing `lockouts` accounting.

### Files created/modified
- **NEW** `backend/app/services/playback/nvr_budget.py`
- **MODIFY** `backend/app/main.py` (initialise + shutdown in lifespan)

### Exact interfaces

```python
# backend/app/services/playback/nvr_budget.py

from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

__all__ = ["BudgetExhausted", "NvrBudget", "budget"]

class BudgetExhausted(Exception):
    """Raised (never blocks) when a per-NVR or global cap is full."""


class NvrBudget:
    """Shared per-NVR + global semaphore for playback sessions.

    Call init() once at app startup (lifespan). acquire(nvr_id) is a
    non-blocking try: raises BudgetExhausted immediately if at cap.

    Thread/task safety: all methods are safe to call from asyncio tasks.
    The internal state is protected by asyncio Locks (not thread locks);
    do not call from threads.
    """

    def __init__(self, per_nvr: int, global_cap: int) -> None:
        self._per_nvr = per_nvr
        self._global_cap = global_cap
        # Counters rather than asyncio.Semaphore (Semaphore has no try-acquire
        # without accessing private _value). Lock guards reads+writes.
        self._lock = asyncio.Lock()
        self._nvr_counts: dict[str, int] = {}   # nvr_id → active count
        self._global_count: int = 0

    def _nvr_count(self, nvr_id: str) -> int:
        return self._nvr_counts.get(nvr_id, 0)

    async def try_acquire(self, nvr_id: str) -> None:
        """Attempt to acquire one slot. Raises BudgetExhausted if at cap.

        Checks global cap first (cheaper), then per-NVR cap.
        """
        async with self._lock:
            if self._global_count >= self._global_cap:
                raise BudgetExhausted(
                    f"Global playback cap ({self._global_cap}) reached"
                )
            if self._nvr_count(nvr_id) >= self._per_nvr:
                raise BudgetExhausted(
                    f"NVR {nvr_id!r} playback cap ({self._per_nvr}) reached — "
                    "close a live tile or wait for another session to end"
                )
            self._nvr_counts[nvr_id] = self._nvr_count(nvr_id) + 1
            self._global_count += 1

    async def release(self, nvr_id: str) -> None:
        """Release one slot. Safe to call even if acquire failed."""
        async with self._lock:
            c = self._nvr_counts.get(nvr_id, 0)
            if c > 0:
                self._nvr_counts[nvr_id] = c - 1
            if self._global_count > 0:
                self._global_count -= 1

    @asynccontextmanager
    async def session(self, nvr_id: str) -> AsyncIterator[None]:
        """Context manager: acquire on enter, release on exit (even if exception)."""
        await self.try_acquire(nvr_id)
        try:
            yield
        finally:
            await self.release(nvr_id)

    def active_count(self, nvr_id: str) -> int:
        """Current active session count for an NVR. Not lock-protected (read-only for monitoring)."""
        return self._nvr_counts.get(nvr_id, 0)

    def global_active(self) -> int:
        return self._global_count

    def snapshot(self) -> dict[str, int]:
        """Return a copy of {nvr_id: count} for observability. Not locked."""
        return dict(self._nvr_counts)


# Module-level singleton — initialised in lifespan
budget: NvrBudget | None = None


def init_budget(per_nvr: int, global_cap: int) -> NvrBudget:
    global budget
    budget = NvrBudget(per_nvr=per_nvr, global_cap=global_cap)
    return budget


def get_budget() -> NvrBudget:
    if budget is None:
        raise RuntimeError("NvrBudget not initialised — call init_budget() in lifespan")
    return budget
```

**Lifespan wiring in `backend/app/main.py`:**
Inside `async with` lifespan, after existing startup:
```python
# Before yield (startup):
from app.services.playback import nvr_budget as _pb_budget
_pb_budget.init_budget(
    per_nvr=settings.playback_nvr_budget,
    global_cap=settings.playback_global_cap,
)

# After yield (shutdown): nothing needed — in-memory state, no async cleanup.
```

**Lockout integration** (in `PlaybackSession`, Task 7, but spec'd here):
Before spawning ffmpeg for a playback session, check the lockout table:
```python
from app.services import lockouts

lock = await lockouts.get_active_lockout(nvr.ip)
if lock:
    raise BudgetExhausted(
        f"NVR {nvr.ip} is in auth lockout for "
        f"{lockouts.remaining_seconds(lock)}s more"
    )
```
On `httpx.HTTPStatusError` 401 from a future NVR credential pre-check, or on
ffmpeg exiting with an RTSP auth error (exit code non-zero, stderr contains
"401 Unauthorized" or "RTSP auth failed"), call:
```python
await lockouts.record_lockout(nvr.ip, cooldown_seconds=1800)
```
This uses the existing `app/services/lockouts.py` — no new code needed there.

### TDD steps

**Step 6.1 — Unit tests for NvrBudget** (write first, all red)
File: `backend/tests/test_playback_nvr_budget.py`

All async (asyncio_mode=auto):
1. `budget = NvrBudget(per_nvr=2, global_cap=4)` — `global_active()` == 0.
2. Two successful `await budget.try_acquire("nvr01")` calls → `active_count("nvr01")` == 2.
3. Third `await budget.try_acquire("nvr01")` → raises `BudgetExhausted`.
4. After one `await budget.release("nvr01")` → third acquire now succeeds.
5. Four acquires on different NVRs (`nvr01`, `nvr02`, `nvr03`, `nvr04`) reaches
   global cap; fifth raises `BudgetExhausted`.
6. `budget.session("nvr01")` context manager: count increments on enter, decrements on exit.
7. Exception inside `async with budget.session(...)` → count still decrements (no leak).
8. `release()` on a never-acquired nvr_id → no error, count stays 0.
9. `global_active()` tracks the sum across all NVRs.
10. `snapshot()` returns a dict copy (mutation does not affect internal state).

**Step 6.2 — Implement `nvr_budget.py`** → all 10 assertions green.

**Step 6.3 — Lifespan wiring** in `backend/app/main.py` + manual verification:
Restart the dev server; confirm `budget` is not `None` after startup (add a
`log.info("NvrBudget initialised: per_nvr=%d global=%d", ...)` statement).

**Step 6.4 — Commit**
`feat(playback): NvrBudget per-NVR semaphore + lifespan init`

### Integration points
- `get_budget()` called by the WS endpoint (Task 8) via `budget.session(nvr_id)`.
- `lockouts.get_active_lockout` and `lockouts.record_lockout` called from
  `PlaybackSession` (Task 7) — uses the existing `SessionLocal` independently
  (matches existing lockouts module pattern).
- **Known gap:** spec §3 says "both go2rtc reconcile and playback respect [NvrBudget]"
  but `go2rtc_sync.py` has no budget awareness. This is out of scope for Phase 2 —
  flag as a follow-up task. The budget guards only playback for now.

### Global constraints (spec §6)
- Playback is the **lower-priority tenant**: rejected with `BudgetExhausted`, not queued.
- Hard global playback cap independent of per-NVR cap — both checks run.
- `release()` must always be called on session end, even on error (use context manager).

### Ambiguities / spec conflicts
- **V9 still unmeasured**: per-NVR cap defaults to 2. Must be updated after running
  V9 verification on-network. The `playback_nvr_budget` setting makes it configurable
  without code changes.

---

## Task 7 — PlaybackSession ffmpeg subprocess lifecycle + fMP4 drain + back-pressure

**Goal:** A class that owns one ffmpeg process pulling `/cam/playback` over RTSP/UDP,
re-muxing/re-encoding to fMP4 on stdout, draining fragments to an async queue, and
supporting seek/speed/pause/close without orphan processes.

### Files created/modified
- **NEW** `backend/app/services/playback/session.py`

### Exact interfaces

```python
# backend/app/services/playback/session.py

from __future__ import annotations

import asyncio
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator

from app.services.playback.url_builder import (
    build_playback_url,
    epoch_to_nvr_local,
    SPEED_WHITELIST,
)

log = logging.getLogger("dss.playback.session")

__all__ = ["PlaybackSession", "SessionState", "footage_epoch_at"]


class SessionState(str):
    IDLE = "idle"
    LOADING = "loading"
    PLAYING = "playing"
    PAUSED = "paused"
    SEEKING = "seeking"
    CLOSED = "closed"
    ERROR = "error"


@dataclass
class PlaybackSession:
    """Owns one ffmpeg process for a playback session.

    Lifecycle:
      1. Instantiate with NVR credentials + clip bounds.
      2. Call await open(start_epoch) to spawn ffmpeg and begin draining.
      3. Iterate drain_queue() (async generator) to receive fMP4 bytes chunks.
      4. Call seek(epoch), set_speed(speed), pause(), resume() as needed.
      5. Call await close() on WS disconnect or idle timeout — always.

    No orphan ffmpeg: close() kills the process tree and cancels drain task.
    Windows: process is assigned to a Job Object (kill-on-close) on spawn.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    nvr_id: str = ""
    nvr_ip: str = ""
    rtsp_port: int = 554
    rtsp_user: str = ""
    rtsp_pw: str = ""          # decrypted, never logged
    channel: int = 1
    tz_offset_minutes: int = 0

    # Clip end boundary — ffmpeg end time; updated on seek
    clip_end_epoch: int = 0

    # Runtime state
    state: str = SessionState.IDLE
    speed: int = 1
    t0: int = 0                 # footage epoch of current ffmpeg start
    _wall_start: float = 0.0    # monotonic when current ffmpeg started
    _proc: asyncio.subprocess.Process | None = None
    _drain_task: asyncio.Task | None = None
    _stderr_task: asyncio.Task | None = None
    _ring: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=30))
    _started_at: float = field(default_factory=time.monotonic)

    async def open(self, start_epoch: int) -> None:
        """Spawn ffmpeg at start_epoch. Sets t0, emits the fMP4 init segment."""
        ...

    async def seek(self, footage_epoch: int) -> None:
        """Respawn ffmpeg at footage_epoch. Updates t0. Caller sends reinit."""
        ...

    async def set_speed(self, speed: int) -> None:
        """Change speed (debounced respawn). Validates against SPEED_WHITELIST."""
        ...

    async def pause(self) -> None:
        """Kill ffmpeg, keep session alive. State → PAUSED."""
        ...

    async def resume(self, footage_epoch: int) -> None:
        """Respawn ffmpeg from footage_epoch after pause. State → LOADING."""
        ...

    async def close(self) -> None:
        """Terminate ffmpeg, cancel drain task. Safe to call multiple times."""
        ...

    def footage_now(self) -> int:
        """Current footage epoch (UTC) based on wall clock + speed."""
        return footage_epoch_at(self.t0, self._wall_start, self.speed, time.monotonic())

    async def drain_queue(self) -> AsyncIterator[bytes]:
        """Yield fMP4 bytes chunks from the ring buffer until CLOSED or ERROR."""
        while self.state not in (SessionState.CLOSED, SessionState.ERROR):
            try:
                chunk = await asyncio.wait_for(self._ring.get(), timeout=1.0)
                yield chunk
            except asyncio.TimeoutError:
                continue


def footage_epoch_at(t0: int, wall_start: float, speed: int, now_wall: float) -> int:
    """Pure function: current footage epoch given session start state and speed.

    Args:
        t0:         Footage epoch (UTC) at the keyframe where ffmpeg started.
        wall_start: Monotonic time when ffmpeg started (time.monotonic()).
        speed:      Playback speed multiplier (1, 2, 4, 8).
        now_wall:   Current monotonic time.

    Returns:
        UTC epoch seconds of the current footage position.

    This is unit-testable. The WS heartbeat uses it to emit {type:"clock"}.
    """
    return t0 + int((now_wall - wall_start) * speed)
```

**ffmpeg argv builder (internal helper in `session.py`):**
```python
def _build_ffmpeg_argv(
    ffbin: str,
    rtsp_url: str,
    vcodec: str,
    keyframe_seconds: float,
    speed: int,
    maxrate_kbps: int,
) -> list[str]:
    """Build the ffmpeg argv for playback (list, no shell).

    Output: fMP4 on stdout (pipe:1). Audio: transcoded to AAC.
    Speed > 1: I-frame-stride filter drops non-keyframe frames and remaps
    PTS so the output plays at realtime pace on the client (each output
    second covers `speed` seconds of footage).

    Note: The exact -vf filter for speed>1 must be validated during
    integration testing (marked INTEGRATION in TDD steps). The signature
    and structure are specced here; ffmpeg behaviour is not unit-testable.
    """
    argv = [
        ffbin,
        "-nostdin", "-loglevel", "error",
        "-rtsp_transport", "udp",
        "-i", rtsp_url,
    ]
    # Video re-encode
    argv += [
        "-c:v", vcodec,
        "-force_key_frames", f"expr:gte(t,n_forced*{keyframe_seconds})",
        "-bf", "0",
        "-pix_fmt", "yuv420p",
    ]
    if maxrate_kbps > 0:
        argv += ["-maxrate", f"{maxrate_kbps}k", "-bufsize", f"{maxrate_kbps}k"]
    # Speed filter (server-side frame decimation)
    if speed > 1:
        # Select every (speed)th frame, remap PTS to realtime.
        # INTEGRATION NOTE: validate this filter on the real NVR stream.
        # The expression selects I-frames at the re-encoded GOP interval
        # (keyframe_seconds) and remaps timestamps so the client sees
        # continuous realtime media time while each second covers `speed`
        # seconds of footage time.
        argv += ["-vf", f"select=not(mod(n\\,{speed})),setpts=N/(FRAME_RATE*TB)"]
        argv += ["-vsync", "vfr"]
    # Audio: transcode to AAC (handles G.711, G.726 — V7 unmeasured on new NVR)
    argv += ["-c:a", "aac"]
    # fMP4 fragmented output on stdout
    argv += [
        "-f", "mp4",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "pipe:1",
    ]
    return argv
```

**Windows Job Object helper (internal, `session.py`):**
```python
def _assign_job_object(pid: int) -> None:
    """Assign process PID to a Windows Job Object with kill-on-close.

    No-op on non-Windows. Uses ctypes (no pywin32 dep).
    On error: logs a warning and continues — the session still works,
    but orphan ffmpeg on crash is possible.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        import ctypes.wintypes
        PROCESS_ALL_ACCESS = 0x1F0FFF
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION = 9

        kernel32 = ctypes.windll.kernel32
        proc_handle = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        if not proc_handle:
            log.warning("Job Object: OpenProcess(%d) failed", pid)
            return
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            kernel32.CloseHandle(proc_handle)
            log.warning("Job Object: CreateJobObjectW failed")
            return

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.wintypes.DWORD),
                ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                ("PriorityClass", ctypes.wintypes.DWORD),
                ("SchedulingClass", ctypes.wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(f, ctypes.c_uint64) for f in
                        ("ReadOperationCount","WriteOperationCount","OtherOperationCount",
                         "ReadTransferCount","WriteTransferCount","OtherTransferCount")]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(
            job,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        kernel32.AssignProcessToJobObject(job, proc_handle)
        kernel32.CloseHandle(proc_handle)
        # Do NOT close `job` — closing it removes the kill-on-close protection.
        # It leaks intentionally; the OS reclaims it when the Python process exits.
        log.debug("Job Object assigned to ffmpeg PID %d", pid)
    except Exception:  # noqa: BLE001
        log.warning("Job Object assignment failed for PID %d", pid, exc_info=True)
```

**Stderr drain (ring-buffer, logged on non-zero exit):**
```python
async def _drain_stderr(proc: asyncio.subprocess.Process, session_id: str) -> None:
    lines: list[str] = []
    while True:
        line = await proc.stderr.readline()
        if not line:
            break
        lines.append(line.decode(errors="replace").rstrip())
        if len(lines) > 200:
            lines.pop(0)  # ring: keep last 200 lines
    rc = await proc.wait()
    if rc not in (0, -15, -9):  # 0=ok, -15=SIGTERM, -9=SIGKILL
        log.error(
            "playback ffmpeg session=%s exited rc=%d stderr:\n%s",
            session_id, rc, "\n".join(lines[-20:])
        )
    else:
        log.debug("playback ffmpeg session=%s exited rc=%d", session_id, rc)
```

**Idle + max-lifetime reaper** (module-level background task, started in lifespan):
```python
# In session.py
_active_sessions: dict[str, "PlaybackSession"] = {}

async def _reaper_loop(idle_timeout: int, max_lifetime: int) -> None:
    """Background task: close idle and over-age sessions."""
    while True:
        await asyncio.sleep(10)
        now = time.monotonic()
        for sid, sess in list(_active_sessions.items()):
            if sess.state == SessionState.PAUSED:
                if now - sess._paused_at > idle_timeout:
                    log.info("Reaper: closing idle session %s", sid)
                    await sess.close()
            if now - sess._started_at > max_lifetime:
                log.info("Reaper: closing over-age session %s", sid)
                await sess.close()

_reaper_task: asyncio.Task | None = None

def start_reaper(idle_timeout: int, max_lifetime: int) -> None:
    global _reaper_task
    _reaper_task = asyncio.create_task(
        _reaper_loop(idle_timeout, max_lifetime), name="playback-reaper"
    )

async def stop_reaper() -> None:
    global _reaper_task
    if _reaper_task:
        _reaper_task.cancel()
        try:
            await _reaper_task
        except asyncio.CancelledError:
            pass
        _reaper_task = None
```

Add `start_reaper` / `stop_reaper` calls to the lifespan in `main.py`:
```python
# startup (after init_budget):
from app.services.playback import session as _pb_session
_pb_session.start_reaper(
    idle_timeout=settings.playback_idle_timeout_seconds,
    max_lifetime=settings.playback_max_lifetime_seconds,
)

# shutdown (before yield returns):
await _pb_session.stop_reaper()
# close all active sessions
for sess in list(_pb_session._active_sessions.values()):
    await sess.close()
```

### TDD steps

**Step 7.1 — Unit tests for `footage_epoch_at`** (write first, red)
File: `backend/tests/test_playback_session_unit.py`

Assertions (pure function — no mocking):
1. `footage_epoch_at(1000, 0.0, 1, 5.0)` → 1005 (5s elapsed, 1× speed).
2. `footage_epoch_at(1000, 0.0, 2, 5.0)` → 1010 (5s elapsed, 2× speed).
3. `footage_epoch_at(1000, 0.0, 4, 5.0)` → 1020 (5s elapsed, 4× speed).
4. `footage_epoch_at(1000, 0.0, 8, 5.0)` → 1040 (5s elapsed, 8× speed).
5. `footage_epoch_at(0, 0.0, 1, 0.0)` → 0 (no elapsed time).
6. `footage_epoch_at(1000, 100.0, 1, 101.5)` → 1001 (1.5s elapsed, truncated).
7. Parametrize: all `speed` values in `{1, 2, 4, 8}` give `t0 + elapsed * speed`.
8. `footage_epoch_at(t0, start, speed, start)` → `t0` (zero elapsed always).

**Step 7.2 — Unit tests for `_build_ffmpeg_argv`**
1. Speed=1: argv does NOT contain `-vf` or `-vsync`.
2. Speed>1: argv contains `-vf` with `select=not(mod(n,...))` and `-vsync vfr`.
3. `"pipe:1"` is the last element.
4. `-f mp4` is present (NOT `-f mpegts`).
5. `-movflags frag_keyframe+empty_moov+default_base_moof` is present.
6. `-rtsp_transport udp` is present (input flag).
7. `-c:a aac` is present.
8. `ffbin` is the first element.
9. No element contains a space character (no shell-splitting risk).
10. `maxrate_kbps=0` → argv does NOT contain `-maxrate`.
11. `maxrate_kbps=8000` → argv contains `-maxrate 8000k` and `-bufsize 8000k`.

**Step 7.3 — Implement `session.py`** → all unit assertions green.

**Step 7.4 — INTEGRATION (manual, on-network, checklist from spec §10):**
- Spawn a PlaybackSession on `192.168.20.15`, ch1. Verify fMP4 bytes flow on stdout.
- Seek 5 times rapidly (20 scrubs per spec §10) → verify no orphan ffmpeg processes
  remain after all seek() calls (`tasklist | findstr ffmpeg` on Windows server = 0 or 1).
- `close()` → verify process exits within 3s.
- Pause for 6 minutes through Caddy `:8443` → idle reaper closes the session.
- Speed=2: verify footage_now() advances ~2× faster than wall clock.
- Job Object: kill the FastAPI process (Ctrl-C) → verify ffmpeg tree dies with it
  (`tasklist` before and after).
- Budget rejection: open 3 sessions to same NVR with `per_nvr=2` → third is rejected.

**Step 7.5 — Commit**
`feat(playback): PlaybackSession ffmpeg lifecycle + fMP4 drain + Job Object`

### Integration points
- `PlaybackSession` instantiated by the WS handler (Task 8) after `budget.session()`
  and auth are resolved.
- Settings accessed: `settings.reencode_ffmpeg_bin`, `settings.reencode_keyframe_seconds`,
  `settings.main_reencode_maxrate_kbps` (reuse live setting), `settings.playback_ring_buffer_chunks`.
- `_active_sessions` dict feeds the observability endpoint (Task 10).
- `lockouts.get_active_lockout(nvr.ip)` called at the top of `open()` before ffmpeg spawn.
- `lockouts.record_lockout(nvr.ip)` called from `_drain_stderr` if stderr indicates 401.

### Global constraints (spec §6)
- **No orphan ffmpeg**: `close()` must `proc.kill()` and `await proc.wait()` before returning.
  Cancel `_drain_task` and `_stderr_task` in `close()`.
- **Back-pressure**: ring buffer bounded (`maxsize=playback_ring_buffer_chunks`). When full,
  `put_nowait()` raises `asyncio.QueueFull` → the drain task drops the chunk (log at DEBUG).
  **Never** use `await ring.put()` inside the ffmpeg stdout reader — that would block the
  reader and cause ffmpeg's RTSP pipeline to stall.
- **Windows Job Object**: always attempt `_assign_job_object(proc.pid)` on spawn;
  log a warning on failure and continue (session still works).
- **Credential hygiene**: `rtsp_pw` never appears in logs, never in error messages sent
  to clients. The `rtsp_url` with embedded credentials is only used in ffmpeg argv; it
  must not appear in structured log records (redact: replace pw with `***` in any
  logged URL string).

### Ambiguities / spec conflicts
1. **Speed filter**: The exact `-vf select=...` expression for server-side speed is an
   INTEGRATION concern. The unit tests assert the argv structure; correctness must be
   validated against the real NVR stream. See spec §5 note: "V1 whether the NVR honours
   RTSP Scale — still unmeasured."
2. **V7 audio codec**: defaulting to `-c:a aac` is safe (AAC passthrough is a no-op if
   the source is already AAC). Verify during integration.
3. **`setpts` expression**: `N/(FRAME_RATE*TB)` is correct for VFR output with dropped
   frames. If the NVR stream has no constant frame rate, use `setpts=PTS-STARTPTS`
   instead. Validate during integration.

---

## Task 8 — WS /stream endpoint: auth handshake + control protocol

**Goal:** Persistent WebSocket at `/playback/{nvr_id}/{channel}/stream` that validates
JWT before accepting, acquires NvrBudget, owns a PlaybackSession, and dispatches
client control messages while streaming fMP4 and JSON signals.

### Files created/modified
- **MODIFY** `backend/app/routers/playback.py` (add WS endpoint)

### Exact interfaces

```python
# Add to backend/app/routers/playback.py

import asyncio
import json
import time
import uuid as _uuid

import jwt
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.crypto import decrypt_password
from app.security import decode_token
from app.services.playback.nvr_budget import get_budget, BudgetExhausted
from app.services.playback.session import (
    PlaybackSession, SessionState, _active_sessions
)
from app.services.playback.url_builder import (
    validate_channel, validate_speed, validate_footage_epoch
)


@router.websocket("/{nvr_id}/{channel}/stream")
async def playback_stream(
    websocket: WebSocket,
    nvr_id: str,
    channel: int,
    token: str | None = None,   # JWT from ?token= query param
):
    """Persistent playback WebSocket.

    Auth flow (MUST complete before websocket.accept()):
      1. Extract JWT from ?token= query param.
      2. Validate with decode_token(); on failure → close(4001).
      3. Load User from DB; check user_can_access_nvr → close(4003).
      4. Load Nvr row; check nvr.enabled → close(4004).
      5. Check lockout → close(4429) with "NVR busy (lockout)".
      6. Acquire NvrBudget → close(4429) with "NVR busy".
      7. await websocket.accept() — only NOW.
      8. Spawn PlaybackSession; send {type:"init"}.
      9. Enter control loop.

    WebSocket close codes:
      4001 — unauthenticated (bad/missing token)
      4003 — forbidden (region access denied)
      4004 — NVR not found or disabled
      4429 — resource exhausted (NVR busy / lockout / global cap)
    """
    ...
```

**Control loop (pseudocode — implement with actual asyncio):**
```python
async def _control_loop(ws: WebSocket, sess: PlaybackSession) -> None:
    """Read client messages and send server signals concurrently.

    Uses asyncio.gather with two coroutines:
      - _receive_loop: read JSON from WS, dispatch to sess
      - _send_loop: drain sess.drain_queue() + send clock heartbeats
    """
```

**Control message dispatch:**
```python
async def _dispatch(msg: dict, sess: PlaybackSession, ws: WebSocket) -> None:
    """Dispatch one parsed client control message."""
    if "seek" in msg:
        epoch = validate_footage_epoch(msg["seek"])
        await sess.seek(epoch)
        await ws.send_json({"type": "reinit", "t0": sess.t0})
    elif "speed" in msg:
        speed = validate_speed(int(msg["speed"]))
        await sess.set_speed(speed)
        await ws.send_json({"type": "reinit", "t0": sess.t0})
    elif "pause" in msg:
        await sess.pause()
    elif "play" in msg:
        await sess.resume(sess.footage_now())
        await ws.send_json({"type": "reinit", "t0": sess.t0})
    elif "keepalive" in msg:
        sess._last_keepalive = time.monotonic()
    elif "stream" in msg:
        # V4: main-only recording; silently ignore stream switch requests
        log.debug("stream switch requested (%s) — ignored (main-only NVR)", msg["stream"])
    else:
        log.warning("Unknown control message: %r", msg)
```

**Clock heartbeat sender:**
```python
async def _clock_sender(ws: WebSocket, sess: PlaybackSession, interval: float) -> None:
    while sess.state not in (SessionState.CLOSED, SessionState.ERROR):
        await asyncio.sleep(interval)
        if sess.state == SessionState.PLAYING:
            await ws.send_json({"type": "clock", "wall_ts": sess.footage_now()})
```

**fMP4 drain sender:**
```python
async def _fragment_sender(ws: WebSocket, sess: PlaybackSession) -> None:
    async for chunk in sess.drain_queue():
        await ws.send_bytes(chunk)
    await ws.send_json({"type": "eof"})
```

### TDD steps

**Step 8.1 — Unit tests for the control-message state machine**
File: `backend/tests/test_playback_ws_protocol.py`

These tests mock `PlaybackSession` and test `_dispatch` in isolation:
1. `{"seek": 1719734400}` → calls `sess.seek(1719734400)` and sends `{"type":"reinit","t0":...}`.
2. `{"speed": 2}` → calls `sess.set_speed(2)` and sends `{"type":"reinit","t0":...}`.
3. `{"speed": 3}` → raises `PlaybackUrlError` (whitelist violation) before calling `sess`.
4. `{"pause": true}` → calls `sess.pause()`.
5. `{"play": true}` → calls `sess.resume(...)`.
6. `{"keepalive": true}` → updates `sess._last_keepalive` only.
7. `{"stream": "sub"}` → no exception, no sess method called (ignored).
8. `{"unknown": "msg"}` → logs warning, no exception.
9. `{"seek": 0}` → raises `PlaybackUrlError` (epoch must be positive).
10. `{"seek": "not-an-int"}` → raises `PlaybackUrlError`.
11. `footage_epoch_at` math via `sess.footage_now()` at various speeds.
12. After `seek()`, `t0` changes to the new seek epoch.

**Step 8.2 — Integration: WS auth rejection (use TestClient websocket)**
File: `backend/tests/test_playback_ws_auth.py`

FastAPI test pattern for WS: `TestClient.websocket_connect(url)`.
1. No `?token=` param → WS closes with code 4001 before `accept()`.
2. Expired/invalid token → closes with 4001.
3. Valid token for user without NVR access → closes with 4003.
4. Valid admin token, non-existent NVR → closes with 4004.
5. Valid token, NVR at budget cap → closes with 4429.
   (mock `NvrBudget.try_acquire` to raise `BudgetExhausted`)

All these tests require the minimal FastAPI app with playback router and mocked deps,
following the same pattern as `test_playback_router_index.py`.

**Step 8.3 — Implement WS endpoint** → all unit + auth rejection tests pass.

**Step 8.4 — INTEGRATION (manual, on-network checklist):**
- Open WS from browser at `wss://10.10.1.152:8443/api/v1/playback/{nvr_id}/1/stream?token=...`
- Verify fMP4 bytes received (MSE can append them).
- Send `{seek: T}` → `reinit` received, video restarts from keyframe.
- Seek 20 times rapidly → no orphan ffmpeg, player recovers.
- 5-minute pause through Caddy → WS stays open (keepalive), idle reaper closes at 300s.
- `{speed: 2}` → footage advances 2× faster than wall clock.
- Budget rejection: open 3 sessions to same NVR → third WS gets code 4429.
- Gap/EOF: seek past end of recording → `{type:"eof"}` received.

**Step 8.5 — Commit**
`feat(playback): WS /stream endpoint with JWT auth + control protocol`

### Integration points
- `decode_token` from `app.security` — same function as HTTP auth, called directly.
- `user_can_access_nvr` from `app.deps` — same logic as `/index` endpoint.
- `decrypt_password` from `app.crypto` — called once per WS session to get NVR creds.
- `get_budget().session(nvr_id)` — context manager wrapping the entire WS session body.
- `_active_sessions[sess.session_id] = sess` registered immediately after `accept()`,
  removed in `finally` (feeds Task 10 observability).
- `lockouts.get_active_lockout(nvr.ip)` called before `accept()`.

### Global constraints (spec §7)
- JWT validated **before** `websocket.accept()` — no ffmpeg spawned for unauthenticated requests.
- `user_can_access_nvr` enforced — recorded footage is more sensitive than live.
- NVR password never in error messages or WS JSON payloads.
- `{type:"error", "reason": <sanitized>}` sent before WS close on runtime errors.
- Rate-limit: track per-user session open attempts; reject with 4429 if >
  `playback_rate_limit_per_minute` in 60s (use a simple in-memory `dict[user_id, deque[float]]`).
- Audit log: `log.info("playback_start nvr=%s ch=%d user=%s session=%s", ...)` at session
  open; `log.info("playback_stop ... duration=%ds", ...)` at close. These form the
  access trail for recorded footage (spec §7 "Audit").

### Ambiguities / spec conflicts
1. **`{pause}` JSON shape**: Spec §4 uses `{pause}` shorthand. Interpreted as
   `{"pause": true}` (key presence is the discriminator). Same for `{play}`, `{keepalive}`.
   Flag to frontend team.
2. **`{stream}` message**: Spec §4 includes `{stream: "sub"|"main"}`. V4 confirms
   main-only recording — this message is silently ignored (no error, no ffmpeg respawn).
   Keep in the protocol parser for forward compat.
3. **`t0` accuracy**: The `t0` in `{type:"init"}` should be the footage epoch of the
   actual first keyframe decoded, not the requested seek epoch. Since we can't know this
   until ffmpeg actually starts and the fMP4 init segment arrives, `t0` is initially set
   to `start_epoch` and corrected once ffmpeg emits a valid PTS. This is an integration
   concern (V2 GOP measurement would help bound the error).

---

## Task 9 — Snapshot + thumbnail endpoints

**Goal:** `GET /playback/{nvr_id}/{channel}/thumb?at=<epoch>` — runs a one-shot ffmpeg
to extract a single frame from the NVR recording as a JPEG. Used by the timeline drag
preview and the "snapshot" evidence download (client-side canvas capture is preferred for
the active player, but the server endpoint handles drag-preview and cold frames).

### Files created/modified
- **NEW** `backend/app/services/playback/snapshot.py`
- **MODIFY** `backend/app/routers/playback.py` (add `/thumb` endpoint)

### Exact interfaces

```python
# backend/app/services/playback/snapshot.py

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from app.services.playback.url_builder import build_playback_url, epoch_to_nvr_local

log = logging.getLogger("dss.playback.snapshot")

__all__ = ["SnapshotError", "grab_frame", "build_snapshot_argv"]


class SnapshotError(Exception):
    """Raised when ffmpeg fails to extract a frame."""


def build_snapshot_argv(
    ffbin: str,
    rtsp_url: str,
    quality: int = 4,           # JPEG quality (ffmpeg -q:v, 1=best, 31=worst)
    timeout_frames: int = 200,  # abort after this many input frames without output
) -> list[str]:
    """Build ffmpeg argv for single-frame JPEG extraction.

    Output: JPEG bytes on stdout (pipe:1).
    Stops after the first output frame (-frames:v 1).
    Uses TCP transport (snapshot is one-shot; UDP loss → blank frame risk).
    """
    return [
        ffbin,
        "-nostdin", "-loglevel", "error",
        "-rtsp_transport", "tcp",      # TCP for snapshot (one-shot; reliability > speed)
        "-i", rtsp_url,
        "-frames:v", "1",
        "-f", "image2",
        "-vcodec", "mjpeg",
        "-q:v", str(quality),
        "pipe:1",
    ]


async def grab_frame(
    ip: str,
    rtsp_port: int,
    user: str,
    pw: str,
    channel: int,
    footage_epoch: int,
    tz_offset_minutes: int,
    ffbin: str,
    timeout_seconds: float = 15.0,
) -> bytes:
    """Extract one JPEG frame at footage_epoch from the NVR recording.

    Uses a 10-second window (start=epoch, end=epoch+10) so the NVR has a
    target segment to seek into.

    Returns:
        JPEG bytes.

    Raises:
        SnapshotError: ffmpeg failed, timed out, or returned empty output.
    """
    start = epoch_to_nvr_local(footage_epoch, tz_offset_minutes)
    end = epoch_to_nvr_local(footage_epoch + 10, tz_offset_minutes)
    rtsp_url = build_playback_url(ip, rtsp_port, user, pw, channel, start, end)
    argv = build_snapshot_argv(ffbin, rtsp_url)

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        raise SnapshotError(f"Snapshot timed out after {timeout_seconds}s")
    except Exception as exc:
        raise SnapshotError(f"Snapshot process error: {exc}") from exc

    if not stdout:
        rc = proc.returncode
        err_text = stderr.decode(errors="replace")[:500]
        raise SnapshotError(
            f"Snapshot ffmpeg returned empty output (rc={rc}): {err_text}"
        )
    return stdout
```

**Router endpoint (add to `playback.py`):**
```python
@router.get("/{nvr_id}/{channel}/thumb")
async def playback_thumb(
    nvr_id: str,
    channel: int,
    at: int,                   # footage epoch (UTC)
    session: SessionDep,
    user: CurrentUser,
) -> Response:
    """Return a JPEG frame at the given footage epoch.

    Returns: JPEG bytes with Content-Type: image/jpeg.
    Auth: same as /index (CurrentUser + user_can_access_nvr).
    Rate: not cached — callers must throttle (drag preview: emit on drag end, not mousemove).
    """
    from fastapi.responses import Response as FastResponse
    from app.services.playback.snapshot import grab_frame, SnapshotError
    from app.services.playback.url_builder import validate_footage_epoch, validate_channel

    validate_channel(channel)
    validate_footage_epoch(at)

    nvr = (await session.execute(select(Nvr).where(Nvr.id == nvr_id))).scalar_one_or_none()
    if nvr is None or not user_can_access_nvr(user, nvr):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "NVR not found")

    settings = get_settings()
    password = decrypt_password(nvr.rtsp_password_encrypted)
    try:
        jpeg = await grab_frame(
            ip=nvr.ip,
            rtsp_port=nvr.port,
            user=nvr.rtsp_username,
            pw=password,
            channel=channel,
            footage_epoch=at,
            tz_offset_minutes=settings.playback_tz_offset_minutes,
            ffbin=settings.reencode_ffmpeg_bin,
        )
    except SnapshotError as exc:
        log.warning("Snapshot failed nvr=%s ch=%d at=%d: %s", nvr_id, channel, at, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Snapshot unavailable") from exc

    return FastResponse(content=jpeg, media_type="image/jpeg")
```

### TDD steps

**Step 9.1 — Unit tests for `build_snapshot_argv`**
File: `backend/tests/test_playback_snapshot.py`

1. `build_snapshot_argv("ffmpeg", "rtsp://...", quality=4)` → first element is `"ffmpeg"`.
2. `-frames:v 1` is present.
3. `-f image2` is present.
4. `-vcodec mjpeg` is present.
5. `pipe:1` is the last element.
6. `-rtsp_transport tcp` is present (NOT udp — snapshot uses TCP for reliability).
7. `-q:v 4` appears in argv.
8. No element contains a space character.
9. `quality=1` → `-q:v 1` in argv.

**Step 9.2 — Unit tests for `grab_frame` timeout/error handling**
Mock `asyncio.create_subprocess_exec` (via `unittest.mock.patch`) to return a proc that:
1. Returns empty stdout → `SnapshotError` raised.
2. Raises `asyncio.TimeoutError` via `wait_for` mock → `SnapshotError("timed out")`.

**Step 9.3 — HTTP endpoint unit test**
File: `backend/tests/test_playback_router_thumb.py`

Pattern: minimal FastAPI app with `playback_router`, dep overrides for auth+session,
`monkeypatch.setattr("app.routers.playback.grab_frame", AsyncMock(return_value=b"\xff\xd8..."))`.

1. `GET /api/v1/playback/{nvr_id}/1/thumb?at=1719734400` → 200, `Content-Type: image/jpeg`.
2. `grab_frame` mock raises `SnapshotError` → response is 502.
3. Unknown NVR → 404.
4. `at=0` → 400 (validate_footage_epoch rejects).
5. `channel=0` → 400.

**Step 9.4 — Implement `snapshot.py` + router endpoint** → all tests green.

**Step 9.5 — INTEGRATION (manual):**
- `GET /api/v1/playback/{nvr_id}/1/thumb?at=<epoch>` with a known recording epoch.
- Verify JPEG is returned and the frame is recognizable as the scene at that time.
- Verify ffmpeg exits cleanly (no orphan processes).
- Test with an epoch in a gap → 502 (SnapshotError) or first available frame.

**Step 9.6 — Commit**
`feat(playback): thumbnail/snapshot endpoint`

### Integration points
- `build_playback_url` and `epoch_to_nvr_local` from Task 5 (`url_builder.py`).
- `validate_channel`, `validate_footage_epoch` from Task 5.
- `decrypt_password`, `user_can_access_nvr`, `get_settings` — same pattern as `/index`.
- Snapshot does NOT acquire `NvrBudget` — it's a short-lived one-shot (< 15s) with no
  sustained NVR stream. If the budget must protect snapshots too, flag for product review.

### Global constraints (spec §7)
- Auth: `CurrentUser` + `user_can_access_nvr` — same as `/index`.
- No SSRF: NVR IP comes from DB row, not request param.
- No credential in error responses.
- Timeout: 15s hard limit on ffmpeg; process killed on timeout.

### Ambiguities / spec conflicts
1. **Snapshot budget**: Spec §6 says "per-NVR playback cap" but doesn't explicitly
   include snapshot. Decision: exclude snapshots from NvrBudget (they're short-lived).
   If V9 is very tight (cap=1), this needs revision.
2. **Client-side vs server-side snapshot**: Spec §1 says "Snapshot: save the current
   frame as PNG with the NVR-local timestamp" — implemented via `useSnapshot` on the
   frontend (`<video>` → canvas → PNG). The `/thumb` endpoint serves drag-preview and
   cold frames. Both are in scope; this task covers the server endpoint only.

---

## Task 10 — Observability + active-sessions endpoint

**Goal:** Structured per-session event logs; an `GET /playback/sessions` endpoint that
lists active sessions (count per NVR/user, uptime); a lightweight session registry.

### Files created/modified
- **MODIFY** `backend/app/services/playback/session.py` (extend `_active_sessions` + session metadata)
- **MODIFY** `backend/app/routers/playback.py` (add `/sessions` endpoint)

### Exact interfaces

**Session metadata** (add to `PlaybackSession` dataclass in Task 7's `session.py`):
```python
@dataclass
class PlaybackSession:
    # ... (existing fields from Task 7) ...

    # Observability metadata — set on construction, not updated
    user_id: str = ""            # str(user.id) UUID
    username: str = ""
    client_ip: str = ""
    nvr_label: str = ""
    _paused_at: float = 0.0      # monotonic when last paused (for idle reaper)
    _seek_count: int = 0         # total seek operations
    _bytes_sent: int = 0         # total fMP4 bytes forwarded to WS
    _fragments_sent: int = 0

    def to_status_dict(self) -> dict:
        """Return serialisable session snapshot for the /sessions endpoint."""
        now = time.monotonic()
        return {
            "session_id": self.session_id,
            "nvr_id": self.nvr_id,
            "nvr_label": self.nvr_label,
            "channel": self.channel,
            "user_id": self.user_id,
            "username": self.username,
            "client_ip": self.client_ip,
            "state": self.state,
            "speed": self.speed,
            "footage_epoch": self.footage_now() if self.state == SessionState.PLAYING else self.t0,
            "uptime_seconds": int(now - self._started_at),
            "seek_count": self._seek_count,
            "bytes_sent": self._bytes_sent,
            "fragments_sent": self._fragments_sent,
        }
```

**Active sessions endpoint:**
```python
# In backend/app/routers/playback.py

from app.services.playback.session import _active_sessions

@router.get("/sessions")
async def active_playback_sessions(
    user: CurrentUser,
) -> dict:
    """List active playback sessions. Admin-only.

    Returns:
        {
          "total": <int>,
          "sessions": [{ session_id, nvr_id, nvr_label, channel, user_id, username,
                         client_ip, state, speed, footage_epoch, uptime_seconds,
                         seek_count, bytes_sent, fragments_sent }, ...]
        }
    """
    from app.deps import require_admin  # or inline role check
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")

    sessions = [s.to_status_dict() for s in _active_sessions.values()]
    return {
        "total": len(sessions),
        "sessions": sessions,
    }
```

**Structured event logging** (in `session.py`, called from lifecycle methods):
```python
# Called from open(), seek(), set_speed(), close() — emit one structured log line per event.
# Use the existing `log = logging.getLogger("dss.playback.session")` logger.

def _log_event(sess: "PlaybackSession", event: str, **extra) -> None:
    log.info(
        "playback_event event=%s session=%s nvr=%s ch=%d user=%s speed=%d %s",
        event,
        sess.session_id,
        sess.nvr_id,
        sess.channel,
        sess.username,
        sess.speed,
        " ".join(f"{k}={v}" for k, v in extra.items()),
    )

# Call sites:
# open():       _log_event(self, "start", t0=self.t0, footage_epoch=self.t0)
# seek():       _log_event(self, "seek",  from_epoch=old_t0, to_epoch=self.t0)
# set_speed():  _log_event(self, "speed", old=old_speed, new=self.speed)
# close():      _log_event(self, "stop",  uptime=int(time.monotonic()-self._started_at),
#                          bytes_sent=self._bytes_sent, seek_count=self._seek_count,
#                          ffmpeg_rc=proc.returncode)
```

### TDD steps

**Step 10.1 — Unit tests for `to_status_dict`**
File: `backend/tests/test_playback_session_unit.py` (extend from Task 7)

1. A freshly constructed `PlaybackSession` with known fields → `to_status_dict()` contains
   all required keys: `session_id`, `nvr_id`, `channel`, `user_id`, `state`, `speed`,
   `uptime_seconds`, `seek_count`, `bytes_sent`, `fragments_sent`.
2. `uptime_seconds` increases over wall time (monkeypatch `time.monotonic`).
3. `footage_epoch` equals `t0` when state is not PLAYING.
4. `footage_epoch` equals `footage_epoch_at(t0, wall_start, speed, now)` when PLAYING.

**Step 10.2 — Unit tests for `/sessions` endpoint**
File: `backend/tests/test_playback_router_sessions.py`

Pattern: minimal FastAPI app with playback router + dep overrides.
Inject fake sessions into `_active_sessions` in the test fixture.

1. Admin user → 200, `{"total": N, "sessions": [...]}`.
2. Operator user → 403.
3. Empty registry → `{"total": 0, "sessions": []}`.
4. Two active sessions → `total: 2`, both appear in `sessions` list.
5. Response contains `uptime_seconds`, `bytes_sent`, `seek_count` for each session.

**Step 10.3 — Implement `to_status_dict` + `/sessions` endpoint** → all tests green.

**Step 10.4 — Wire structured logging** in lifecycle methods (`open`, `seek`, `close`).
No automated test for log output — verify manually by running a session and checking
the FastAPI log output. Each event should produce one structured log line.

**Step 10.5 — INTEGRATION (manual):**
- Open 2 sessions → `GET /api/v1/playback/sessions` returns both with correct metadata.
- Close one session → endpoint returns 1.
- Verify `bytes_sent` increments as fMP4 fragments flow.
- Verify `uptime_seconds` is accurate.
- `GET /api/v1/playback/sessions` as an operator → 403.

**Step 10.6 — Commit**
`feat(playback): observability + active-sessions endpoint`

### Integration points
- `_active_sessions` dict is populated in the WS endpoint (Task 8) immediately after
  `websocket.accept()` and cleared in the WS `finally` block.
- `to_status_dict()` calls `footage_now()` from Task 7.
- `NvrBudget.snapshot()` (Task 6) provides per-NVR counts; the `/sessions` endpoint
  may include this as a summary.

### Global constraints (spec §9)
- Per-session events: start/seek/speed/stop, fragments+bytes sent, NVR session duration,
  ffmpeg exit code — all required by spec §9.
- ffmpeg stderr ring-buffered (last 200 lines) and logged on non-zero exit — implemented
  in Task 7's `_drain_stderr`.
- `/sessions` mirrors go2rtc's stream list pattern — admin-only.
- Distinguish "no recording in range" (ffmpeg exits cleanly, empty stdout) from
  "NVR error" (401, timeout, ffmpeg exits non-zero with connection error in stderr).

---

## Flagged ambiguities and spec/spike conflicts

### F1 — RBAC: `user_can_access_nvr` vs `user_can_access_camera`
**Spec §7:** "enforce `user_can_access_nvr` (region scoping) on `/index`, `/thumb`, and `/stream`."
**`deps.py` comment:** "RBAC model: operator — sees ONLY the cameras explicitly granted to them
(per-camera grants in `user.cameras`)."

The existing `/index` endpoint uses `user_can_access_nvr` (region-based). The active RBAC
model is per-camera (not per-region). For playback endpoints that identify a `(nvr_id, channel)`,
the correct guard should be `user_can_access_camera` (check that the specific camera is
granted to the user), not just NVR-level region membership.

**Recommendation:** Use `user_can_access_camera` on `/stream` and `/thumb` (load the Camera
row by `nvr_id + channel`, call `user_can_access_camera`). For consistency, update
`/index` and `/availability` to do the same. Flag for product owner review before Task 8.

### F2 — `nvr.port` is RTSP; HTTP CGI is always 80
`Nvr.port` is the RTSP port (default 554). The Phase-1 `find_clips` calls use port 80
(hard-coded) for the HTTP CGI. The `build_playback_url` uses `nvr.port` for RTSP.
Implementations must not mix these up. Document in docstrings.

### F3 — Password special characters in RTSP URL
If `nvr.rtsp_password_encrypted` decrypts to a password containing `@`, `:`, `/`, or `?`,
the constructed `rtsp://user:pw@host/...` URL is ambiguous (the `@` in the password would
split the URL authority). Since the URL goes to ffmpeg argv (list, not shell), ffmpeg
may or may not handle it correctly depending on its URL parser.

**Mitigation:** URL-percent-encode only `user` and `pw` in `build_playback_url` using
`urllib.parse.quote(user, safe="")` and `quote(pw, safe="")`. Add a test case for
`pw="pass@word"`. If ffmpeg's RTSP parser doesn't accept percent-encoded credentials,
the workaround is to use `-user pw` RTSP options — validate during integration.

### F4 — V9 (per-NVR playback cap) unmeasured
Default `playback_nvr_budget = 2` is conservative and may be too low (only 2 concurrent
playback sessions per NVR when V9 may allow 4). Needs live measurement on both NVRs.
The setting makes it a config change without code changes.

### F5 — Speed filter correctness (V1 RTSP Scale unmeasured)
The `select=not(mod(n,speed)),setpts=...` ffmpeg filter for server-side speed is an
approximation that must be validated against the re-encoded stream on the real NVR.
Specifically: does the re-encoded GOP (0.5s keyframe interval) interact correctly with
frame selection at 2×/4×/8×? The 0.5s GOP means at 2× we output every other keyframe
(every 1s of footage time in the output), which is coarser than ideal. Validate during
Task 7 integration testing.

### F6 — `{type:"init"}` codec string
The `codec` field in `{type:"init"}` should carry the codec description string for MSE
`SourceBuffer` MIME type configuration. The value depends on the ffmpeg encoder used
(libx264 → `"avc1.42E01E"`, h264_qsv → same profile). Rather than hard-coding, the
session should detect the codec from the fMP4 init segment or use a settings-derived
constant. For MVP: hard-code `"avc1.42E01E"` (Baseline H.264) and validate during
integration (if the encoder uses a different profile, MSE will reject the init segment).

### F7 — `t0` accuracy vs actual keyframe PTS
The spec says: "playhead snaps to the actual returned keyframe." The `t0` value in
`{type:"init"}` should be the footage epoch of the actual first keyframe in the fMP4
output, not the requested seek epoch. There is no easy way to extract this from the
fMP4 stream without parsing the TRUN box. For MVP: `t0 = start_epoch` (the requested
seek time). The error is bounded by one GOP (≤ 0.5s after re-encode). The `clock`
heartbeat corrects drift over time. Flag for phase 3 if higher accuracy is required.

### F8 — Windows-only Job Object dependency
The `_assign_job_object` implementation uses `ctypes.windll` (Windows-only). On macOS/Linux
dev machines, the function is a no-op (guarded by `sys.platform != "win32"`). The unit
tests for `_build_ffmpeg_argv` and `footage_epoch_at` run on macOS; the Job Object code
path is only tested manually on the Windows server.

### F9 — NvrBudget not thread-safe
The `NvrBudget` implementation uses `asyncio.Lock` (not `threading.Lock`). It is safe for
concurrent asyncio tasks but NOT for concurrent threads. Since FastAPI + uvicorn run in
a single asyncio event loop (no thread pool for WS handlers), this is correct. If the
app ever uses `run_in_threadpool` or `anyio.to_thread` for playback operations, switch
to `asyncio.Lock` + `loop.run_in_executor` patterns.

---

## File tree summary (new files to create)

```
backend/
  app/
    services/
      playback/
        __init__.py        (already exists — may need to update __all__)
        index_parser.py    (Phase 1, unchanged)
        media_find.py      (Phase 1, unchanged)
        url_builder.py     ← Task 5 (NEW)
        nvr_budget.py      ← Task 6 (NEW)
        session.py         ← Task 7 (NEW)
        snapshot.py        ← Task 9 (NEW)
    routers/
      playback.py          ← Tasks 8, 9, 10 (MODIFY: add WS + /thumb + /sessions)
    settings.py            ← Task 5 (MODIFY: add playback_* settings)
    main.py                ← Tasks 6, 7 (MODIFY: lifespan wiring)
  tests/
    test_playback_url_builder.py          ← Task 5 (NEW)
    test_playback_nvr_budget.py           ← Task 6 (NEW)
    test_playback_session_unit.py         ← Tasks 7, 10 (NEW)
    test_playback_ws_auth.py              ← Task 8 (NEW)
    test_playback_ws_protocol.py          ← Task 8 (NEW)
    test_playback_snapshot.py             ← Task 9 (NEW)
    test_playback_router_thumb.py         ← Task 9 (NEW)
    test_playback_router_sessions.py      ← Task 10 (NEW)
```

---

*End of Phase 2 task specs. Approved interfaces grounded in live code read on 2026-06-30.*
