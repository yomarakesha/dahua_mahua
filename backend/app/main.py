"""FastAPI app entrypoint.

Lifespan responsibilities (in order):
  1. Ensure a bootstrap admin exists (only if the user table is empty).
  2. If `mediamtx_managed=True`, spawn MediaMTX as a child process.
  3. Reconcile MediaMTX paths from the DB — idempotent and tolerant of an
     unreachable MediaMTX (we just log and move on; admins can retry from
     POST /mediamtx/reconcile).

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
    regions,
    streams,
    users,
)
from app.security import hash_password
from app.services import path_sync
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
                    must_change_password=True,
                )
            )
    log.warning(
        "Bootstrap admin '%s' created — change the password on first login",
        settings.bootstrap_admin_username,
    )


async def _initial_reconcile() -> None:
    """Best-effort path sync on startup. If MediaMTX isn't reachable yet
    (e.g. docker-compose race), we log and continue — the admin can retry
    via POST /api/v1/mediamtx/reconcile once it comes up."""
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

    await _ensure_schema()
    await _ensure_bootstrap_admin()

    if settings.mediamtx_managed:
        from app.services import mediamtx_proc
        mediamtx_proc.start()

    await _initial_reconcile()

    try:
        yield
    finally:
        await shutdown_client()
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    prefix = settings.api_prefix
    for r in (auth, regions, users, nvrs, cameras, streams, events, mediamtx_router, discovery, client_log):
        app.include_router(r.router, prefix=prefix)

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
