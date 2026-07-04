"""The account-wide NVR auto-disable must not fire on a transient blip.

On a constrained / via-NVR link, packet loss can briefly drop every channel
at once. The old logic read that as "wrong password / host down" and disabled
the whole (working) NVR — the exact thing that made registrars vanish on the
PoE site. An NVR that streamed fine within `recovery_seconds` must be left
alone; only an NVR that was NEVER ready (genuine wrong-password/unreachable)
should be disabled to guard against an IP ban.
"""
import httpx
import pytest

from app.services import go2rtc_api, source_watch


class _FakeClient:
    """Returns a pre-normalised {name: {ready, readers, source}} map — the shape
    go2rtc_api.Go2rtcClient.list_stream_states yields after parsing /api/streams."""

    paths: dict = {}

    async def list_stream_states(self):
        return _FakeClient.paths


def _patch(monkeypatch):
    disabled_nvrs: list[str] = []
    monkeypatch.setattr(go2rtc_api, "get_client", lambda: _FakeClient())

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


async def test_dial_grace_spares_cold_redial_then_still_catches_real_failure(monkeypatch):
    """First-dial grace: a healthy NVR reopened after a long idle shows
    consumer-attached + no-producer while go2rtc dials — polls inside the
    grace window must NOT count toward the disable threshold. Once the NVR
    keeps failing PAST the grace, the IP-ban guard still fires."""
    disabled = _patch(monkeypatch)
    state = ({}, {}, {}, {})
    first_fail: dict[str, float] = {}
    nvr = "nvr-slow-dial"
    _FakeClient.paths = {f"{nvr}_ch1": {"ready": False, "readers": ["x"], "source": {}}}

    # Polls within the grace window: not counted, nothing disabled.
    await source_watch._poll_once(
        *state, 1, 4, 180, nvr_first_fail=first_fail, dial_grace_seconds=3600
    )
    await source_watch._poll_once(
        *state, 1, 4, 180, nvr_first_fail=first_fail, dial_grace_seconds=3600
    )
    assert disabled == []
    assert nvr in first_fail  # episode is being tracked

    # Same episode, grace elapsed (simulate by backdating the first-fail mark):
    first_fail[nvr] -= 7200
    await source_watch._poll_once(
        *state, 1, 4, 180, nvr_first_fail=first_fail, dial_grace_seconds=3600
    )
    assert disabled == [nvr]  # genuine persistent failure still caught


async def test_dial_grace_mark_cleared_on_recovery(monkeypatch):
    """A recovered NVR clears its first-fail mark, so the NEXT failing episode
    gets a fresh grace window instead of inheriting a stale timestamp."""
    _patch(monkeypatch)
    state = ({}, {}, {}, {})
    first_fail: dict[str, float] = {}
    nvr = "nvr-192-168-20-39"

    _FakeClient.paths = {f"{nvr}_ch1": {"ready": False, "readers": ["x"], "source": {}}}
    await source_watch._poll_once(
        *state, 1, 4, 180, nvr_first_fail=first_fail, dial_grace_seconds=3600
    )
    assert nvr in first_fail

    # Dial completes → ready. The mark must be dropped.
    _FakeClient.paths = {f"{nvr}_ch1": {"ready": True, "readers": ["x"], "source": {}}}
    await source_watch._poll_once(
        *state, 1, 4, 180, nvr_first_fail=first_fail, dial_grace_seconds=3600
    )
    assert nvr not in first_fail


# ── go2rtc relay mode ────────────────────────────────────────────────────────
# Production runs relay == "go2rtc" with MediaMTX absent; the watchdog must
# police go2rtc's /api/streams instead, using the same disable logic.


class _FakeGo2rtcClient:
    """Returns a pre-normalised {name: {ready, readers, source}} map, mimicking
    Go2rtcClient.list_stream_states after it has parsed /api/streams."""

    states: dict = {}
    raise_exc: Exception | None = None

    async def list_stream_states(self):
        if _FakeGo2rtcClient.raise_exc is not None:
            raise _FakeGo2rtcClient.raise_exc
        return _FakeGo2rtcClient.states


def _patch_go2rtc(monkeypatch):
    disabled_nvrs: list[str] = []
    _FakeGo2rtcClient.raise_exc = None
    _FakeGo2rtcClient.states = {}
    monkeypatch.setattr(go2rtc_api, "get_client", lambda: _FakeGo2rtcClient())

    async def _dn(nvr_id, reason):
        disabled_nvrs.append(nvr_id)

    async def _dc(nvr_id, channel, reason):  # pragma: no cover - guard only
        pass

    monkeypatch.setattr(source_watch, "_disable_nvr", _dn)
    monkeypatch.setattr(source_watch, "_disable_camera", _dc)
    return disabled_nvrs


async def test_go2rtc_healthy_stream_not_disabled(monkeypatch):
    disabled = _patch_go2rtc(monkeypatch)
    state = ({}, {}, {}, {})
    nvr = "nvr-192-168-20-15"

    # Consumer pulling + producer up → ready → nothing to police.
    _FakeGo2rtcClient.states = {f"{nvr}_ch1": {"ready": True, "readers": ["v"], "source": None}}
    await source_watch._poll_once(*state, 1, 4, 180)

    assert disabled == []


async def test_go2rtc_failing_with_consumers_is_disabled(monkeypatch):
    disabled = _patch_go2rtc(monkeypatch)
    state = ({}, {}, {}, {})
    nvr = "nvr-bad-password"

    # Consumer pulling but no producer established (wrong-password signature).
    _FakeGo2rtcClient.states = {f"{nvr}_ch1": {"ready": False, "readers": ["v"], "source": None}}
    await source_watch._poll_once(*state, 1, 4, 180)

    assert disabled == [nvr]


async def test_go2rtc_idle_no_consumers_untouched(monkeypatch):
    disabled = _patch_go2rtc(monkeypatch)
    state = ({}, {}, {}, {})

    # list_stream_states already drops consumer-less idle streams, so the
    # watchdog simply sees nothing — a healthy idle on-demand NVR is never touched.
    _FakeGo2rtcClient.states = {}
    await source_watch._poll_once(*state, 1, 4, 180)

    assert disabled == []


async def test_go2rtc_api_down_is_noop(monkeypatch):
    disabled = _patch_go2rtc(monkeypatch)
    state = ({}, {}, {}, {})

    # go2rtc unreachable (restarting) → quiet-degrade, never flap an NVR.
    _FakeGo2rtcClient.raise_exc = httpx.ConnectError("boom")
    await source_watch._poll_once(*state, 1, 4, 180)

    assert disabled == []


# ── go2rtc /api/streams normalisation (list_stream_states) ────────────────────


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeHttp:
    def __init__(self, payload):
        self._payload = payload

    async def get(self, url):
        return _FakeResp(self._payload)


def _client_with(payload):
    c = go2rtc_api.Go2rtcClient("http://x")
    c._client = _FakeHttp(payload)
    return c


async def test_list_stream_states_healthy_and_failing():
    c = _client_with({
        "cam-healthy": {"producers": [{"url": "rtsp://x"}], "consumers": [{"id": 1}]},
        "cam-failing": {"producers": [], "consumers": [{"id": 1}]},
    })
    out = await c.list_stream_states()
    assert out["cam-healthy"]["ready"] is True
    assert out["cam-healthy"]["readers"] == [{"id": 1}]
    assert out["cam-failing"]["ready"] is False


async def test_list_stream_states_idle_stream_omitted():
    # No consumers → idle on-demand → dropped so the watchdog never evaluates it.
    c = _client_with({"cam-idle": {"producers": [], "consumers": []}})
    out = await c.list_stream_states()
    assert out == {}


async def test_list_stream_states_unknown_shape_no_crash():
    # Garbage / unexpected shapes must yield no data, never raise.
    c = _client_with({"weird": "not-a-dict", "also": 123})
    out = await c.list_stream_states()
    assert out == {}
    c2 = _client_with(["totally", "wrong"])
    assert await c2.list_stream_states() == {}
