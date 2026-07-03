"""Warm-stream pool — keep a bounded set of go2rtc SUB producers hot.

Why this exists (spike evidence, docs/superpowers/plans/2026-07-03-smartpss-parity.md):
a go2rtc SUB stream opens in ~0.5s when its producer is already connected (warm)
vs 2.6–5s cold — go2rtc instant-starts a new MSE client from its cached keyframe.
So if the backend keeps a server-side consumer draining a camera's SUB, the next
browser that opens that camera is instant.

We warm SUBS ONLY. MAINS use the mpegts-pipe path (no keyframe cache) and do NOT
benefit, so warming them would just burn NVR pull slots for nothing.

Bounds — this MUST be conservative. The testik NVR (192.168.20.39) has a hard
concurrent-pull cap; over-warming would exhaust it and starve real viewers /
playback. Two caps apply, both emulating the NvrBudget pattern:
  * a GLOBAL cap (``warm_pool_max_streams``, default 24), and
  * a PER-NVR cap (``warm_pool_per_nvr_max``, default 8) — a warm stream is
    cheaper than a playback session but STILL counts against the NVR's real
    concurrent-pull limit.
When more cameras are requested than fit, we warm the highest-priority subset
(the order they were requested) and LOG what was dropped — never silent.

Credential hygiene: the go2rtc ``stream.mp4?src=`` URL carries the stream NAME
only (no NVR user/password), so it is safe to log. NVR passwords never appear
here at all — go2rtc owns the credentialed RTSP dial.

Lifecycle: a module singleton, ``start()`` on lifespan startup (idles with an
empty desired set), ``close_all()`` on shutdown. ``set_desired()`` is idempotent
and race-safe (an asyncio.Lock guards the diff).
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Iterable

import httpx

from app.settings import get_settings

log = logging.getLogger("dss.warm_pool")

__all__ = ["WarmPool", "get_warm_pool", "reset_warm_pool"]

# One warm consumer = one long-lived GET /api/stream.mp4 that we drain and
# discard. Reconnect backoff after a drop (the producer flapped or go2rtc was
# restarted). A clean stream-end reconnects fast (backoff resets).
_BACKOFF_INITIAL = 1.0
_BACKOFF_MAX = 30.0

# Bytes discarded per read. go2rtc's mp4 fragments are small; a modest chunk
# keeps the drain responsive without spinning on tiny reads.
_DRAIN_CHUNK = 64 * 1024

# A warm consumer never stops sending, so there is NO read timeout — only a
# connect timeout. A stall shows up as the connection dropping, which the worker
# reconnect loop already handles.
_CONNECT_TIMEOUT = 5.0


def sub_stream_name(nvr_id: str, channel: int) -> str:
    """go2rtc SUB stream name for a camera — MUST match path_sync.path_name()
    for StreamQuality.sub (``{nvr_id}_ch{channel}``), so the producer we warm is
    the exact one the browser opens."""
    return f"{nvr_id}_ch{channel}"


class WarmPool:
    """Bounded, NvrBudget-aware pool of warm go2rtc SUB consumers.

    Keys are ``(nvr_id, channel)`` tuples. ``set_desired`` diffs the requested
    set against the running tasks and starts/stops workers accordingly, honouring
    the global + per-NVR caps. De-selected streams are torn down only after a
    short drop-grace so a quick page flip (open → close → reopen) doesn't thrash
    the NVR.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        # key -> the running drain worker task
        self._tasks: dict[tuple[str, int], asyncio.Task] = {}
        # key -> a pending "cancel after drop-grace" timer task
        self._grace: dict[tuple[str, int], asyncio.Task] = {}
        # accepted desired keys, in priority order (post-cap)
        self._desired: list[tuple[str, int]] = []
        # last drop report: [(key, reason), ...] for observability
        self._last_dropped: list[tuple[tuple[str, int], str]] = []
        self._client: httpx.AsyncClient | None = None
        self._started = False

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Mark the pool live and create its httpx client. Idles with an empty
        desired set until the first ``set_desired``. Idempotent."""
        if self._started:
            return
        self._started = True
        if self._client is None:
            self._client = self._new_client()
        log.info("warm_pool started (idle, empty desired set)")

    async def close_all(self) -> None:
        """Cancel every worker + grace timer and close the httpx client.

        Lifespan-clean: safe to call once at shutdown. Idempotent."""
        async with self._lock:
            tasks = list(self._tasks.values()) + list(self._grace.values())
            self._tasks.clear()
            self._grace.clear()
            self._desired = []
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._client = None
        self._started = False
        log.info("warm_pool closed (%d worker(s) cancelled)", len(tasks))

    # ── desired-set reconciliation ───────────────────────────────────────────

    async def set_desired(self, camera_keys: Iterable[tuple[str, int]]) -> dict:
        """Reconcile the running warm consumers to *camera_keys*.

        *camera_keys* is an ordered iterable of ``(nvr_id, channel)`` — the order
        conveys priority (earliest = highest), so when a cap is hit the earliest
        entries win. Duplicates are collapsed (first occurrence kept).

        Returns ``{"warming": n, "capped": m}`` where *n* is the number of
        streams now targeted (post-cap) and *m* is how many were dropped by a cap.
        Race-safe: the diff is guarded by an asyncio.Lock.
        """
        # Normalise → ordered unique list of (str, int).
        keys: list[tuple[str, int]] = []
        seen: set[tuple[str, int]] = set()
        for raw in camera_keys:
            key = (str(raw[0]), int(raw[1]))
            if key not in seen:
                seen.add(key)
                keys.append(key)

        async with self._lock:
            accepted, dropped = self._apply_caps(keys)
            self._desired = accepted
            self._last_dropped = dropped
            accepted_set = set(accepted)

            if dropped:
                # Never silently truncate — name every dropped stream + why.
                log.warning(
                    "warm_pool: caps hit — warming %d, dropped %d: %s",
                    len(accepted),
                    len(dropped),
                    ", ".join(
                        f"{sub_stream_name(*k)}({reason})" for k, reason in dropped
                    ),
                )

            # Start workers for newly-accepted keys; cancel any pending grace
            # timer for a key that just got re-selected (page flip back).
            for key in accepted:
                g = self._grace.pop(key, None)
                if g is not None:
                    g.cancel()
                if key not in self._tasks:
                    self._tasks[key] = asyncio.create_task(
                        self._worker(key), name=f"warm-{sub_stream_name(*key)}"
                    )

            # De-selected keys still running: schedule a grace-delayed cancel so
            # a quick reopen keeps the producer warm instead of re-dialling.
            for key in list(self._tasks):
                if key not in accepted_set and key not in self._grace:
                    self._grace[key] = asyncio.create_task(
                        self._grace_cancel(key),
                        name=f"warm-grace-{sub_stream_name(*key)}",
                    )

            return {"warming": len(accepted), "capped": len(dropped)}

    def _apply_caps(
        self, keys: list[tuple[str, int]]
    ) -> tuple[list[tuple[str, int]], list[tuple[tuple[str, int], str]]]:
        """Split *keys* (priority order) into (accepted, dropped) honouring the
        global and per-NVR caps. Caps are read from settings on every call so a
        runtime config change takes effect on the next reconcile."""
        settings = get_settings()
        global_max = settings.warm_pool_max_streams
        per_nvr_max = settings.warm_pool_per_nvr_max
        accepted: list[tuple[str, int]] = []
        dropped: list[tuple[tuple[str, int], str]] = []
        per_nvr: dict[str, int] = defaultdict(int)
        for key in keys:
            nvr_id, _channel = key
            if len(accepted) >= global_max:
                dropped.append((key, "global_cap"))
                continue
            if per_nvr[nvr_id] >= per_nvr_max:
                dropped.append((key, "per_nvr_cap"))
                continue
            accepted.append(key)
            per_nvr[nvr_id] += 1
        return accepted, dropped

    async def _grace_cancel(self, key: tuple[str, int]) -> None:
        """After the drop-grace, tear down a de-selected worker — unless it was
        re-selected in the meantime (in which case set_desired cancelled us)."""
        try:
            await asyncio.sleep(get_settings().warm_pool_drop_grace_seconds)
        except asyncio.CancelledError:
            return
        async with self._lock:
            if key in set(self._desired):
                # Re-selected during the grace window — leave it running.
                self._grace.pop(key, None)
                return
            self._grace.pop(key, None)
            task = self._tasks.pop(key, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            log.info("warm_pool: dropped %s after drop-grace", sub_stream_name(*key))

    # ── worker ───────────────────────────────────────────────────────────────

    async def _worker(self, key: tuple[str, int]) -> None:
        """Keep one SUB producer warm: open GET /api/stream.mp4 and drain bytes,
        reconnecting with exponential backoff on any drop."""
        nvr_id, channel = key
        sub_name = sub_stream_name(nvr_id, channel)
        backoff = _BACKOFF_INITIAL
        while True:
            try:
                await self._drain_once(sub_name)
                # Clean end (go2rtc closed the stream) → reconnect promptly.
                backoff = _BACKOFF_INITIAL
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # sub_name has no credentials — safe to log.
                log.warning(
                    "warm_pool: %s dropped (%s); retry in %.1fs",
                    sub_name,
                    type(exc).__name__,
                    backoff,
                )
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise
            backoff = min(backoff * 2, _BACKOFF_MAX)

    async def _drain_once(self, sub_name: str) -> None:
        """Open one warm consumer and drain bytes until the stream ends.

        Isolated so tests can override it without any real go2rtc/NVR network."""
        client = self._client
        if client is None:
            client = self._client = self._new_client()
        base = get_settings().go2rtc_api_url.rstrip("/")
        url = f"{base}/api/stream.mp4"
        async with client.stream("GET", url, params={"src": sub_name}) as resp:
            resp.raise_for_status()
            async for _chunk in resp.aiter_bytes(_DRAIN_CHUNK):
                pass  # discard — we only keep the producer + keyframe cache warm

    @staticmethod
    def _new_client() -> httpx.AsyncClient:
        # trust_env=False so a stray HTTP(S)_PROXY can't hijack the localhost
        # go2rtc call (mirrors Go2rtcClient). read=None: a warm stream never
        # stops, so only the connect phase is bounded.
        return httpx.AsyncClient(
            timeout=httpx.Timeout(_CONNECT_TIMEOUT, read=None),
            trust_env=False,
        )

    # ── observability ────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """A serialisable snapshot for logging / an admin view. No locks — a
        read-only best-effort view (single event loop)."""
        settings = get_settings()
        per_nvr: dict[str, int] = defaultdict(int)
        for nvr_id, _channel in self._tasks:
            per_nvr[nvr_id] += 1
        return {
            "started": self._started,
            "warming": len(self._tasks),
            "desired": len(self._desired),
            "grace_pending": len(self._grace),
            "per_nvr": dict(per_nvr),
            "last_dropped": [
                {"stream": sub_stream_name(*k), "reason": reason}
                for k, reason in self._last_dropped
            ],
            "caps": {
                "global_max": settings.warm_pool_max_streams,
                "per_nvr_max": settings.warm_pool_per_nvr_max,
            },
        }


# ── module singleton ─────────────────────────────────────────────────────────

_pool: WarmPool | None = None


def get_warm_pool() -> WarmPool:
    """Return the process-wide WarmPool singleton (created on first use)."""
    global _pool
    if _pool is None:
        _pool = WarmPool()
    return _pool


def reset_warm_pool() -> None:
    """Drop the singleton (tests only — does NOT close tasks; call close_all
    first if a live pool exists)."""
    global _pool
    _pool = None
