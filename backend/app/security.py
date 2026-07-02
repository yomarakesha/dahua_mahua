"""Password hashing (Argon2id) + JWT issue/verify.

Why Argon2id: memory-hard, GPU-resistant, the OWASP-recommended default for
new applications. The legacy SHA-256 hashes from dss/auth.py are NOT compatible
— users must reset their passwords after migration (bootstrap admin handles
the first login).
"""

from __future__ import annotations

import secrets
import time
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError

from app.settings import get_settings

_hasher = PasswordHasher()

# A throwaway Argon2 hash of a random secret, computed once at import. The login
# path verifies an unknown/inactive user's password against THIS so an Argon2
# verify always runs — otherwise skipping the hash for a missing username is a
# fast-fail timing oracle that lets an attacker enumerate valid usernames.
_DUMMY_PASSWORD_HASH = _hasher.hash(secrets.token_urlsafe(32))


def hash_password(plaintext: str) -> str:
    return _hasher.hash(plaintext)


def verify_password(plaintext: str, hashed: str) -> bool:
    try:
        _hasher.verify(hashed, plaintext)
        return True
    except (VerifyMismatchError, InvalidHash):
        return False


def dummy_verify(plaintext: str) -> None:
    """Run a throwaway Argon2 verify to equalize timing on the unknown-user login
    path (constant work whether or not the username exists). Result is discarded."""
    verify_password(plaintext, _DUMMY_PASSWORD_HASH)


def needs_rehash(hashed: str) -> bool:
    """True if the hash uses outdated parameters (e.g. lower memory cost)."""
    try:
        return _hasher.check_needs_rehash(hashed)
    except InvalidHash:
        return True


# ── JWT ─────────────────────────────────────────────────────────────────────


def issue_access_token(*, subject: str, role: str, extra: dict[str, Any] | None = None) -> str:
    settings = get_settings()
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "iat": now,
        "exp": now + settings.jwt_access_ttl_seconds,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """Decode + verify a JWT. Raises jwt.PyJWTError subclasses on failure."""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
