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
