"""Offline licensing — verification, tamper/expiry/foreign-machine, graceful bad input."""

from __future__ import annotations

import base64
import json
from datetime import date, timedelta

import pytest

from app import licensing

FP = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"  # a fake but well-formed fingerprint


@pytest.fixture()
def keys():
    priv, pub = licensing.generate_keypair()
    return priv, pub, licensing.public_key_pem(pub)


def _make(priv, *, fingerprint=FP, expires="2999-01-01", **over):
    fields = {
        "customer": "ACME LLC",
        "site_id": "acme-01",
        "issued": "2026-07-07",
        "expires": expires,
        "features": ["playback", "hikvision"],
        "max_cameras": 64,
        "max_nvrs": 8,
        "hardware_id": fingerprint,
    }
    fields.update(over)
    return licensing.sign_license(fields, priv)


def test_valid_license_verifies(keys):
    priv, _pub, pem = keys
    doc = _make(priv)
    status = licensing.verify_license(json.dumps(doc), pubkey=pem, fingerprint=FP)
    assert status.valid
    assert status.customer == "ACME LLC"
    assert status.max_cameras == 64
    assert status.max_nvrs == 8
    assert "playback" in status.features
    assert status.days_left is not None and status.days_left > 0


def test_perpetual_license(keys):
    priv, _pub, pem = keys
    doc = _make(priv, expires=None)
    status = licensing.verify_license(json.dumps(doc), pubkey=pem, fingerprint=FP)
    assert status.valid
    assert status.expires is None
    assert status.days_left is None  # perpetual → no countdown


def test_tampered_field_fails(keys):
    priv, _pub, pem = keys
    doc = _make(priv)
    doc["max_cameras"] = 9999  # bump a limit after signing
    status = licensing.verify_license(json.dumps(doc), pubkey=pem, fingerprint=FP)
    assert not status.valid
    assert "signature" in status.reason.lower()


def test_tampered_signature_fails(keys):
    priv, _pub, pem = keys
    doc = _make(priv)
    doc["sig"] = base64.b64encode(b"\x00" * 64).decode()
    status = licensing.verify_license(json.dumps(doc), pubkey=pem, fingerprint=FP)
    assert not status.valid


def test_wrong_fingerprint_fails(keys):
    priv, _pub, pem = keys
    doc = _make(priv)
    status = licensing.verify_license(json.dumps(doc), pubkey=pem, fingerprint="deadbeef" * 4)
    assert not status.valid
    assert "machine" in status.reason.lower()
    # Fields still surfaced (sig was authentic) so the UI can explain the mismatch.
    assert status.customer == "ACME LLC"


def test_expired_license_fails(keys):
    priv, _pub, pem = keys
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    doc = _make(priv, expires=yesterday)
    status = licensing.verify_license(json.dumps(doc), pubkey=pem, fingerprint=FP)
    assert not status.valid
    assert "expired" in status.reason.lower()
    assert status.days_left is not None and status.days_left < 0


def test_expiry_uses_supplied_now(keys):
    priv, _pub, pem = keys
    doc = _make(priv, expires="2026-01-10")
    # As-of before expiry → valid
    ok = licensing.verify_license(
        json.dumps(doc), pubkey=pem, fingerprint=FP, now=date(2026, 1, 1)
    )
    assert ok.valid and ok.days_left == 9
    # As-of after expiry → invalid
    bad = licensing.verify_license(
        json.dumps(doc), pubkey=pem, fingerprint=FP, now=date(2026, 2, 1)
    )
    assert not bad.valid


def test_wrong_public_key_fails(keys):
    priv, _pub, _pem = keys
    doc = _make(priv)
    _priv2, _pub2, pem2 = (lambda p: (p, p.public_key(), licensing.public_key_pem(p.public_key())))(
        licensing.generate_keypair()[0]
    )
    status = licensing.verify_license(json.dumps(doc), pubkey=pem2, fingerprint=FP)
    assert not status.valid


def test_garbage_input_is_graceful(keys):
    _priv, _pub, pem = keys
    for bad in (b"", b"   ", b"not json at all", b"{}", b'{"sig": 123}', b'[1,2,3]'):
        status = licensing.verify_license(bad, pubkey=pem, fingerprint=FP)
        assert not status.valid
        assert isinstance(status.reason, str) and status.reason


def test_missing_public_key_fails_closed(keys):
    priv, _pub, _pem = keys
    doc = _make(priv)
    status = licensing.verify_license(json.dumps(doc), pubkey=None, fingerprint=FP)
    # No embedded key + no env → cannot verify → invalid, not a crash.
    assert not status.valid


def test_machine_fingerprint_stable_and_nonempty():
    a = licensing.machine_fingerprint()
    b = licensing.machine_fingerprint()
    assert a == b
    assert isinstance(a, str) and len(a) == 32
    assert all(c in "0123456789abcdef" for c in a)


def test_load_license_missing_file(tmp_path):
    status = licensing.load_license(tmp_path / "nope.lic")
    assert not status.valid
    assert "no license" in status.reason.lower()


def test_load_and_save_roundtrip(keys, tmp_path, monkeypatch):
    priv, _pub, pem = keys
    # Point verification at our test key + fingerprint.
    monkeypatch.setenv(licensing.ENV_PUBLIC_KEY, pem.decode())
    monkeypatch.setattr(licensing, "machine_fingerprint", lambda: FP)
    licensing.invalidate_cache()

    doc = _make(priv)
    lic_path = tmp_path / "license.lic"
    status = licensing.save_license(json.dumps(doc), path=lic_path)
    assert status.valid
    assert lic_path.exists()

    reloaded = licensing.load_license(lic_path)
    assert reloaded.valid and reloaded.customer == "ACME LLC"


def test_save_rejects_garbage_without_overwriting(tmp_path, monkeypatch):
    monkeypatch.setattr(licensing, "machine_fingerprint", lambda: FP)
    licensing.invalidate_cache()
    lic_path = tmp_path / "license.lic"
    status = licensing.save_license("total garbage", path=lic_path)
    assert not status.valid
    assert not lic_path.exists()  # never wrote a bad blob


def test_status_to_dict_shape(keys):
    priv, _pub, pem = keys
    doc = _make(priv)
    status = licensing.verify_license(json.dumps(doc), pubkey=pem, fingerprint=FP)
    d = status.to_dict()
    assert set(d) >= {"valid", "reason", "customer", "expires", "features", "limits", "days_left"}
    assert d["limits"] == {"max_cameras": 64, "max_nvrs": 8}


def test_issue_tool_roundtrip(tmp_path, monkeypatch):
    """The CLI issuer signs a license our verifier accepts."""
    from tools import issue_license

    keydir = tmp_path / "keys"
    assert issue_license.main(["genkey", "--out-dir", str(keydir)]) == 0
    pub_pem = (keydir / "public_key.pem").read_bytes()

    out = tmp_path / "acme.lic"
    rc = issue_license.main(
        [
            "issue",
            "--private-key", str(keydir / "private_key.pem"),
            "--fingerprint", FP,
            "--customer", "ACME LLC",
            "--site-id", "acme-01",
            "--expires", "2999-01-01",
            "--max-cameras", "32",
            "--max-nvrs", "4",
            "--feature", "playback",
            "--out", str(out),
        ]
    )
    assert rc == 0
    status = licensing.verify_license(out.read_bytes(), pubkey=pub_pem, fingerprint=FP)
    assert status.valid and status.max_cameras == 32 and status.features == ["playback"]
