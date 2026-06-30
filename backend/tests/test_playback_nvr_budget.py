"""Unit tests for NvrBudget — per-NVR + global playback semaphore.

All tests are async (asyncio_mode=auto in pytest.ini).
"""

import pytest

from app.services.playback.nvr_budget import BudgetExhausted, NvrBudget


# ── 1. Fresh instance starts at zero ────────────────────────────────────────


async def test_initial_global_active_is_zero():
    budget = NvrBudget(per_nvr=2, global_cap=4)
    assert budget.global_active() == 0


# ── 2. Two successful acquires on the same NVR ───────────────────────────────


async def test_two_acquires_same_nvr():
    budget = NvrBudget(per_nvr=2, global_cap=4)
    await budget.try_acquire("nvr01")
    await budget.try_acquire("nvr01")
    assert budget.active_count("nvr01") == 2


# ── 3. Third acquire on same NVR raises BudgetExhausted ──────────────────────


async def test_third_acquire_same_nvr_raises():
    budget = NvrBudget(per_nvr=2, global_cap=4)
    await budget.try_acquire("nvr01")
    await budget.try_acquire("nvr01")
    with pytest.raises(BudgetExhausted):
        await budget.try_acquire("nvr01")


# ── 4. Release frees a slot so the next acquire succeeds ────────────────────


async def test_release_frees_slot():
    budget = NvrBudget(per_nvr=2, global_cap=4)
    await budget.try_acquire("nvr01")
    await budget.try_acquire("nvr01")
    await budget.release("nvr01")
    # Now one slot is free — should not raise
    await budget.try_acquire("nvr01")
    assert budget.active_count("nvr01") == 2


# ── 5. Global cap: four acquires across four NVRs exhaust it; fifth raises ───


async def test_global_cap_exhausted():
    budget = NvrBudget(per_nvr=2, global_cap=4)
    await budget.try_acquire("nvr01")
    await budget.try_acquire("nvr02")
    await budget.try_acquire("nvr03")
    await budget.try_acquire("nvr04")
    assert budget.global_active() == 4
    with pytest.raises(BudgetExhausted):
        await budget.try_acquire("nvr05")


# ── 6. session() context manager: count increments on enter, decrements on exit


async def test_session_context_manager_increments_and_decrements():
    budget = NvrBudget(per_nvr=2, global_cap=4)
    async with budget.session("nvr01"):
        assert budget.active_count("nvr01") == 1
        assert budget.global_active() == 1
    assert budget.active_count("nvr01") == 0
    assert budget.global_active() == 0


# ── 7. Exception inside session() still releases the slot ────────────────────


async def test_session_releases_on_exception():
    budget = NvrBudget(per_nvr=2, global_cap=4)
    with pytest.raises(RuntimeError):
        async with budget.session("nvr01"):
            assert budget.active_count("nvr01") == 1
            raise RuntimeError("simulated failure")
    assert budget.active_count("nvr01") == 0
    assert budget.global_active() == 0


# ── 8. release() on never-acquired nvr_id is a no-op ─────────────────────────


async def test_release_never_acquired_is_noop():
    budget = NvrBudget(per_nvr=2, global_cap=4)
    # Must not raise; count stays 0
    await budget.release("nvr-unknown")
    assert budget.active_count("nvr-unknown") == 0
    assert budget.global_active() == 0


# ── 9. global_active() tracks the sum across NVRs ────────────────────────────


async def test_global_active_tracks_sum():
    budget = NvrBudget(per_nvr=3, global_cap=10)
    await budget.try_acquire("nvrA")
    await budget.try_acquire("nvrA")
    await budget.try_acquire("nvrB")
    assert budget.global_active() == 3
    await budget.release("nvrA")
    assert budget.global_active() == 2


# ── 10. snapshot() returns a dict copy; mutation does not affect internal state


async def test_snapshot_returns_independent_copy():
    budget = NvrBudget(per_nvr=3, global_cap=10)
    await budget.try_acquire("nvrX")
    snap = budget.snapshot()
    assert snap == {"nvrX": 1}
    # mutate the copy
    snap["nvrX"] = 999
    snap["nvrY"] = 42
    # internal state must be unchanged
    assert budget.active_count("nvrX") == 1
    assert budget.active_count("nvrY") == 0
