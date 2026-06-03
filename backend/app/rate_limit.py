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

# Periodically drop IP entries whose attempts have all aged out, so a flood
# from many (e.g. spoofed) source IPs can't grow this dict without bound.
_SWEEP_INTERVAL_SECONDS = 300.0
_last_sweep = 0.0


def _sweep_locked(now: float, window: float) -> None:
    global _last_sweep
    if now - _last_sweep < _SWEEP_INTERVAL_SECONDS:
        return
    _last_sweep = now
    stale = [ip for ip, ts in _attempts.items() if all(now - t >= window for t in ts)]
    for ip in stale:
        del _attempts[ip]


def check_and_record(ip: str) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds). Records the attempt if allowed."""
    settings = get_settings()
    now = time.time()
    window = settings.login_rate_window_seconds
    with _lock:
        _sweep_locked(now, window)
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
