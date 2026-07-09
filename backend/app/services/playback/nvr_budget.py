"""Per-NVR + global playback semaphore.

Usage
-----
Call ``init_budget(per_nvr, global_cap)`` once in the FastAPI lifespan
(startup), then call ``get_budget().session(nvr_id)`` from the WS endpoint
(Task 8) to guard each session:

    async with get_budget().session(nvr_id):
        # spawn ffmpeg, stream data …

The context manager is non-blocking: it raises ``BudgetExhausted`` immediately
if either cap is full (playback is the lower-priority tenant — never queue).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

# The module-level ``budget`` singleton is intentionally NOT exported: it is
# mutable/late-bound (None until init_budget()); callers must go through
# ``get_budget()`` which raises if used before initialisation.
__all__ = ["BudgetExhausted", "NvrBudget", "init_budget", "get_budget"]


class BudgetExhausted(Exception):
    """Raised (never blocks) when a per-NVR or global cap is full."""


class NvrBudget:
    """Shared per-NVR + global semaphore for playback sessions.

    Call ``init_budget()`` once at app startup (lifespan). ``try_acquire(nvr_id)``
    is a non-blocking try: raises ``BudgetExhausted`` immediately if at cap.

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
        self._nvr_counts: dict[str, int] = {}  # nvr_id → active count
        self._global_count: int = 0

    def _nvr_count(self, nvr_id: str) -> int:
        return self._nvr_counts.get(nvr_id, 0)

    async def try_acquire(self, nvr_id: str) -> None:
        """Attempt to acquire one slot. Raises ``BudgetExhausted`` if at cap.

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
        """Release one slot. Safe to call even if acquire was never called."""
        async with self._lock:
            c = self._nvr_counts.get(nvr_id, 0)
            if c > 0:
                self._nvr_counts[nvr_id] = c - 1
                self._global_count -= 1  # only when a per-NVR slot was actually held

    @asynccontextmanager
    async def session(self, nvr_id: str) -> AsyncIterator[None]:
        """Context manager: acquire on enter, release on exit (even on exception)."""
        await self.try_acquire(nvr_id)
        try:
            yield
        finally:
            await self.release(nvr_id)

    def active_count(self, nvr_id: str) -> int:
        """Current active session count for an NVR. Read-only; not lock-protected."""
        return self._nvr_counts.get(nvr_id, 0)

    def global_active(self) -> int:
        """Total active sessions across all NVRs. Read-only; not lock-protected."""
        return self._global_count

    def snapshot(self) -> dict[str, int]:
        """Return a copy of ``{nvr_id: count}`` for observability. Not locked."""
        return dict(self._nvr_counts)


# Module-level singleton — initialised in lifespan
budget: NvrBudget | None = None


def init_budget(per_nvr: int, global_cap: int) -> NvrBudget:
    """Initialise the module-level singleton. Call once in the FastAPI lifespan."""
    global budget
    budget = NvrBudget(per_nvr=per_nvr, global_cap=global_cap)
    return budget


def get_budget() -> NvrBudget:
    """Return the singleton. Raises ``RuntimeError`` if not yet initialised."""
    if budget is None:
        raise RuntimeError(
            "NvrBudget not initialised — call init_budget() in lifespan"
        )
    return budget
