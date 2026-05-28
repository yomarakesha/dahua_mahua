"""
File-system paths, runtime constants, and shared logger setup.

Import-side effect: configures the `dss` logger with a rotating file handler
(dss_debug.log, 5MB × 3) plus an INFO+ console handler. Other modules just
`from .config import log` and use it.
"""

import logging
import logging.handlers
import sys
from pathlib import Path

# Project root is the parent of this `dss/` package.
DIR = Path(__file__).resolve().parent.parent

INVENTORY       = DIR / "nvr_inventory.json"
CREDENTIALS     = DIR / "credentials.json"
MEDIAMTX_BIN    = DIR / ("mediamtx.exe" if sys.platform == "win32" else "mediamtx")
MEDIAMTX_CFG    = DIR / "mediamtx.yml"
GENERATE_SCRIPT = DIR / "scripts" / "generate_config.py"
EVENT_LOG       = DIR / "nvr_events.jsonl"
DEBUG_LOG       = DIR / "dss_debug.log"
LOCKOUTS_FILE   = DIR / "nvr_lockouts.json"
TLS_CERT        = DIR / "cert.pem"
TLS_KEY         = DIR / "key.pem"
WEB_DIR         = DIR / "web"

PORT = 8080

SESSION_TTL          = 28800   # 8 hours
LOGIN_RATE_WINDOW    = 300     # 5 minutes
LOGIN_RATE_MAX       = 10      # max login attempts per window per IP
DEFAULT_BAN_COOLDOWN = 1800    # 30 minutes — Dahua default IP lockout
EVENT_LOG_MAX_LINES  = 10000   # max lines before rotation

# Mutable: flipped to True by server.py once TLS context is attached.
use_tls = False


# ── Logger setup ─────────────────────────────────────────────────────────────

log = logging.getLogger("dss")
log.setLevel(logging.DEBUG)

_fh = logging.handlers.RotatingFileHandler(
    str(DEBUG_LOG), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
log.addHandler(_fh)

_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)
_ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S"))
log.addHandler(_ch)
