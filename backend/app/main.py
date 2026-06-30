"""FastAPI app entrypoint.

Lifespan responsibilities (in order):
  1. Ensure a bootstrap admin exists (only if the user table is empty).
  2. Reconcile relay streams from the DB — go2rtc by default (settings.relay),
     idempotent and tolerant of an unreachable relay (we log and move on; admins
     can retry from POST /mediamtx/reconcile, which is relay-aware).
  3. (Legacy) if `mediamtx_managed=True`, spawn MediaMTX as a child process.

Routers live under `settings.api_prefix` (default `/api/v1`).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models import User, Role  # noqa: F401  (ensure mappers register before create_all)
from app.routers import (
    auth,
    cameras,
    client_log,
    discovery,
    events,
    mediamtx as mediamtx_router,
    nvrs,
    playback as playback_router,
    regions,
    streams,
    users,
)
from app.security import hash_password
from app.services import path_sync, source_watch
from app.services.mediamtx_api import get_client, shutdown_client
from app.settings import get_settings

log = logging.getLogger("dss.main")


async def _ensure_schema() -> None:
    """For SQLite (local dev) we create tables on startup instead of running
    Alembic. Postgres always goes through `alembic upgrade head` — never
    autocreate against it, or future migrations will drift."""
    if engine.dialect.name != "sqlite":
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all never alters existing tables, so columns added after a
        # DB was first created need explicit (idempotent) ALTERs here.
        # Mirrors alembic revision 0002_camera_ip for Postgres.
        cols = [
            row[1]
            for row in (await conn.exec_driver_sql("PRAGMA table_info(cameras)")).fetchall()
        ]
        if "ip" not in cols:
            await conn.exec_driver_sql("ALTER TABLE cameras ADD COLUMN ip VARCHAR(64)")
            log.info("SQLite: added cameras.ip column")
    log.info("SQLite schema ensured via create_all")


async def _ensure_bootstrap_admin() -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        async with session.begin():
            existing = (await session.execute(select(User).limit(1))).scalar_one_or_none()
            if existing is not None:
                return
            session.add(
                User(
                    username=settings.bootstrap_admin_username,
                    password_hash=hash_password(settings.bootstrap_admin_password),
                    role=Role.admin,
                    must_change_password=False,
                )
            )
    log.warning(
        "Bootstrap admin '%s' created — change the password on first login",
        settings.bootstrap_admin_username,
    )


async def _initial_reconcile() -> None:
    """Best-effort stream sync on startup against the active relay. If the relay
    isn't reachable yet, log and continue — the admin can retry once it's up."""
    settings = get_settings()
    if settings.relay == "go2rtc":
        from app.services import go2rtc_api, go2rtc_sync
        try:
            await go2rtc_api.get_client().ping()
        except Exception:  # noqa: BLE001
            log.warning("go2rtc not reachable at startup — skipping initial reconcile")
            return
        async with SessionLocal() as session:
            report = await go2rtc_sync.reconcile(session, delete_orphans=False)
        log.info("Startup reconcile (go2rtc): %s", report)
        return
    client = get_client()
    if not await client.ping():
        log.warning("MediaMTX not reachable at startup — skipping initial reconcile")
        return
    async with SessionLocal() as session:
        report = await path_sync.reconcile(session, delete_orphans=False)
    log.info("Startup reconcile: %s", report.summary())


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every request at INFO — the source watchdog polls MediaMTX
    # every few seconds, so that would flood the console. Keep it to warnings.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Refuse to boot with insecure default secrets (prod) and verify the
    # Fernet key is usable before anything tries to encrypt an NVR password.
    settings.validate_production()

    await _ensure_schema()
    await _ensure_bootstrap_admin()

    if settings.mediamtx_managed:
        from app.services import mediamtx_proc
        mediamtx_proc.start()

    # Recover NVRs the watchdog disabled in a previous session BEFORE the
    # startup reconcile — so the reconcile recreates their MediaMTX paths.
    # (Re-enabling after reconcile would leave them enabled but unstreamable:
    # the paths were removed on auto-disable and nothing would re-add them.)
    await source_watch.reenable_auto_disabled()
    await _initial_reconcile()
    source_watch.start()

    try:
        yield
    finally:
        await source_watch.stop()
        await shutdown_client()
        # go2rtc client owns an httpx pool created lazily during reconcile; close it.
        from app.services import go2rtc_api
        await go2rtc_api.close_client()
        if settings.mediamtx_managed:
            from app.services import mediamtx_proc
            mediamtx_proc.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
    )

    # LAN deployment: operators reach the UI by the host's IP (10.x / 192.168.x /
    # 172.16-31.x) on the frontend port, so the browser's Origin varies per client
    # machine. allow_credentials=True forbids "*", so we match any private-LAN
    # origin (any port) via regex, alongside the explicit cors_origins list.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=r"^http://(localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    prefix = settings.api_prefix
    for r in (auth, regions, users, nvrs, cameras, streams, events, mediamtx_router, discovery, client_log, playback_router):
        app.include_router(r.router, prefix=prefix)

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
