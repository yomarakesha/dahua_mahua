"""Unit tests for the warm-stream pool (WarmPool).

All network is mocked — ``_drain_once`` is replaced by a coroutine that blocks
until cancelled, so a "warming" task is one that exists in ``pool._tasks``. No
real go2rtc / NVR calls are made.

asyncio_mode=auto (pytest.ini) → async tests need no decorator.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
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
    """A started WarmPool whose _drain_once blocks forever (until cancelled), so
    each worker task simply persists in _tasks — lets us assert the diff logic
    without touching the network.

    Instrumentation:
      * ``_opened``   — every sub_name whose drain was (re)opened.
      * ``_open_now`` — live drains per NVR RIGHT NOW (finally-decremented on
        cancel), i.e. the real concurrent-pull count against that NVR.
      * ``_max_open`` — the peak of ``_open_now`` ever seen per NVR. This is the
        cap-overshoot witness: it captures the transient state, so a flip that
        briefly stacks pulls over the cap is caught even though the final _tasks
        count settles back down.
      * ``_open_total`` / ``_max_open_total`` — the same, summed across ALL NVRs
        (the true global concurrent-pull count + its peak) for the global cap.
        NOTE: peaks on different NVRs need not be simultaneous, so the GLOBAL
        peak must be measured directly, not by summing per-NVR peaks.
    """
    p = WarmPool()
    p.start()  # production always starts the pool before use; the guard needs it
    opened: list[str] = []
    open_now: dict[str, int] = defaultdict(int)
    max_open: dict[str, int] = defaultdict(int)
    totals = {"now": 0, "max": 0}

    async def _fake_drain(sub_name: str) -> None:
        opened.append(sub_name)
        nvr = sub_name.rsplit("_ch", 1)[0]
        open_now[nvr] += 1
        max_open[nvr] = max(max_open[nvr], open_now[nvr])
        totals["now"] += 1
        totals["max"] = max(totals["max"], totals["now"])
        try:
            await asyncio.Event().wait()  # block until the worker is cancelled
        finally:
            open_now[nvr] -= 1
            totals["now"] -= 1

    monkeypatch.setattr(p, "_drain_once", _fake_drain)
    p._opened = opened  # type: ignore[attr-defined]
    p._open_now = open_now  # type: ignore[attr-defined]
    p._max_open = max_open  # type: ignore[attr-defined]
    p._totals = totals  # type: ignore[attr-defined]
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


# ── cap holds under page-flip churn (the shipped overshoot bug) ──────────────


async def test_per_nvr_cap_holds_under_page_flip(pool, monkeypatch):
    """Regression: an NVR filled to per_nvr_max, then flipped to a DIFFERENT full
    set on the SAME NVR, must NEVER run more than per_nvr_max live pulls — the
    de-selected pulls must be torn down BEFORE the new ones start.

    Pre-fix, the de-selected ch1–8 sat in grace (still pulling) while ch9–16
    started immediately → 16 concurrent pulls on one NVR. Here _max_open['T']
    would hit 16; it must stay at 8.
    """
    _set_settings(monkeypatch, global_max=24, per_nvr_max=8, grace=10.0)
    T = "T"  # testik
    await pool.set_desired([(T, ch) for ch in range(1, 9)])   # ch1–8, at cap
    await asyncio.sleep(0)  # let the 8 workers open their drains
    assert pool._open_now[T] == 8

    # Flip the whole page to a fresh full set on the SAME NVR.
    await pool.set_desired([(T, ch) for ch in range(9, 17)])  # ch9–16
    await asyncio.sleep(0)  # let the new workers open their drains

    # Final live pulls == cap (count _tasks, which INCLUDES any in-grace pull).
    live = [k for k in pool._tasks if k[0] == T]
    assert len(live) == 8, f"expected 8 live pulls, got {len(live)}: {live}"
    assert pool._open_now[T] == 8
    # The witness: concurrency never transiently exceeded the per-NVR cap.
    assert pool._max_open[T] <= 8, f"cap overshoot: peaked at {pool._max_open[T]}"
    # The de-selected set was reclaimed (not left pulling in grace).
    assert not any(k[0] == T and k[1] <= 8 for k in pool._tasks)
    await pool.close_all()


async def test_rapid_flip_churn_never_stacks_grace_piles(pool, monkeypatch):
    """Rapid repeated flips must not stack grace piles above the cap."""
    _set_settings(monkeypatch, global_max=24, per_nvr_max=8, grace=10.0)
    T = "T"
    for base in (1, 9, 17, 25, 1, 9):  # six back-to-back full-page flips
        await pool.set_desired([(T, ch) for ch in range(base, base + 8)])
        await asyncio.sleep(0)
        assert len([k for k in pool._tasks if k[0] == T]) <= 8
    assert pool._max_open[T] <= 8, f"cap overshoot: peaked at {pool._max_open[T]}"
    await pool.close_all()


async def test_global_cap_holds_under_cross_nvr_flip(pool, monkeypatch):
    """The GLOBAL cap must also count in-grace pulls across NVRs under churn."""
    _set_settings(monkeypatch, global_max=8, per_nvr_max=8, grace=10.0)
    # Fill the global cap: 4 on A + 4 on B = 8.
    await pool.set_desired([("A", 1), ("A", 2), ("A", 3), ("A", 4),
                            ("B", 1), ("B", 2), ("B", 3), ("B", 4)])
    await asyncio.sleep(0)
    assert len(pool._tasks) == 8
    # Flip to a fresh full global set on two other NVRs.
    await pool.set_desired([("C", 1), ("C", 2), ("C", 3), ("C", 4),
                            ("D", 1), ("D", 2), ("D", 3), ("D", 4)])
    await asyncio.sleep(0)
    assert len(pool._tasks) == 8, f"global overshoot: {len(pool._tasks)} live pulls"
    assert pool._totals["max"] <= 8, (
        f"global cap overshoot: peaked at {pool._totals['max']}"
    )
    await pool.close_all()


async def test_flip_keeps_grace_warm_when_room(pool, monkeypatch):
    """With headroom under the cap, de-selected pulls STAY warm in grace (the
    working page-flip optimisation must be preserved, not over-cancelled)."""
    _set_settings(monkeypatch, global_max=24, per_nvr_max=8, grace=10.0)
    T = "T"
    await pool.set_desired([(T, 1), (T, 2)])       # 2 warm
    await asyncio.sleep(0)
    await pool.set_desired([(T, 3), (T, 4)])       # flip to 2 new — 2+2=4 ≤ 8
    await asyncio.sleep(0)
    # ch1,ch2 kept warm in grace (room under the cap); ch3,ch4 accepted.
    assert (T, 1) in pool._grace and (T, 2) in pool._grace
    assert (T, 1) in pool._tasks and (T, 2) in pool._tasks
    assert (T, 3) in pool._tasks and (T, 4) in pool._tasks
    assert pool._max_open[T] <= 8
    await pool.close_all()


async def test_set_desired_noop_when_not_started(monkeypatch):
    """A stray set_desired after close_all (or before start) is a no-op — it must
    not resurrect the pool or open any pull."""
    _set_settings(monkeypatch)
    p = WarmPool()  # never started
    result = await p.set_desired([("nvr01", 1)])
    assert result == {"warming": 0, "capped": 0}
    assert p._tasks == {}
    assert p._desired == []


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
