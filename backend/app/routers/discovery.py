"""LAN discovery + bulk import — admin only.

Two endpoints:
  POST /discovery/scan    — run ONVIF + TCP probes, return candidates.
  POST /discovery/import  — create NVRs in bulk from the candidate list.

Import flow rationale: we deliberately do NOT auto-import scan results in
the same call, because the operator should be able to review what was
found and edit channels / labels before committing. The scan + UI choose
pattern also makes the destructive step (writing to inventory) explicit.
"""

from __future__ import annotations

import logging
import re
import time

from fastapi import APIRouter
from sqlalchemy import select

from app.crypto import encrypt_password
from app.deps import AdminUser, SessionDep
from app.models import Camera, Nvr, Vendor
from app.schemas import (
    DiscoveryCandidate,
    DiscoveryImportRequest,
    DiscoveryImportResult,
    DiscoveryScanRequest,
    DiscoveryScanResponse,
)
from app.services import path_sync
from app.services.discovery import (
    Candidate,
    default_cidr,
    detect_dahua_channels,
    tcp_scan,
    ws_discovery,
)
from app.services.rtsp_probe import probe_rtsp

log = logging.getLogger("dss.discovery_api")
router = APIRouter(prefix="/discovery", tags=["discovery"])


def _merge(a: dict[str, Candidate], b: dict[str, Candidate]) -> dict[str, Candidate]:
    """Union of two candidate maps keyed by IP. ONVIF wins for vendor /
    label because it's authoritative; TCP-scan entries contribute only the
    fact that the port is open."""
    out = dict(a)
    for ip, cand in b.items():
        if ip in out:
            for src in cand.sources:
                if src not in out[ip].sources:
                    out[ip].sources.append(src)
        else:
            out[ip] = cand
    return out


def _nvr_id_from_ip(ip: str) -> str:
    """Stable, human-readable PK derived from the IP. Hyphens because the
    pattern in NvrCreate disallows dots."""
    return "nvr-" + re.sub(r"[^a-z0-9]+", "-", ip.lower())


@router.post("/scan", response_model=DiscoveryScanResponse)
async def scan(
    body: DiscoveryScanRequest,
    session: SessionDep,
    _: AdminUser,
) -> DiscoveryScanResponse:
    start = time.perf_counter()
    found: dict[str, Candidate] = {}

    if body.onvif:
        try:
            found = _merge(found, await ws_discovery(timeout=body.timeout))
        except Exception as e:  # noqa: BLE001
            log.warning("WS-Discovery failed: %s", e)

    cidr_used: str | None = None
    if body.tcp:
        cidr_used = body.cidr or default_cidr()
        if cidr_used:
            try:
                found = _merge(found, await tcp_scan(cidr_used, timeout=0.6))
            except Exception as e:  # noqa: BLE001
                log.warning("TCP scan failed: %s", e)
        else:
            log.warning("TCP scan requested but no CIDR provided and autodetect failed")

    # Mark already-known IPs so the UI can dim them.
    known_ips = set(
        (await session.execute(select(Nvr.ip))).scalars().all()
    )

    # Optional channel autodetect — only if creds were provided.
    detected: dict[str, int] = {}
    if body.rtsp_username and body.rtsp_password:
        import asyncio
        async def _detect(ip: str) -> tuple[str, int | None]:
            n = await detect_dahua_channels(ip, body.rtsp_username, body.rtsp_password)
            return ip, n
        results = await asyncio.gather(*[_detect(ip) for ip in found])
        for ip, n in results:
            if n:
                detected[ip] = n

    candidates: list[DiscoveryCandidate] = []
    for ip in sorted(found, key=lambda x: tuple(int(p) for p in x.split("."))):
        c = found[ip]
        candidates.append(DiscoveryCandidate(
            ip=ip,
            port=c.port,
            sources=c.sources,
            vendor_guess=c.vendor_guess,
            label_hint=c.label_hint,
            xaddrs=c.xaddrs,
            scopes=c.scopes,
            detected_channels=detected.get(ip),
            already_known=ip in known_ips,
        ))

    duration_ms = int((time.perf_counter() - start) * 1000)
    log.info("Discovery scan: cidr=%s onvif=%s tcp=%s found=%d in %dms",
             cidr_used, body.onvif, body.tcp, len(candidates), duration_ms)

    return DiscoveryScanResponse(
        cidr_used=cidr_used,
        candidates=candidates,
        duration_ms=duration_ms,
    )


@router.post("/import", response_model=list[DiscoveryImportResult])
async def import_hosts(
    body: DiscoveryImportRequest,
    session: SessionDep,
    _: AdminUser,
) -> list[DiscoveryImportResult]:
    """Create NVRs from a list of discovered hosts.

    `test_first=True` probes RTSP digest auth before writing — hosts that
    fail are skipped, not aborted. Returns a per-host result so the UI can
    show a summary table.
    """
    results: list[DiscoveryImportResult] = []
    created_any = False

    # Pre-load known ids/ips to avoid surprise IntegrityErrors mid-loop.
    existing_ids = set((await session.execute(select(Nvr.id))).scalars().all())
    existing_ips = set((await session.execute(select(Nvr.ip))).scalars().all())

    for item in body.hosts:
        nvr_id = item.nvr_id or _nvr_id_from_ip(item.ip)
        if nvr_id in existing_ids:
            results.append(DiscoveryImportResult(
                ip=item.ip, nvr_id=nvr_id, ok=False,
                message=f"NVR id '{nvr_id}' already exists",
            ))
            continue
        if item.ip in existing_ips:
            results.append(DiscoveryImportResult(
                ip=item.ip, nvr_id=nvr_id, ok=False,
                message=f"IP {item.ip} already used by another NVR",
            ))
            continue

        if body.test_first:
            import asyncio
            probe = await asyncio.to_thread(
                probe_rtsp,
                item.ip,
                item.port,
                body.rtsp_username,
                body.rtsp_password,
                1,
                item.vendor,
                f"[{nvr_id}]",
            )
            if not probe.ok:
                results.append(DiscoveryImportResult(
                    ip=item.ip, nvr_id=nvr_id, ok=False,
                    message=f"RTSP probe failed: {probe.message}",
                ))
                continue

        nvr = Nvr(
            id=nvr_id,
            label=item.label or item.ip,
            ip=item.ip,
            port=item.port,
            rtsp_username=body.rtsp_username,
            rtsp_password_encrypted=encrypt_password(body.rtsp_password),
            vendor=item.vendor,
            group=item.group,
            region_id=body.region_id,
        )
        session.add(nvr)
        for ch in range(1, item.channels + 1):
            session.add(Camera(nvr_id=nvr_id, channel=ch))
        try:
            await session.commit()
        except Exception as e:  # noqa: BLE001
            await session.rollback()
            results.append(DiscoveryImportResult(
                ip=item.ip, nvr_id=nvr_id, ok=False,
                message=f"DB error: {e}",
            ))
            continue
        existing_ids.add(nvr_id)
        existing_ips.add(item.ip)
        created_any = True
        results.append(DiscoveryImportResult(
            ip=item.ip, nvr_id=nvr_id, ok=True,
            message=f"created with {item.channels} channels",
        ))

    # One reconcile at the end (not per host) — cheap and idempotent.
    if created_any:
        await path_sync.reconcile(session, delete_orphans=False)

    return results
