"""Lightweight HTTP middleware.

``RequestIDMiddleware`` tags every HTTP request with a short correlation id
(uuid4 hex, 8 chars), exposes it as the ``X-Request-ID`` response header, and on
an unhandled exception logs the traceback WITH that id and returns a JSON 500
body ``{"detail": "internal error", "request_id": "<id>"}`` so a field report
can be tied back to the ``dss.request`` log line.

WebSocket connections are left untouched — the ASGI dispatch only wraps HTTP.
"""

from __future__ import annotations

import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

log = logging.getLogger("dss.request")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = uuid.uuid4().hex[:8]
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001 — log with correlation id, return JSON 500
            log.exception(
                "unhandled error request_id=%s %s %s",
                request_id,
                request.method,
                request.url.path,
            )
            return JSONResponse(
                status_code=500,
                content={"detail": "internal error", "request_id": request_id},
                headers={"X-Request-ID": request_id},
            )
        response.headers["X-Request-ID"] = request_id
        return response


class LicenseEnforcementMiddleware(BaseHTTPMiddleware):
    """Hard-block the protected API when the license is not runnable.

    NO-OP unless ``settings.license_enforcement_enabled`` is True — so the live
    deployment (running today with no license installed) is completely unaffected
    until an operator opts in. When enforcement is ON and the current license
    state is one of ``licensing.BLOCKED_STATES`` (expired-past-grace / missing /
    invalid / mismatch), every request is rejected with **HTTP 402 Payment
    Required** and a JSON body ``{"error": "license_blocked", "state": "<state>"}``
    — except an allow-list that MUST stay reachable so an admin can recover:

      • the license endpoints  (…/license  — GET status + POST activate),
      • the auth endpoints     (…/auth/*   — log in),
      • branding               (…/branding — the block screen themes itself),
      • liveness/readiness      (/healthz, /health, /readyz).

    The ``grace`` and ``valid`` states never block. WebSocket scopes are untouched
    (BaseHTTPMiddleware only wraps HTTP); the REST inventory routes are blocked, so
    the app is unusable regardless — and a WS can't carry a 402 anyway.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        from app import licensing
        from app.settings import get_settings

        settings = get_settings()
        if not settings.license_enforcement_enabled:
            return await call_next(request)

        if self._is_allowed(request.url.path, settings.api_prefix):
            return await call_next(request)

        try:
            info = licensing.license_state(
                grace_days=settings.license_grace_days,
            )
            state = info.get("state", "invalid")
        except Exception:  # noqa: BLE001 — never let the gate itself crash the API
            log.exception("license enforcement check failed — allowing request")
            return await call_next(request)

        if state in licensing.BLOCKED_STATES:
            return JSONResponse(
                status_code=402,
                content={"error": "license_blocked", "state": state},
            )
        return await call_next(request)

    @staticmethod
    def _is_allowed(path: str, api_prefix: str) -> bool:
        # Always-open liveness/readiness probes (no prefix).
        if path in ("/healthz", "/health", "/readyz"):
            return True
        prefix = api_prefix.rstrip("/")
        # …/license and …/license/* (status + activation), …/auth/* (login),
        # …/branding (block-screen theming).
        for sub in ("/license", "/auth", "/branding"):
            base = f"{prefix}{sub}"
            if path == base or path.startswith(base + "/"):
                return True
        return False
