"""Optional subprocess wrapper for MediaMTX.

Used when `settings.mediamtx_managed=True` — typically during local dev or
bare-metal deploys where one process supervisor (us) brings everything up.
In docker-compose deployments MediaMTX runs in its own container, so this
module is never imported.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path

from app.settings import get_settings

log = logging.getLogger("dss.mediamtx_proc")


_proc: subprocess.Popen | None = None


def _pump_output(stream, level: int) -> None:
    try:
        for raw in iter(stream.readline, b""):
            if not raw:
                break
            line = raw.decode(errors="replace").rstrip()
            if line:
                log.log(level, "%s", line)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def start() -> None:
    global _proc
    if _proc is not None and _proc.poll() is None:
        return

    settings = get_settings()
    bin_path = settings.mediamtx_bin
    cfg_path = Path(settings.mediamtx_config_path)
    if not cfg_path.is_absolute():
        cfg_path = settings.project_root / cfg_path

    log.info("Starting MediaMTX: %s %s", bin_path, cfg_path)
    _proc = subprocess.Popen(
        [str(bin_path), str(cfg_path)],
        cwd=str(settings.project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )
    time.sleep(0.3)
    if _proc.poll() is not None:
        stderr = _proc.stderr.read().decode(errors="replace") if _proc.stderr else ""
        rc = _proc.returncode
        _proc = None
        raise RuntimeError(f"MediaMTX exited immediately rc={rc}: {stderr}")

    threading.Thread(
        target=_pump_output, args=(_proc.stdout, logging.INFO),
        name="mediamtx-stdout", daemon=True,
    ).start()
    threading.Thread(
        target=_pump_output, args=(_proc.stderr, logging.WARNING),
        name="mediamtx-stderr", daemon=True,
    ).start()

    log.info("MediaMTX started (PID %d)", _proc.pid)


def stop() -> None:
    global _proc
    if _proc is None:
        return
    pid = _proc.pid
    log.info("Stopping MediaMTX (PID %d)", pid)
    try:
        _proc.terminate()
        _proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        log.warning("MediaMTX (PID %d) did not exit in 5s — killing", pid)
        _proc.kill()
        _proc.wait()
    _proc = None


def is_running() -> bool:
    return _proc is not None and _proc.poll() is None
