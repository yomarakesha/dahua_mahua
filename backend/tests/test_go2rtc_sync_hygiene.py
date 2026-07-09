"""Concurrency guard on reconcile + file-mode orphan cleanup on NVR delete."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.services.go2rtc_sync as gs


# ── Item 2: reconcile is serialized by a module-level lock ─────────────────────

@pytest.mark.asyncio
async def test_reconcile_is_serialized(monkeypatch):
    """Two concurrent reconciles must not overlap: the lock forces the second to
    wait until the first releases (max concurrent bodies == 1)."""
    active = 0
    max_active = 0

    async def fake_impl(session, *, client=None, delete_orphans=True):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)  # hold the lock long enough to collide
        active -= 1
        return {"ok": True}

    monkeypatch.setattr(gs, "_reconcile_impl", fake_impl)

    await asyncio.gather(gs.reconcile(None), gs.reconcile(None))
    assert max_active == 1


# ── Item 5: NVR delete rewrites the YAML in file-managed modes ─────────────────

@pytest.mark.asyncio
async def test_remove_streams_file_mode_triggers_reconcile(monkeypatch):
    """Re-encode/exec mode keeps streams in the YAML; API deletes would orphan
    them. remove_streams_for_nvr must run the file-mode reconcile instead."""
    monkeypatch.setattr(gs, "get_settings", lambda: SimpleNamespace(
        reencode_enabled=True, main_stream_mode="native",
    ))
    calls = {}

    async def fake_reconcile(session, *, delete_orphans=True):
        calls["delete_orphans"] = delete_orphans
        return {}

    monkeypatch.setattr(gs, "reconcile", fake_reconcile)
    # If it tried the API path it would call get_client — make that explode.
    monkeypatch.setattr(gs, "get_client", lambda: (_ for _ in ()).throw(AssertionError("used API")))

    await gs.remove_streams_for_nvr(None, "nvr-1")
    assert calls == {"delete_orphans": True}


@pytest.mark.asyncio
async def test_remove_streams_api_mode_deletes_via_client(monkeypatch):
    """Plain (non-exec, native) mode still deletes the NVR's streams via the API."""
    monkeypatch.setattr(gs, "get_settings", lambda: SimpleNamespace(
        reencode_enabled=False, main_stream_mode="native",
    ))

    deleted: list[str] = []

    class FakeClient:
        async def list_streams(self):
            return {"nvr-1_ch1": "", "nvr-1_ch2_main": "", "other_ch1": ""}

        async def delete_stream(self, name):
            deleted.append(name)

    monkeypatch.setattr(gs, "get_client", lambda: FakeClient())
    # reconcile must NOT be used in API mode.
    async def _boom(*a, **k):
        raise AssertionError("reconcile should not run in API mode")
    monkeypatch.setattr(gs, "reconcile", _boom)

    await gs.remove_streams_for_nvr(None, "nvr-1")
    assert set(deleted) == {"nvr-1_ch1", "nvr-1_ch2_main"}
