"""Thin async client for MediaMTX's HTTP control API (port 9997).

We only use the path-config endpoints — enough to add/patch/delete a path
without restarting the process. See
https://bluenviron.github.io/mediamtx/ for the OpenAPI spec.

Errors:
  * 400 from `add` when the path already exists — surfaced as PathExists.
  * 404 from `get`/`patch`/`delete` when the path doesn't exist — PathNotFound.
  * Anything else becomes MediaMTXError with the raw body for debugging.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.settings import get_settings

log = logging.getLogger("dss.mediamtx")


class MediaMTXError(RuntimeError):
    pass


class PathExists(MediaMTXError):
    pass


class PathNotFound(MediaMTXError):
    pass


class MediaMTXClient:
    """Async HTTP client. Reuses one connection pool for the app lifetime."""

    def __init__(self, base_url: str | None = None, timeout: float = 5.0):
        self._base = (base_url or get_settings().mediamtx_api_url).rstrip("/")
        # trust_env=False so we don't pick up the user's HTTP(S)_PROXY env vars
        # — MediaMTX runs on localhost and routing it through a proxy is a
        # configuration footgun that surfaces as ConnectError mid-request.
        self._client = httpx.AsyncClient(
            base_url=self._base, timeout=timeout, trust_env=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ping(self) -> bool:
        """Used by lifespan/health checks. Doesn't raise on connection error."""
        try:
            r = await self._client.get("/v3/config/paths/list", params={"itemsPerPage": 1})
            return r.status_code == 200
        except httpx.HTTPError as e:
            log.debug("MediaMTX ping failed: %s", e)
            return False

    async def list_paths(self) -> dict[str, dict[str, Any]]:
        """Return all configured paths keyed by name. MediaMTX paginates;
        pull everything (~684 entries at full scale — single page is fine)."""
        out: dict[str, dict[str, Any]] = {}
        page = 0
        t0 = time.perf_counter()
        while True:
            r = await self._client.get(
                "/v3/config/paths/list",
                params={"page": page, "itemsPerPage": 500},
            )
            self._raise(r)
            body = r.json()
            for item in body.get("items", []):
                out[item["name"]] = item
            if (page + 1) * body.get("itemsPerPage", 500) >= body.get("itemCount", 0):
                break
            page += 1
        log.debug("MediaMTX list_paths → %d entries in %.0fms",
                  len(out), (time.perf_counter() - t0) * 1000)
        return out

    async def get_path(self, name: str) -> dict[str, Any]:
        t0 = time.perf_counter()
        r = await self._client.get(f"/v3/config/paths/get/{name}")
        dt = (time.perf_counter() - t0) * 1000
        if r.status_code == 404:
            log.debug("MediaMTX get_path %s → 404 (%.0fms)", name, dt)
            raise PathNotFound(name)
        self._raise(r)
        log.debug("MediaMTX get_path %s → %d (%.0fms)", name, r.status_code, dt)
        return r.json()

    async def add_path(self, name: str, config: dict[str, Any]) -> None:
        t0 = time.perf_counter()
        r = await self._client.post(f"/v3/config/paths/add/{name}", json=config)
        dt = (time.perf_counter() - t0) * 1000
        if r.status_code == 400 and "already" in r.text.lower():
            log.info("MediaMTX add_path %s → already exists (%.0fms)", name, dt)
            raise PathExists(name)
        if r.status_code >= 400:
            log.warning("MediaMTX add_path %s → %d %s (%.0fms)", name, r.status_code, r.text[:200], dt)
        else:
            log.info("MediaMTX add_path %s → %d (%.0fms)", name, r.status_code, dt)
        self._raise(r)

    async def patch_path(self, name: str, config: dict[str, Any]) -> None:
        t0 = time.perf_counter()
        r = await self._client.patch(f"/v3/config/paths/patch/{name}", json=config)
        dt = (time.perf_counter() - t0) * 1000
        if r.status_code == 404:
            log.info("MediaMTX patch_path %s → 404 not found (%.0fms)", name, dt)
            raise PathNotFound(name)
        if r.status_code >= 400:
            log.warning("MediaMTX patch_path %s → %d %s (%.0fms)", name, r.status_code, r.text[:200], dt)
        else:
            log.info("MediaMTX patch_path %s fields=%s → %d (%.0fms)",
                     name, list(config.keys()), r.status_code, dt)
        self._raise(r)

    async def delete_path(self, name: str) -> None:
        t0 = time.perf_counter()
        r = await self._client.delete(f"/v3/config/paths/delete/{name}")
        dt = (time.perf_counter() - t0) * 1000
        if r.status_code == 404:
            log.info("MediaMTX delete_path %s → 404 (already gone, %.0fms)", name, dt)
            raise PathNotFound(name)
        if r.status_code >= 400:
            log.warning("MediaMTX delete_path %s → %d %s (%.0fms)", name, r.status_code, r.text[:200], dt)
        else:
            log.info("MediaMTX delete_path %s → %d (%.0fms)", name, r.status_code, dt)
        self._raise(r)

    @staticmethod
    def _raise(r: httpx.Response) -> None:
        if r.status_code >= 400:
            raise MediaMTXError(f"{r.request.method} {r.request.url}: {r.status_code} {r.text}")


# Module-level singleton — created lazily so importing this file doesn't
# spin up an httpx client (matters for migrations / one-shot scripts).
_client: MediaMTXClient | None = None


def get_client() -> MediaMTXClient:
    global _client
    if _client is None:
        _client = MediaMTXClient()
    return _client


async def shutdown_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
