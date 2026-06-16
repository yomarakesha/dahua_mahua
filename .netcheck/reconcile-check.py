"""Offline end-to-end validation of Variant A path generation.

Runs the real reconcile() against a clean MediaMTX using the live dss.db,
then reports how many _main paths point straight at a camera (192.168.23.x)
vs the NVR, and that sub paths still go via the NVR. No camera traffic is
generated — sourceOnDemand means MediaMTX only dials the camera when a
viewer asks. Passwords are masked in output.

Run from backend/ (so ./dss.db resolves) with backend on PYTHONPATH.
"""
import asyncio
import re

from app.db import SessionLocal
from app.services.mediamtx_api import get_client
from app.services.path_sync import reconcile

MASK = re.compile(r"//[^@/]+@")


def mask(s: str) -> str:
    return MASK.sub("//***@", s)


async def main() -> None:
    client = get_client()

    # Start clean: drop any leftover diag/test paths.
    existing = await client.list_paths()
    for name in list(existing):
        if name.startswith(("diagtest_", "nvr-")):
            try:
                await client.delete_path(name)
            except Exception:  # noqa: BLE001
                pass

    async with SessionLocal() as session:
        report = await reconcile(session, delete_orphans=True)
    print(f"[reconcile] {report.summary()}")
    if report.errors:
        for n, e in report.errors[:10]:
            print(f"  ERROR {n}: {e}")

    paths = await client.list_paths()
    mains = {n: p for n, p in paths.items() if n.endswith("_main")}
    subs = {n: p for n, p in paths.items() if not n.endswith("_main")}

    direct = sum(1 for p in mains.values() if "192.168.23." in p.get("source", ""))
    via_nvr_main = len(mains) - direct
    sub_via_nvr = sum(1 for p in subs.values() if "192.168.20.58" in p.get("source", ""))

    print(f"\ntotal paths={len(paths)}  main={len(mains)}  sub={len(subs)}")
    print(f"  main direct-from-camera (192.168.23.x): {direct}")
    print(f"  main via NVR (no camera ip)           : {via_nvr_main}")
    print(f"  sub  via NVR (192.168.20.58)          : {sub_via_nvr} / {len(subs)}")

    print("\nsample main paths:")
    for n in sorted(mains)[:4]:
        print(f"  {n} -> {mask(mains[n].get('source',''))}")
    print("sample sub paths:")
    for n in sorted(subs)[:3]:
        print(f"  {n} -> {mask(subs[n].get('source',''))}")


asyncio.run(main())
