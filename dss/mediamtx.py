"""
MediaMTX subprocess management: start, stop, restart.

The process handle is module-private. Use `start()`/`stop()`/`restart()`.
"""

import subprocess
import time

from .config import MEDIAMTX_BIN, MEDIAMTX_CFG, DIR, log


_proc = None


def start():
    """Spawn MediaMTX. Raises RuntimeError if it exits immediately."""
    global _proc
    log.info("Starting MediaMTX: %s %s", MEDIAMTX_BIN, MEDIAMTX_CFG)
    _proc = subprocess.Popen(
        [str(MEDIAMTX_BIN), str(MEDIAMTX_CFG)],
        cwd=str(DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.3)
    if _proc.poll() is not None:
        stderr = _proc.stderr.read().decode(errors="replace")
        _proc = None
        log.error("MediaMTX exited immediately: %s", stderr)
        raise RuntimeError(f"MediaMTX exited immediately: {stderr}")
    log.info("MediaMTX started (PID %d)", _proc.pid)


def stop():
    """Terminate MediaMTX gracefully, kill if it doesn't exit within 5s."""
    global _proc
    if _proc is None:
        return
    try:
        _proc.terminate()
        _proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _proc.kill()
        _proc.wait()
    _proc = None


def restart():
    stop()
    time.sleep(0.5)
    start()
