"""GET /readyz readiness probe: DB + relay checks, 200 all-ok else 503."""

from __future__ import annotations

import warnings
from unittest.mock import AsyncMock

import pytest

import app.main as main_mod
from app.services import go2rtc_api

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient


class _FakeSession:
    """Async-context session whose execute() succeeds or raises on demand."""

    def __init__(self, ok: bool):
        self._ok = ok

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        if not self._ok:
            raise RuntimeError("db down")
        return None


def _client() -> TestClient:
    # No `with` → lifespan (schema/bootstrap/reconcile) is skipped; /readyz only
    # needs the route + the patched module globals.
    return TestClient(main_mod.app)


def _patch(monkeypatch, *, db_ok: bool, relay_ok: bool):
    monkeypatch.setattr(main_mod, "SessionLocal", lambda: _FakeSession(db_ok))
    fake = AsyncMock()
    if relay_ok:
        fake.ping = AsyncMock(return_value=None)
    else:
        fake.ping = AsyncMock(side_effect=RuntimeError("relay down"))
    monkeypatch.setattr(go2rtc_api, "get_client", lambda: fake)


def test_readyz_all_ok(monkeypatch):
    _patch(monkeypatch, db_ok=True, relay_ok=True)
    r = _client().get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"db": "ok", "relay": "ok"}


def test_readyz_db_down_is_503(monkeypatch):
    _patch(monkeypatch, db_ok=False, relay_ok=True)
    r = _client().get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["db"] == "fail" and body["relay"] == "ok"


def test_readyz_relay_down_is_503(monkeypatch):
    _patch(monkeypatch, db_ok=True, relay_ok=False)
    r = _client().get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["db"] == "ok" and body["relay"] == "fail"


def test_readyz_never_leaks_internals(monkeypatch):
    _patch(monkeypatch, db_ok=False, relay_ok=False)
    r = _client().get("/readyz")
    # Body is only the fixed status map — no exception text / stack.
    assert set(r.json()) == {"db", "relay"}
    assert all(v in ("ok", "fail") for v in r.json().values())
