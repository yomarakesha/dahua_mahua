"""NVR create/update IP + port validation (raw IPs only, port 1..65535)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import NvrCreate, NvrUpdate


def _create(**over):
    base = dict(label="NVR", ip="192.168.20.34", rtsp_password="pw")
    base.update(over)
    return NvrCreate(**base)


def test_create_accepts_valid_ipv4():
    assert _create(ip="10.10.1.152").ip == "10.10.1.152"


def test_create_accepts_valid_ipv6():
    assert _create(ip="fe80::1").ip == "fe80::1"


def test_create_rejects_hostname():
    with pytest.raises(ValidationError):
        _create(ip="nvr.local")


def test_create_rejects_garbage_ip():
    with pytest.raises(ValidationError):
        _create(ip="999.1.1.1")


def test_create_rejects_empty_ip():
    with pytest.raises(ValidationError):
        _create(ip="")


@pytest.mark.parametrize("port", [0, -1, 65536, 100000])
def test_create_rejects_out_of_range_port(port):
    with pytest.raises(ValidationError):
        _create(port=port)


def test_create_accepts_edge_ports():
    assert _create(port=1).port == 1
    assert _create(port=65535).port == 65535


def test_update_ip_optional_but_validated():
    # None stays None (partial update — field untouched).
    assert NvrUpdate().ip is None
    assert NvrUpdate(ip=None).ip is None
    assert NvrUpdate(ip="192.168.1.5").ip == "192.168.1.5"
    with pytest.raises(ValidationError):
        NvrUpdate(ip="not-an-ip")


def test_update_port_range_enforced():
    assert NvrUpdate(port=554).port == 554
    with pytest.raises(ValidationError):
        NvrUpdate(port=70000)
