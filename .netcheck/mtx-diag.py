"""Ground-truth diagnostic for the 'patched=34 but list shows 0' mystery.

Drives the backend's OWN MediaMTXClient against the running MediaMTX so we
remove every ambiguity (httpx vs Invoke-RestMethod, which instance, name
normalisation). Prints raw status codes and bodies for add/get/patch/list
on a real-style hyphenated path name.

Run from backend/ so .env (and thus mediamtx_api_url) loads identically to
the server:  cwd=backend  python ../.netcheck/mtx-diag.py
"""
import asyncio

import httpx

from app.settings import get_settings

BASE = get_settings().mediamtx_api_url.rstrip("/")
print(f"[cfg] mediamtx_api_url = {BASE}")

# Two names: the real DSS scheme (hyphens in the NVR id) and a plain one.
REAL = "nvr-192-168-20-58_ch1_main"
PLAIN = "diagtest_ch1_main"

CFG = {
    "source": "rtsp://user:pass@192.168.23.11:554/cam/realmonitor?channel=1&subtype=0",
    "sourceOnDemand": True,
    "sourceOnDemandStartTimeout": "20s",
    "sourceOnDemandCloseAfter": "60s",
    "rtspTransport": "tcp",
}


async def dump_list(c: httpx.AsyncClient, tag: str) -> None:
    r = await c.get("/v3/config/paths/list", params={"page": 0, "itemsPerPage": 500})
    body = r.json()
    names = [i["name"] for i in body.get("items", [])]
    print(f"[list {tag}] status={r.status_code} itemCount={body.get('itemCount')} names={names}")


async def try_add(c: httpx.AsyncClient, name: str) -> None:
    r = await c.post(f"/v3/config/paths/add/{name}", json=CFG)
    print(f"[add  {name}] status={r.status_code} body={r.text[:300]!r}")


async def try_get(c: httpx.AsyncClient, name: str) -> None:
    r = await c.get(f"/v3/config/paths/get/{name}")
    print(f"[get  {name}] status={r.status_code} body={r.text[:200]!r}")


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=5.0, trust_env=False) as c:
        await dump_list(c, "before")
        for name in (PLAIN, REAL):
            print(f"\n=== {name} ===")
            await try_add(c, name)
            await try_get(c, name)
        await dump_list(c, "after")


asyncio.run(main())
