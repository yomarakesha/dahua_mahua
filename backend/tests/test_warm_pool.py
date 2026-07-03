"""Unit tests for the warm-stream pool (WarmPool).

All network is mocked — ``_drain_once`` is replaced by a coroutine that blocks
until cancelled, so a "warming" task is one that exists in ``pool._tasks``. No
real go2rtc / NVR calls are made.

asyncio_mode=auto (pytest.ini) → async tests need no decorator.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.warm_pool import WarmPool, sub_stream_name


def _settings(*, global_max=24, per_nvr_max=8, grace=10.0, enabled=True):
    return SimpleNamespace(
        warm_pool_enabled=enabled,
        warm_pool_max_streams=global_max,
        warm_pool_per_nvr_max=per_nvr_max,
        warm_pool_drop_grace_seconds=grace,
        go2rtc_api_url="http://127.0.0.1:1984",
    )


@pytest.fixture
def pool(monkeypatch):
    """A WarmPool whose _drain_once blocks forever (until cancelled), so each
    worker task simply persists in _tasks — lets us assert the diff logic without
    touching the network. Records every sub_name opened."""
    p = WarmPool()
    opened: list[str] = []

    async def _fake_drain(sub_name: str) -> None:
        opened.append(sub_name)
        await asyncio.Event().wait()  # block until the worker is cancelled

    monkeypatch.setattr(p, "_drain_once", _fake_drain)
    p._opened = opened  # type: ignore[attr-defined]
    return p


def _set_settings(monkeypatch, **kw):
    monkeypatch.setattr(
        "app.services.warm_pool.get_settings", lambda: _settings(**kw)
    )


# ── stream-name contract ─────────────────────────────────────────────────────


def test_sub_stream_name_format():
    """MUST match path_sync.path_name for SUB: {nvr_id}_ch{channel}."""
    assert sub_stream_name("nvr01", 5) == "nvr01_ch5"


# ── set_desired diffing ──────────────────────────────────────────────────────


async def test_set_desired_starts_tasks(pool, monkeypatch):
    _set_settings(monkeypatch)
    result = await pool.set_desired([("nvr01", 1), ("nvr01", 2)])
    await asyncio.sleep(0)  # let workers start + call _drain_once
    assert result == {"warming": 2, "capped": 0}
    assert set(pool._tasks) == {("nvr01", 1), ("nvr01", 2)}
    assert set(pool._opened) == {"nvr01_ch1", "nvr01_ch2"}
    await pool.close_all()


async def test_set_desired_is_idempotent(pool, monkeypatch):
    _set_settings(monkeypatch)
    await pool.set_desired([("nvr01", 1)])
    task = pool._tasks[("nvr01", 1)]
    # Re-request the SAME key → must NOT restart the worker (same task object).
    await pool.set_desired([("nvr01", 1)])
    assert pool._tasks[("nvr01", 1)] is task
    await pool.close_all()


async def test_set_desired_dedupes_input(pool, monkeypatch):
    _set_settings(monkeypatch)
    result = await pool.set_desired([("nvr01", 1), ("nvr01", 1)])
    assert result["warming"] == 1
    assert set(pool._tasks) == {("nvr01", 1)}
    await pool.close_all()


async def test_deselect_schedules_grace_not_immediate(pool, monkeypatch):
    _set_settings(monkeypatch, grace=10.0)
    await pool.set_desired([("nvr01", 1)])
    # Drop it → worker must still be running during the grace window.
    await pool.set_desired([])
    assert ("nvr01", 1) in pool._tasks  # NOT torn down yet
    assert ("nvr01", 1) in pool._grace
    await pool.close_all()


async def test_deselect_tears_down_after_grace(pool, monkeypatch):
    _set_settings(monkeypatch, grace=0.05)
    await pool.set_desired([("nvr01", 1)])
    await pool.set_desired([])
    # Wait past the drop-grace → worker cancelled + removed.
    await asyncio.sleep(0.15)
    assert ("nvr01", 1) not in pool._tasks
    assert ("nvr01", 1) not in pool._grace
    await pool.close_all()


async def test_reselect_within_grace_keeps_worker(pool, monkeypatch):
    _set_settings(monkeypatch, grace=10.0)
    await pool.set_desired([("nvr01", 1)])
    task = pool._tasks[("nvr01", 1)]
    await pool.set_desired([])              # de-select → grace scheduled
    await pool.set_desired([("nvr01", 1)])  # re-select before grace fires
    assert ("nvr01", 1) not in pool._grace  # grace cancelled
    assert pool._tasks[("nvr01", 1)] is task  # SAME worker — never re-dialled
    await pool.close_all()


# ── caps ─────────────────────────────────────────────────────────────────────


async def test_global_cap_warms_subset_and_reports_dropped(pool, monkeypatch):
    _set_settings(monkeypatch, global_max=2, per_nvr_max=8)
    result = await pool.set_desired(
        [("a", 1), ("a", 2), ("a", 3), ("a", 4)]
    )
    assert result == {"warming": 2, "capped": 2}
    # Priority order preserved: the first two win.
    assert set(pool._tasks) == {("a", 1), ("a", 2)}
    assert [k for k, _r in pool._last_dropped] == [("a", 3), ("a", 4)]
    assert all(r == "global_cap" for _k, r in pool._last_dropped)
    await pool.close_all()


async def test_per_nvr_cap_enforced(pool, monkeypatch):
    _set_settings(monkeypatch, global_max=24, per_nvr_max=2)
    # 3 on nvrA (1 over per-nvr cap) + 1 on nvrB (fits).
    result = await pool.set_desired(
        [("A", 1), ("A", 2), ("A", 3), ("B", 1)]
    )
    assert result == {"warming": 3, "capped": 1}
    assert set(pool._tasks) == {("A", 1), ("A", 2), ("B", 1)}
    assert pool._last_dropped == [(("A", 3), "per_nvr_cap")]
    await pool.close_all()


async def test_cap_drop_is_not_silent(pool, monkeypatch, caplog):
    _set_settings(monkeypatch, global_max=1, per_nvr_max=8)
    import logging

    with caplog.at_level(logging.WARNING, logger="dss.warm_pool"):
        await pool.set_desired([("a", 1), ("a", 2)])
    assert any("caps hit" in r.message or "dropped" in r.message for r in caplog.records)
    await pool.close_all()


# ── shutdown ─────────────────────────────────────────────────────────────────


async def test_close_all_cancels_every_worker(pool, monkeypatch):
    _set_settings(monkeypatch)
    await pool.set_desired([("nvr01", 1), ("nvr02", 1)])
    tasks = list(pool._tasks.values())
    await pool.close_all()
    assert pool._tasks == {}
    assert pool._grace == {}
    assert all(t.cancelled() or t.done() for t in tasks)


async def test_close_all_cancels_pending_grace(pool, monkeypatch):
    _set_settings(monkeypatch, grace=100.0)
    await pool.set_desired([("nvr01", 1)])
    await pool.set_desired([])  # grace timer pending
    assert pool._grace
    await pool.close_all()
    assert pool._grace == {}


async def test_close_all_idempotent(pool, monkeypatch):
    _set_settings(monkeypatch)
    await pool.set_desired([("nvr01", 1)])
    await pool.close_all()
    await pool.close_all()  # must not raise
    assert pool._tasks == {}


# ── stats ────────────────────────────────────────────────────────────────────


async def test_stats_snapshot(pool, monkeypatch):
    _set_settings(monkeypatch, global_max=10, per_nvr_max=4)
    await pool.set_desired([("A", 1), ("A", 2), ("B", 1)])
    s = pool.stats()
    assert s["warming"] == 3
    assert s["desired"] == 3
    assert s["per_nvr"] == {"A": 2, "B": 1}
    assert s["caps"] == {"global_max": 10, "per_nvr_max": 4}
    await pool.close_all()
