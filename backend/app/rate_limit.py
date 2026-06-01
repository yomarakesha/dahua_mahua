"""In-memory per-IP login rate limiter.

Stays in-process — for a multi-worker / multi-instance deployment, swap the
backing store for Redis. For our single-backend setup this is fine and
matches the behaviour of the legacy dss/auth.py implementation.
"""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock

from app.settings import get_settings

_attempts: dict[str, list[float]] = defaultdict(list)
_lock = Lock()


def check_and_record(ip: str) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds). Records the attempt if allowed."""
    settings = get_settings()
    now = time.time()
    window = settings.login_rate_window_seconds
    with _lock:
        attempts = [t for t in _attempts[ip] if now - t < window]
        if len(attempts) >= settings.login_rate_max:
            retry_after = int(window - (now - attempts[0]))
            _attempts[ip] = attempts
            return False, max(1, retry_after)
        attempts.append(now)
        _attempts[ip] = attempts
        return True, 0


def reset(ip: str) -> None:
    with _lock:
        _attempts.pop(ip, None)
