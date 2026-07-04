"""FastAPI app entrypoint.

Lifespan responsibilities (in order):
  1. Ensure a bootstrap admin exists (only if the user table is empty).
  2. Reconcile go2rtc streams from the DB — idempotent and tolerant of an
     unreachable relay (we log and move on; admins can retry from
     POST /nvrs/reconcile).

Routers live under `settings.api_prefix` (default `/api/v1`).
"""

from __future__ import annotations

import logging
import logging.handlers
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text

from app.db import Base, SessionLocal, engine
from app.middleware import RequestIDMiddleware
from app.models import User, Role  # noqa: F401  (ensure mappers register before create_all)
from app.routers import (
    auth,
    cameras,
    client_log,
    discovery,
    events,
    live as live_router,
    nvrs,
    playback as playback_router,
    regions,
    streams,
    users,
)
from app.security import hash_password
from app.services import source_watch
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
    """Best-effort go2rtc stream sync on startup. If go2rtc isn't reachable yet,
    log and continue — the admin can retry once it's up."""
    from app.services import go2rtc_api, go2rtc_sync
    try:
        await go2rtc_api.get_client().ping()
    except Exception:  # noqa: BLE001
        log.warning("go2rtc not reachable at startup — skipping initial reconcile")
        return
    async with SessionLocal() as session:
        report = await go2rtc_sync.reconcile(session, delete_orphans=False)
    log.info("Startup reconcile (go2rtc): %s", report)


def _configure_logging(settings) -> None:
    """Set up stream (stderr) + optional rotating-file logging.

    NSSM does not persist stderr, so a RotatingFileHandler keeps bounded logs on
    disk. On Windows rotation can fail if another process holds the file, so the
    handler uses ``delay=True`` (open lazily) and we degrade gracefully to
    stderr-only if the log dir isn't writable — never crash startup on logging.
    """
    level = logging.DEBUG if settings.debug else logging.INFO
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    formatter = logging.Formatter(fmt)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    handlers: list[logging.Handler] = [stream_handler]

    if settings.log_file:
        try:
            log_path = Path(settings.log_file)
            if log_path.parent and not log_path.parent.exists():
                log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_path,
                maxBytes=settings.log_file_max_bytes,
                backupCount=settings.log_file_backup_count,
                encoding="utf-8",
                delay=True,  # open lazily — avoids a Windows file-lock on boot
            )
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)
        except OSError:
            log.warning(
                "Log file %s not writable — continuing with stderr only",
                settings.log_file,
                exc_info=True,
            )

    logging.basicConfig(level=level, handlers=handlers, force=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _configure_logging(settings)
    # httpx logs every request at INFO — the source watchdog polls go2rtc
    # every few seconds, so that would flood the console. Keep it to warnings.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Refuse to boot with insecure default secrets (prod) and verify the
    # Fernet key is usable before anything tries to encrypt an NVR password.
    settings.validate_production()

    await _ensure_schema()
    await _ensure_bootstrap_admin()

    # Recover NVRs the watchdog disabled in a previous session BEFORE the
    # startup reconcile — so the reconcile recreates their go2rtc streams.
    # (Re-enabling after reconcile would leave them enabled but unstreamable
    # until the next reconcile.)
    await source_watch.reenable_auto_disabled()
    await _initial_reconcile()
    source_watch.start()

    from app.services.playback import nvr_budget as _pb_budget
    _pb_budget.init_budget(
        per_nvr=settings.playback_nvr_budget,
        global_cap=settings.playback_global_cap,
    )
    log.info(
        "NvrBudget initialised: per_nvr=%d global=%d",
        settings.playback_nvr_budget,
        settings.playback_global_cap,
    )

    from app.services.playback import session as _pb_session
    _pb_session.start_reaper(
        idle_timeout=settings.playback_idle_timeout_seconds,
        max_lifetime=settings.playback_max_lifetime_seconds,
    )
    log.info(
        "Playback reaper started: idle=%ds max_lifetime=%ds",
        settings.playback_idle_timeout_seconds,
        settings.playback_max_lifetime_seconds,
    )

    # Warm-stream pool: idles with an empty desired set until the frontend posts
    # to /live/warm. Always started (cheap when idle); the endpoint is a no-op
    # while warm_pool_enabled is False, so nothing is warmed until opted in.
    from app.services.warm_pool import get_warm_pool
    _warm_pool = get_warm_pool()
    _warm_pool.start()
    log.info(
        "Warm pool started: enabled=%s max_streams=%d per_nvr_max=%d",
        settings.warm_pool_enabled,
        settings.warm_pool_max_streams,
        settings.warm_pool_per_nvr_max,
    )

    try:
        yield
    finally:
        await _warm_pool.close_all()
        await _pb_session.stop_reaper()
        # No orphan ffmpeg: close every active playback session (Contract #11).
        await _pb_session.close_all()
        await source_watch.stop()
        # go2rtc client owns an httpx pool created lazily during reconcile; close it.
        from app.services import go2rtc_api
        await go2rtc_api.close_client()


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
    # Correlation id + JSON 500 on unhandled HTTP errors. Added before CORS so
    # it runs innermost (closest to the route); WebSocket routes are untouched.
    app.add_middleware(RequestIDMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=r"^http://(localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    prefix = settings.api_prefix
    for r in (auth, regions, users, nvrs, cameras, streams, events, discovery, client_log, playback_router, live_router):
        app.include_router(r.router, prefix=prefix)

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz", tags=["meta"])
    async def readyz(response: Response) -> dict:
        """Readiness probe: verifies the backend can actually serve traffic, not
        just that the process is up (that's /healthz). Checks the DB (a trivial
        SELECT 1) and that the go2rtc relay API answers. Returns 200 only if every
        check is "ok", else 503. Never raises and never leaks internal error
        detail — the body is a fixed per-check status map."""
        checks: dict[str, str] = {}

        try:
            async with SessionLocal() as session:
                await session.execute(text("SELECT 1"))
            checks["db"] = "ok"
        except Exception:  # noqa: BLE001
            checks["db"] = "fail"

        try:
            from app.services import go2rtc_api
            await go2rtc_api.get_client().ping()
            checks["relay"] = "ok"
        except Exception:  # noqa: BLE001
            checks["relay"] = "fail"

        if any(v != "ok" for v in checks.values()):
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return checks

    return app


app = create_app()
