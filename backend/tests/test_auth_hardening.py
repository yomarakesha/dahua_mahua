"""Login timing-oracle mitigation (dummy Argon2 verify) + rate-limit reset."""

from __future__ import annotations

from argon2 import PasswordHasher

from app import rate_limit
from app import security


def test_dummy_hash_is_a_real_argon2_hash():
    # Must be verifiable Argon2 so the unknown-user path does the same work as a
    # real verify (uniform timing).
    assert security._DUMMY_PASSWORD_HASH.startswith("$argon2")
    ph = PasswordHasher()
    # A wrong password against the dummy hash raises (i.e. it's a genuine verify),
    # confirming dummy_verify exercises the full Argon2 path.
    import pytest
    from argon2.exceptions import VerifyMismatchError
    with pytest.raises(VerifyMismatchError):
        ph.verify(security._DUMMY_PASSWORD_HASH, "definitely-not-the-secret")


def test_dummy_verify_never_raises_and_returns_none():
    assert security.dummy_verify("whatever") is None
    assert security.dummy_verify("") is None


def test_rate_limit_reset_clears_the_key(monkeypatch):
    ip = "203.0.113.7"
    # Force a tiny budget so we can drive it to the limit deterministically.
    from types import SimpleNamespace
    monkeypatch.setattr(
        rate_limit, "get_settings",
        lambda: SimpleNamespace(login_rate_max=2, login_rate_window_seconds=300),
    )
    assert rate_limit.check_and_record(ip)[0] is True
    assert rate_limit.check_and_record(ip)[0] is True
    allowed, retry = rate_limit.check_and_record(ip)
    assert allowed is False and retry > 0

    rate_limit.reset(ip)  # a successful login clears the budget
    assert rate_limit.check_and_record(ip)[0] is True
