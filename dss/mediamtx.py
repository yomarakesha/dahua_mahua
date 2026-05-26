"""
MediaMTX subprocess management: start, stop, restart.

The process handle is module-private. Use `start()`/`stop()`/`restart()`.
"""

import logging
import subprocess
import threading
import time

from .config import MEDIAMTX_BIN, MEDIAMTX_CFG, DIR, log


mtx_log = logging.getLogger("dss.mediamtx")

_proc = None


def _pump_output(stream, level):
    """Drain a subprocess stream line-by-line into the logger.

    Without this the PIPE buffers fill up and MediaMTX eventually blocks on
    its own writes. Logging the output also surfaces every RTSP client
    connection, publish/read session, and auth failure that MediaMTX emits.
    """
    try:
        for raw in iter(stream.readline, b""):
            if not raw:
                break
            line = raw.decode(errors="replace").rstrip()
            if line:
                mtx_log.log(level, "%s", line)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def start():
    """Spawn MediaMTX. Raises RuntimeError if it exits immediately."""
    global _proc
    log.info("Starting MediaMTX: %s %s", MEDIAMTX_BIN, MEDIAMTX_CFG)
    _proc = subprocess.Popen(
        [str(MEDIAMTX_BIN), str(MEDIAMTX_CFG)],
        cwd=str(DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )
    time.sleep(0.3)
    if _proc.poll() is not None:
        stderr = _proc.stderr.read().decode(errors="replace") if _proc.stderr else ""
        rc = _proc.returncode
        _proc = None
        log.error("MediaMTX exited immediately rc=%s: %s", rc, stderr)
        raise RuntimeError(f"MediaMTX exited immediately: {stderr}")

    threading.Thread(
        target=_pump_output, args=(_proc.stdout, logging.INFO),
        name="mediamtx-stdout", daemon=True,
    ).start()
    threading.Thread(
        target=_pump_output, args=(_proc.stderr, logging.WARNING),
        name="mediamtx-stderr", daemon=True,
    ).start()

    log.info("MediaMTX started (PID %d)", _proc.pid)


def stop():
    """Terminate MediaMTX gracefully, kill if it doesn't exit within 5s."""
    global _proc
    if _proc is None:
        return
    pid = _proc.pid
    log.info("Stopping MediaMTX (PID %d)", pid)
    try:
        _proc.terminate()
        _proc.wait(timeout=5)
        log.info("MediaMTX (PID %d) exited rc=%s", pid, _proc.returncode)
    except subprocess.TimeoutExpired:
        log.warning("MediaMTX (PID %d) did not exit in 5s — killing", pid)
        _proc.kill()
        _proc.wait()
        log.info("MediaMTX (PID %d) killed", pid)
    _proc = None


def restart():
    log.info("Restarting MediaMTX")
    stop()
    time.sleep(0.5)
    start()
