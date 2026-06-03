"""Receive diagnostic logs from the browser and write them to dss.client.

Unauthenticated on purpose: pre-login errors (failed /auth/login, network
failures from the login page) need to be captured too. Payload size is
capped so a misbehaving client can't fill the disk.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from threading import Lock
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

log = logging.getLogger("dss.client")

router = APIRouter(prefix="/client-log", tags=["meta"])

_MAX_ENTRIES_PER_REQUEST = 500
_MAX_FIELD_LEN = 2000

# This endpoint is unauthenticated, so cap how often any single IP can post —
# otherwise an anonymous client can flood the log / fill the disk.
_RATE_MAX_REQUESTS = 30
_RATE_WINDOW_SECONDS = 10.0
_rate_hits: dict[str, list[float]] = defaultdict(list)
_rate_lock = Lock()


def _rate_limited(ip: str) -> bool:
    now = time.time()
    with _rate_lock:
        hits = [t for t in _rate_hits[ip] if now - t < _RATE_WINDOW_SECONDS]
        if len(hits) >= _RATE_MAX_REQUESTS:
            _rate_hits[ip] = hits
            return True
        hits.append(now)
        _rate_hits[ip] = hits
        return False


def _sanitize(s: str) -> str:
    """Strip CR/LF so a client can't forge extra log lines (log injection)."""
    return s.replace("\r", " ").replace("\n", " ")


class ClientLogEntry(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    ts: str | None = None  # client-side HH:MM:SS.mmm; advisory only
    path: str = ""         # MediaMTX path / NVR id the entry is about, optional
    msg: str = Field(default="", max_length=_MAX_FIELD_LEN)
    detail: str = Field(default="", max_length=_MAX_FIELD_LEN)


class ClientLogBatch(BaseModel):
    entries: list[ClientLogEntry] = Field(default_factory=list, max_length=_MAX_ENTRIES_PER_REQUEST)


_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def ingest(batch: ClientLogBatch, request: Request) -> None:
    client_ip = request.client.host if request.client else "?"
    if _rate_limited(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many client-log requests",
        )
    for e in batch.entries:
        # Tag every line with the client IP and the optional path so logs can
        # be filtered by NVR or by the browser session they came from.
        prefix = f"[{client_ip}]"
        if e.path:
            prefix += f"[{_sanitize(e.path)}]"
        msg = f"{prefix} {_sanitize(e.msg)}"
        if e.detail:
            msg = f"{msg} | {_sanitize(e.detail)}"
        log.log(_LEVEL_MAP.get(e.level, logging.INFO), msg)
