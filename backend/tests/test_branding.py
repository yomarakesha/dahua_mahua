"""GET /api/v1/branding — public white-label endpoint.

Verifies the defaults reproduce today's brand (so an un-configured deploy is
visually unchanged) and that the endpoint needs no auth.
"""

from __future__ import annotations

import warnings

import app.main as main_mod

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient


def test_branding_returns_defaults():
    # No BRAND_* env → defaults must equal today's look.
    r = TestClient(main_mod.app).get("/api/v1/branding")
    assert r.status_code == 200
    assert r.json() == {
        "name": "Kanagatly VMS",
        "short": "KM",
        "primary": "#2ecc71",
        "accent": "#43e088",
        "logo_url": "",
    }


def test_branding_is_unauthenticated():
    # No Authorization header — the login page must reach it pre-auth.
    r = TestClient(main_mod.app).get("/api/v1/branding")
    assert r.status_code == 200
