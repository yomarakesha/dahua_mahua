"""HTTP client for the go2rtc relay (the buffered-MSE browser delivery layer).

Why go2rtc: cameras on a jittery LAN deliver frames in bursts; WebRTC's tiny
real-time jitter buffer freezes on that even at 0% packet loss. go2rtc serves a
buffered MSE pipeline to the browser that absorbs the bursts (proven: 6-cam grid
went from dozens of WebRTC freezes to ~zero MSE stalls). It keeps the same
fan-out model of one on-demand RTSP pull per camera, N viewers.

go2rtc's stream API:
    GET    /api/streams                 -> {name: {producers:[{url}], consumers}}
    PUT    /api/streams?name=N&src=URL  -> add/replace a stream
    DELETE /api/streams?src=N           -> remove (note: `src` carries the NAME)
"""

from __future__ import annotations

import logging

import httpx

from app.settings import get_settings

log = logging.getLogger("dss.go2rtc")


class Go2rtcError(Exception):
    pass


class Go2rtcClient:
    def __init__(self, base_url: str, timeout: float = 5.0):
        self._base = base_url.rstrip("/")
        # trust_env=False so a stray HTTP(S)_PROXY can't hijack localhost calls.
        self._client = httpx.AsyncClient(timeout=timeout, trust_env=False)

    async def ping(self) -> None:
        r = await self._client.get(f"{self._base}/api/streams")
        r.raise_for_status()

    async def list_streams(self) -> dict[str, str]:
        """Return {name: active_producer_url}. NOTE: go2rtc only lists a producer
        while the stream is actively connected, so an idle on-demand stream maps
        to "" (not its configured source). Reconcile therefore re-PUTs idle
        streams — harmless (idempotent config update, no viewer to disturb), but
        the source-unchanged skip only fires for streams with live viewers."""
        r = await self._client.get(f"{self._base}/api/streams")
        r.raise_for_status()
        out: dict[str, str] = {}
        for name, info in (r.json() or {}).items():
            producers = (info or {}).get("producers") or []
            out[name] = (producers[0].get("url", "") if producers else "")
        return out

    async def list_stream_states(self) -> dict[str, dict]:
        """Per-stream runtime health from GET /api/streams, normalised to the
        SAME ``{ready, readers, source}`` shape the source watchdog already
        consumes, so the disable-decision logic stays simple.

        Heuristic (see source_watch for the full rationale):
          * ``readers``  = the stream's consumers (active viewers).
          * A stream with **no consumers** is an idle on-demand source — go2rtc
            has no reason to dial its RTSP producer, so a missing producer here
            is EXPECTED, not a failure. Such streams are omitted entirely, so
            the watchdog never even looks at them (no false auto-disable).
          * For a stream WITH ≥1 consumer, ``ready`` is True iff go2rtc lists at
            least one producer (an established upstream connection). A live
            consumer but ZERO producers is the wrong-password / host-unreachable
            signature — that is the only thing we let count as "failing".

        Defensive: go2rtc's JSON differs across versions. Anything we can't
        confidently parse is skipped (treated as "no data"), never raised — the
        watchdog must never crash or false-positive on an unknown shape."""
        r = await self._client.get(f"{self._base}/api/streams")
        r.raise_for_status()
        body = r.json()
        out: dict[str, dict] = {}
        if not isinstance(body, dict):
            return out  # unknown top-level shape → no data, stay quiet
        for name, info in body.items():
            if not isinstance(info, dict):
                continue
            producers = info.get("producers")
            consumers = info.get("consumers")
            producers = producers if isinstance(producers, list) else []
            consumers = consumers if isinstance(consumers, list) else []
            if not consumers:
                # Idle on-demand stream (nobody watching) — never evaluate it.
                continue
            ready = any(isinstance(p, dict) for p in producers)
            out[name] = {"ready": ready, "readers": consumers, "source": None}
        return out

    async def set_stream(self, name: str, src: str) -> None:
        r = await self._client.put(
            f"{self._base}/api/streams", params={"name": name, "src": src}
        )
        if r.status_code >= 400:
            raise Go2rtcError(f"set_stream {name}: {r.status_code} {r.text[:120]}")

    async def delete_stream(self, name: str) -> None:
        r = await self._client.delete(
            f"{self._base}/api/streams", params={"src": name}
        )
        if r.status_code >= 400 and r.status_code != 404:
            raise Go2rtcError(f"delete_stream {name}: {r.status_code}")

    async def restart(self) -> None:
        """Restart go2rtc so it re-reads its config file. Used by the file-based
        re-encode sync (exec sources are only honoured from the YAML, not the API).
        go2rtc re-execs itself; active viewers reconnect."""
        r = await self._client.post(f"{self._base}/api/restart")
        if r.status_code >= 400:
            raise Go2rtcError(f"restart: {r.status_code} {r.text[:120]}")

    async def aclose(self) -> None:
        await self._client.aclose()


_client: Go2rtcClient | None = None


def get_client() -> Go2rtcClient:
    global _client
    if _client is None:
        _client = Go2rtcClient(get_settings().go2rtc_api_url)
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
