"""The account-wide NVR auto-disable must not fire on a transient blip.

On a constrained / via-NVR link, packet loss can briefly drop every channel
at once. The old logic read that as "wrong password / host down" and disabled
the whole (working) NVR — the exact thing that made registrars vanish on the
PoE site. An NVR that streamed fine within `recovery_seconds` must be left
alone; only an NVR that was NEVER ready (genuine wrong-password/unreachable)
should be disabled to guard against an IP ban.
"""
import pytest

from app.services import source_watch


class _FakeClient:
    paths: dict = {}

    async def list_active_paths(self):
        return _FakeClient.paths


def _patch(monkeypatch):
    disabled_nvrs: list[str] = []
    monkeypatch.setattr(source_watch, "get_client", lambda: _FakeClient())

    async def _dn(nvr_id, reason):
        disabled_nvrs.append(nvr_id)

    async def _dc(nvr_id, channel, reason):  # pragma: no cover - guard only
        pass

    monkeypatch.setattr(source_watch, "_disable_nvr", _dn)
    monkeypatch.setattr(source_watch, "_disable_camera", _dc)
    return disabled_nvrs


async def test_recently_ready_nvr_survives_total_blip(monkeypatch):
    disabled = _patch(monkeypatch)
    state = ({}, {}, {}, {})  # nvr_fail, cam_fail, ch_last_ready, nvr_last_ready
    nvr = "nvr-192-168-20-15"

    # Round 1: a channel is ready -> records last-ready for the NVR.
    _FakeClient.paths = {f"{nvr}_ch1": {"ready": True, "readers": ["x"], "source": {}}}
    await source_watch._poll_once(*state, 1, 4, 180)
    # Round 2: total blip — viewer still pulling, nothing ready this round.
    _FakeClient.paths = {f"{nvr}_ch1": {"ready": False, "readers": ["x"], "source": {}}}
    await source_watch._poll_once(*state, 1, 4, 180)

    assert disabled == []  # working NVR not nuked by a transient packet-loss spike


async def test_never_ready_nvr_is_still_disabled(monkeypatch):
    disabled = _patch(monkeypatch)
    state = ({}, {}, {}, {})
    nvr = "nvr-bad-password"

    # Never ready, viewer pulling -> genuine failure, must disable (IP-ban guard).
    _FakeClient.paths = {f"{nvr}_ch1": {"ready": False, "readers": ["x"], "source": {}}}
    await source_watch._poll_once(*state, 1, 4, 180)

    assert disabled == [nvr]
