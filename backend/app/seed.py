"""One-shot seed: import legacy nvr_inventory.json into the database.

Idempotent — re-running updates existing rows by id rather than inserting
duplicates. Cameras are created from the `channels: N` count (1..N).

Usage (from backend/):
    python -m app.seed                          # uses ../nvr_inventory.json
    python -m app.seed --inventory /path/to.json
    python -m app.seed --region-slug central    # assign all NVRs to one region
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import encrypt_password
from app.db import SessionLocal
from app.models import Camera, Nvr, Region, User, Role, Vendor
from app.security import hash_password
from app.settings import get_settings


async def _ensure_region(session: AsyncSession, slug: str, name: str | None = None) -> Region:
    region = (await session.execute(select(Region).where(Region.slug == slug))).scalar_one_or_none()
    if region is None:
        region = Region(slug=slug, name=name or slug.title())
        session.add(region)
        await session.flush()
        print(f"  + region '{slug}' created")
    return region


async def _ensure_bootstrap_admin(session: AsyncSession) -> None:
    settings = get_settings()
    existing = (await session.execute(select(User).limit(1))).scalar_one_or_none()
    if existing is not None:
        return
    admin = User(
        username=settings.bootstrap_admin_username,
        password_hash=hash_password(settings.bootstrap_admin_password),
        role=Role.admin,
        must_change_password=True,
    )
    session.add(admin)
    print(
        f"  + bootstrap admin '{admin.username}' created "
        f"(password: '{settings.bootstrap_admin_password}', must change on first login)"
    )


async def _upsert_nvr(
    session: AsyncSession,
    nvr_data: dict,
    defaults: dict,
    region: Region | None,
) -> Nvr:
    nvr_id = nvr_data["id"]
    label = nvr_data.get("label", nvr_id)
    ip = nvr_data["ip"]
    port = int(nvr_data.get("port", defaults.get("default_port", 554)))
    username = nvr_data.get("username", defaults.get("default_username", "admin"))
    password = nvr_data.get("password", defaults.get("default_password", ""))
    vendor_str = (nvr_data.get("vendor") or defaults.get("default_vendor") or "dahua").lower()
    vendor = Vendor(vendor_str) if vendor_str in {v.value for v in Vendor} else Vendor.dahua
    enabled = bool(nvr_data.get("enabled", True))
    group = nvr_data.get("group")
    channels = int(nvr_data.get("channels", 1))

    existing = (await session.execute(select(Nvr).where(Nvr.id == nvr_id))).scalar_one_or_none()
    if existing is None:
        nvr = Nvr(
            id=nvr_id,
            label=label,
            ip=ip,
            port=port,
            rtsp_username=username,
            rtsp_password_encrypted=encrypt_password(password),
            vendor=vendor,
            enabled=enabled,
            group=group,
            region_id=region.id if region else None,
        )
        session.add(nvr)
        await session.flush()
        action = "created"
    else:
        existing.label = label
        existing.ip = ip
        existing.port = port
        existing.rtsp_username = username
        existing.rtsp_password_encrypted = encrypt_password(password)
        existing.vendor = vendor
        existing.enabled = enabled
        existing.group = group
        if region is not None:
            existing.region_id = region.id
        nvr = existing
        action = "updated"

    # Sync cameras to match the `channels: N` count.
    existing_channels = {
        cam.channel: cam
        for cam in (await session.execute(select(Camera).where(Camera.nvr_id == nvr_id))).scalars()
    }
    for ch in range(1, channels + 1):
        if ch not in existing_channels:
            session.add(Camera(nvr_id=nvr_id, channel=ch))
    # We don't auto-delete cameras above the new channel count — operators
    # may have customised camera names; deletion should be explicit via API.

    print(f"  • {nvr_id} {action} ({label}, {channels} ch)")
    return nvr


async def seed(inventory_path: Path, region_slug: str | None) -> None:
    if not inventory_path.exists():
        print(f"Inventory file not found: {inventory_path}", file=sys.stderr)
        sys.exit(1)

    raw = json.loads(inventory_path.read_text(encoding="utf-8"))
    defaults = raw.get("global", {})
    nvrs = raw.get("nvrs", [])

    print(f"Seeding from {inventory_path} ({len(nvrs)} NVRs)")
    async with SessionLocal() as session:
        async with session.begin():
            await _ensure_bootstrap_admin(session)
            region = await _ensure_region(session, region_slug) if region_slug else None
            for nvr_data in nvrs:
                await _upsert_nvr(session, nvr_data, defaults, region)
    print("Done.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed DSS database from nvr_inventory.json")
    default_inventory = get_settings().project_root / "nvr_inventory.json"
    parser.add_argument("--inventory", type=Path, default=default_inventory)
    parser.add_argument(
        "--region-slug",
        default=None,
        help="If set, assign all imported NVRs to this region (creating it if needed).",
    )
    args = parser.parse_args()
    asyncio.run(seed(args.inventory, args.region_slug))


if __name__ == "__main__":
    main()
