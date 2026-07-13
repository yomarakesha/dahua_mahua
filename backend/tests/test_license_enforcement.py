"""LicenseEnforcementMiddleware — the real teeth of license enforcement.

Proves:
  • default-OFF is a complete no-op: with license_enforcement_enabled=False,
    protected routes are NEVER blocked, regardless of license state.
  • ON + a blocked state → protected routes get HTTP 402 {error, state}.
  • ON + a blocked state → the allow-list (license / auth / branding / health)
    stays reachable so an admin can recover by installing a fresh license.
  • ON + valid/grace → pass-through (grace does NOT block).
"""

from __future__ import annotations

import warnings
from types import SimpleNamespace

import pytest

from app import licensing
from app import settings as settings_mod
from app.middleware import LicenseEnforcementMiddleware

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

PREFIX = "/api/v1"


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(LicenseEnforcementMiddleware)

    @app.get(f"{PREFIX}/nvrs")  # a stand-in PROTECTED route
    async def nvrs() -> dict:
        return {"ok": True}

    @app.get(f"{PREFIX}/license")
    async def license_get() -> dict:
        return {"open": "license"}

    @app.post(f"{PREFIX}/auth/login")
    async def login() -> dict:
        return {"open": "auth"}

    @app.get(f"{PREFIX}/branding")
    async def branding() -> dict:
        return {"open": "branding"}

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"open": "health"}

    @app.get("/readyz")
    async def readyz() -> dict:
        return {"open": "ready"}

    return app


def _configure(monkeypatch, *, enforced: bool, state: str) -> None:
    fake_settings = SimpleNamespace(
        license_enforcement_enabled=enforced,
        license_grace_days=7,
        api_prefix=PREFIX,
    )
    monkeypatch.setattr(settings_mod, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(
        licensing,
        "license_state",
        lambda *a, **k: {"state": state, "days_left": None, "grace_days_left": None},
    )


# ── Default-OFF is a no-op (the safety guarantee) ────────────────────────────
@pytest.mark.parametrize("state", ["expired", "missing", "invalid", "mismatch", "grace", "valid"])
def test_off_never_blocks_regardless_of_state(monkeypatch, state):
    _configure(monkeypatch, enforced=False, state=state)
    r = TestClient(_build_app()).get(f"{PREFIX}/nvrs")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ── ON: blocked states reject the protected route with 402 ───────────────────
@pytest.mark.parametrize("state", ["expired", "missing", "invalid", "mismatch"])
def test_on_blocks_protected_route_with_402(monkeypatch, state):
    _configure(monkeypatch, enforced=True, state=state)
    r = TestClient(_build_app()).get(f"{PREFIX}/nvrs")
    assert r.status_code == 402
    assert r.json() == {"error": "license_blocked", "state": state}


# ── ON: grace / valid pass through ───────────────────────────────────────────
@pytest.mark.parametrize("state", ["grace", "valid"])
def test_on_allows_grace_and_valid(monkeypatch, state):
    _configure(monkeypatch, enforced=True, state=state)
    r = TestClient(_build_app()).get(f"{PREFIX}/nvrs")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ── ON + blocked: the recovery allow-list stays open ─────────────────────────
@pytest.mark.parametrize(
    "method,path",
    [
        ("get", f"{PREFIX}/license"),
        ("post", f"{PREFIX}/auth/login"),
        ("get", f"{PREFIX}/branding"),
        ("get", "/healthz"),
        ("get", "/readyz"),
    ],
)
def test_on_allows_recovery_routes_even_when_blocked(monkeypatch, method, path):
    _configure(monkeypatch, enforced=True, state="missing")
    client = TestClient(_build_app())
    r = getattr(client, method)(path)
    assert r.status_code == 200
    assert "open" in r.json()
