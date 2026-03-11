#!/usr/bin/env python3
"""
DSS Server — Static file server + REST API + MediaMTX process management.

Replaces the old run.sh (python3 -m http.server + manual mediamtx launch).
Provides API endpoints for NVR inventory management and MediaMTX control.
"""

import hashlib
import json
import logging
import logging.handlers
import os
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, parse_qs, urlparse

DIR = Path(__file__).resolve().parent
INVENTORY = DIR / "nvr_inventory.json"
CREDENTIALS = DIR / "credentials.json"
MEDIAMTX_BIN = DIR / ("mediamtx.exe" if sys.platform == "win32" else "mediamtx")
MEDIAMTX_CFG = DIR / "mediamtx.yml"
GENERATE_SCRIPT = DIR / "test" / "generate_config.py"
EVENT_LOG = DIR / "nvr_events.jsonl"
DEBUG_LOG = DIR / "dss_debug.log"
TLS_CERT = DIR / "cert.pem"
TLS_KEY = DIR / "key.pem"
WEB_DIR = DIR / "web"
PORT = 8080

mtx_proc = None
# Active sessions: token -> { username, created }
sessions = {}
use_tls = False

# ── Logging setup ───────────────────────────────────────────────────────────

log = logging.getLogger("dss")
log.setLevel(logging.DEBUG)

# File handler: rotating 5MB x 3 backups → dss_debug.log
_fh = logging.handlers.RotatingFileHandler(
    str(DEBUG_LOG), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
log.addHandler(_fh)

# Console handler: INFO+ only
_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)
_ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S"))
log.addHandler(_ch)

# ── Configuration constants ──────────────────────────────────────────────────

SESSION_TTL = 28800            # 8 hours
LOGIN_RATE_WINDOW = 300        # 5 minutes
LOGIN_RATE_MAX = 10            # max login attempts per window per IP
DEFAULT_BAN_COOLDOWN = 1800    # 30 minutes — Dahua default lockout
EVENT_LOG_MAX_LINES = 10000    # max lines before rotation


# ── Login rate limiting ──────────────────────────────────────────────────────

login_attempts = {}  # ip -> [timestamp, ...]


def check_login_rate(client_ip):
    """Returns (allowed: bool, retry_after: int)."""
    now = time.time()
    attempts = login_attempts.get(client_ip, [])
    # Prune old attempts
    attempts = [t for t in attempts if now - t < LOGIN_RATE_WINDOW]
    login_attempts[client_ip] = attempts
    if len(attempts) >= LOGIN_RATE_MAX:
        retry_after = int(LOGIN_RATE_WINDOW - (now - attempts[0]))
        return False, max(1, retry_after)
    return True, 0


def record_login_attempt(client_ip):
    login_attempts.setdefault(client_ip, []).append(time.time())


# ── Authentication ───────────────────────────────────────────────────────────

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def load_credentials():
    if CREDENTIALS.exists():
        return json.loads(CREDENTIALS.read_text())
    # Create default credentials
    creds = {"username": "admin", "password_hash": hash_password("admin")}
    CREDENTIALS.write_text(json.dumps(creds, indent=2) + "\n")
    print("  Created default credentials (admin:admin)")
    return creds


def verify_login(username, password):
    creds = load_credentials()
    return username == creds["username"] and hash_password(password) == creds["password_hash"]


def create_session(username):
    token = secrets.token_hex(32)
    sessions[token] = {"username": username, "created": time.time()}
    return token


def get_session(cookie_header):
    if not cookie_header:
        return None
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    if "dss_session" not in cookie:
        return None
    token = cookie["dss_session"].value
    session = sessions.get(token)
    if not session:
        return None
    # Session expiry check
    if time.time() - session["created"] > SESSION_TTL:
        sessions.pop(token, None)
        return None
    return session


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>DSS - Login</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { background: #0f0f1a; color: #c8c8d0; font-family: -apple-system, sans-serif;
           display: flex; align-items: center; justify-content: center; min-height: 100vh; }
    .login-box { background: #1a1a2e; padding: 32px; border-radius: 8px; width: 320px;
                 border: 1px solid #2a2a40; }
    .login-box h1 { font-size: 22px; margin-bottom: 6px; color: #e94560; }
    .login-box p { font-size: 12px; color: #888; margin-bottom: 20px; }
    label { display: block; font-size: 13px; margin-bottom: 4px; color: #aaa; }
    input { width: 100%; padding: 8px 10px; background: #0f0f1a; border: 1px solid #2a2a40;
            border-radius: 4px; color: #c8c8d0; font-size: 14px; margin-bottom: 14px; }
    input:focus { outline: none; border-color: #e94560; }
    button { width: 100%; padding: 10px; background: #e94560; color: #fff; border: none;
             border-radius: 4px; font-size: 14px; cursor: pointer; }
    button:hover { background: #d63850; }
    .error { color: #f44336; font-size: 12px; margin-bottom: 10px; display: none; }
  </style>
</head>
<body>
  <div class="login-box">
    <h1>DSS</h1>
    <p>Camera Dashboard</p>
    <div class="error" id="error">Invalid username or password</div>
    <form id="form">
      <label>Username</label>
      <input type="text" name="username" id="username" autocomplete="username" autofocus>
      <label>Password</label>
      <input type="password" name="password" id="password" autocomplete="current-password">
      <button type="submit">Login</button>
    </form>
  </div>
  <script>
    document.getElementById("form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const err = document.getElementById("error");
      err.style.display = "none";
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: document.getElementById("username").value,
          password: document.getElementById("password").value,
        }),
      });
      if (res.ok) {
        location.href = "/";
      } else {
        const data = await res.json().catch(() => ({}));
        err.textContent = data.error || "Invalid username or password";
        err.style.display = "block";
      }
    });
  </script>
</body>
</html>
"""


# ── MediaMTX process management ──────────────────────────────────────────────

def start_mediamtx():
    global mtx_proc
    log.info("Starting MediaMTX: %s %s", MEDIAMTX_BIN, MEDIAMTX_CFG)
    mtx_proc = subprocess.Popen(
        [str(MEDIAMTX_BIN), str(MEDIAMTX_CFG)],
        cwd=str(DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.3)
    if mtx_proc.poll() is not None:
        stderr = mtx_proc.stderr.read().decode(errors="replace")
        mtx_proc = None
        log.error("MediaMTX exited immediately: %s", stderr)
        raise RuntimeError(f"MediaMTX exited immediately: {stderr}")
    log.info("MediaMTX started (PID %d)", mtx_proc.pid)


def stop_mediamtx():
    global mtx_proc
    if mtx_proc is None:
        return
    try:
        mtx_proc.terminate()
        mtx_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        mtx_proc.kill()
        mtx_proc.wait()
    mtx_proc = None


def restart_mediamtx():
    stop_mediamtx()
    time.sleep(0.5)
    start_mediamtx()


# ── NVR Lockout Tracking ─────────────────────────────────────────────────────

nvr_lockouts = {}  # ip -> { "banned_at": float, "cooldown": int }


def get_lockout_info(ip):
    """Returns (is_locked: bool, remaining_seconds: int, banned_until: float)."""
    info = nvr_lockouts.get(ip)
    if not info:
        return False, 0, 0
    elapsed = time.time() - info["banned_at"]
    cooldown = info.get("cooldown", DEFAULT_BAN_COOLDOWN)
    remaining = cooldown - elapsed
    if remaining <= 0:
        nvr_lockouts.pop(ip, None)
        return False, 0, 0
    return True, int(remaining), info["banned_at"] + cooldown


def record_lockout(ip, cooldown=None):
    nvr_lockouts[ip] = {
        "banned_at": time.time(),
        "cooldown": cooldown or DEFAULT_BAN_COOLDOWN,
    }


def clear_lockout(ip):
    nvr_lockouts.pop(ip, None)


# ── NVR Event Log ────────────────────────────────────────────────────────────

def log_nvr_event(nvr_id, ip, event_type, message=""):
    entry = {
        "ts": time.time(),
        "nvr_id": nvr_id,
        "ip": ip,
        "event": event_type,
        "message": message,
    }
    try:
        with open(EVENT_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
        # Simple rotation: if file too large, truncate to last N lines
        if EVENT_LOG.stat().st_size > 2 * 1024 * 1024:  # > 2MB
            lines = EVENT_LOG.read_text().strip().split("\n")
            EVENT_LOG.write_text("\n".join(lines[-EVENT_LOG_MAX_LINES:]) + "\n")
    except OSError:
        pass


def read_events(nvr_id=None, limit=200):
    if not EVENT_LOG.exists():
        return []
    try:
        lines = EVENT_LOG.read_text().strip().split("\n")
        events = []
        for line in reversed(lines):
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if nvr_id and entry.get("nvr_id") != nvr_id:
                continue
            events.append(entry)
            if len(events) >= limit:
                break
        return events
    except OSError:
        return []


# ── NVR Health ─────────────────────────────────────────────────────────────

def _parse_rtsp_status(response):
    """Extract status code from RTSP response first line."""
    if not response:
        return 0, "No response"
    first_line = response.split("\r\n")[0]
    # e.g. "RTSP/1.0 401 Unauthorized"
    parts = first_line.split(None, 2)
    if len(parts) >= 2:
        try:
            return int(parts[1]), first_line
        except ValueError:
            pass
    return 0, first_line


def _parse_digest_challenge(response):
    """Extract digest auth parameters from a 401 response's WWW-Authenticate header."""
    for line in response.split("\r\n"):
        if line.lower().startswith("www-authenticate:"):
            value = line.split(":", 1)[1].strip()
            if value.lower().startswith("digest"):
                params = {}
                # Parse key="value" pairs
                for part in value[6:].split(","):
                    part = part.strip()
                    if "=" in part:
                        k, v = part.split("=", 1)
                        params[k.strip().lower()] = v.strip().strip('"')
                return params
    return None


def _compute_digest_response(username, password, realm, nonce, method, uri):
    """Compute MD5 digest auth response (RFC 2069 / simplified RFC 2617)."""
    import hashlib
    ha1 = hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    resp = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
    return resp


def test_nvr_rtsp(ip, port, username, password, channel=1, timeout=5, nvr_id=None):
    """Test RTSP connectivity using proper digest authentication.
    Returns (ok: bool, message: str, extra: dict).

    Flow:
    1. Send OPTIONS without auth — expect 401 with digest challenge
    2. Compute digest response and resend — expect 200
    3. Only record lockout on 403 (actual IP ban), NOT on 401
    """
    extra = {}

    # Check lockout first
    is_locked, remaining, banned_until = get_lockout_info(ip)
    if is_locked:
        mins = remaining // 60
        secs = remaining % 60
        extra["banned_until"] = banned_until
        extra["remaining"] = remaining
        msg = f"Locked out — retry in {mins}m {secs}s"
        return False, msg, extra

    uri = f"rtsp://{ip}:{port}/cam/realmonitor?channel={channel}&subtype=1"
    method = "OPTIONS"
    tag = f"[{nvr_id or ip}:{port}]"

    sock = None
    try:
        log.debug("%s Connecting TCP...", tag)
        sock = socket.create_connection((ip, port), timeout=timeout)
        log.debug("%s TCP connected, sending OPTIONS (no auth)", tag)

        # Step 1: Send OPTIONS without auth
        req1 = f"{method} {uri} RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: DSS\r\n\r\n"
        sock.sendall(req1.encode())
        resp1 = sock.recv(4096).decode(errors="replace")
        status1, first_line1 = _parse_rtsp_status(resp1)
        log.debug("%s Step1 response: %d — %s", tag, status1, first_line1)

        if status1 == 200:
            # No auth required — OK
            clear_lockout(ip)
            if nvr_id:
                log_nvr_event(nvr_id, ip, "auth_ok", "Credential test passed (no auth)")
            return True, "OK", extra

        if status1 == 403:
            # IP is banned by the NVR
            record_lockout(ip)
            extra["banned_until"] = time.time() + DEFAULT_BAN_COOLDOWN
            extra["remaining"] = DEFAULT_BAN_COOLDOWN
            if nvr_id:
                log_nvr_event(nvr_id, ip, "banned", "IP banned by NVR (403)")
            return False, "Forbidden (IP banned — too many failed attempts)", extra

        if status1 != 401:
            return False, f"Unexpected: {first_line1}", extra

        # Step 2: Parse digest challenge from 401 response
        digest = _parse_digest_challenge(resp1)
        log.debug("%s Digest challenge: %s", tag, digest)
        if not digest or "realm" not in digest or "nonce" not in digest:
            # NVR doesn't support digest — 401 likely means wrong basic-auth credentials
            log.warning("%s 401 but no digest challenge in response", tag)
            if nvr_id:
                log_nvr_event(nvr_id, ip, "auth_fail", "401 without digest challenge")
            return False, "Authentication failed (no digest challenge)", extra

        realm = digest["realm"]
        nonce = digest["nonce"]

        # Compute digest response
        response_hash = _compute_digest_response(username, password, realm, nonce, method, uri)

        auth_header = (
            f'Digest username="{username}", realm="{realm}", nonce="{nonce}", '
            f'uri="{uri}", response="{response_hash}"'
        )

        log.debug("%s Sending OPTIONS with digest auth (realm=%s)", tag, realm)
        # Step 3: Resend OPTIONS with digest auth
        req2 = (
            f"{method} {uri} RTSP/1.0\r\n"
            f"CSeq: 2\r\n"
            f"User-Agent: DSS\r\n"
            f"Authorization: {auth_header}\r\n"
            f"\r\n"
        )
        sock.sendall(req2.encode())
        resp2 = sock.recv(4096).decode(errors="replace")
        status2, first_line2 = _parse_rtsp_status(resp2)
        log.debug("%s Step2 response: %d — %s", tag, status2, first_line2)

        if status2 == 200:
            clear_lockout(ip)
            log.info("%s AUTH OK", tag)
            if nvr_id:
                log_nvr_event(nvr_id, ip, "auth_ok", "Credential test passed")
            return True, "OK", extra
        elif status2 == 401:
            log.warning("%s AUTH FAIL — wrong password (digest rejected)", tag)
            if nvr_id:
                log_nvr_event(nvr_id, ip, "auth_fail", "Wrong password (digest 401)")
            return False, "Authentication failed (wrong password)", extra
        elif status2 == 403:
            record_lockout(ip)
            extra["banned_until"] = time.time() + DEFAULT_BAN_COOLDOWN
            extra["remaining"] = DEFAULT_BAN_COOLDOWN
            log.warning("%s BANNED by NVR (403)", tag)
            if nvr_id:
                log_nvr_event(nvr_id, ip, "banned", "IP banned by NVR (403)")
            return False, "Forbidden (IP banned — too many failed attempts)", extra
        else:
            log.warning("%s Unexpected response: %s", tag, first_line2)
            return False, f"Unexpected: {first_line2}", extra

    except socket.timeout:
        log.warning("%s Connection timeout", tag)
        return False, "Connection timeout (NVR unreachable)", extra
    except ConnectionRefusedError:
        log.warning("%s Connection refused", tag)
        return False, "Connection refused (RTSP port closed)", extra
    except OSError as e:
        log.warning("%s Network error: %s", tag, e)
        return False, f"Network error: {e}", extra
    finally:
        if sock:
            try:
                sock.close()
            except OSError:
                pass


def check_nvr_reachable(ip, port=554, timeout=3):
    """Quick TCP connect check — does NOT send credentials."""
    try:
        sock = socket.create_connection((ip, port), timeout=timeout)
        sock.close()
        return True, "Reachable"
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        return False, str(e)


# ── Validation ────────────────────────────────────────────────────────────────

def validate_inventory(data):
    if not isinstance(data, dict):
        return "Inventory must be a JSON object"
    if "global" not in data:
        return "Missing 'global' key"
    if "nvrs" not in data or not isinstance(data["nvrs"], list):
        return "Missing or invalid 'nvrs' array"
    valid_sources = ("nvr", "server", "")
    for i, nvr in enumerate(data["nvrs"]):
        if not isinstance(nvr, dict):
            return f"NVR #{i} is not an object"
        if not nvr.get("id"):
            return f"NVR #{i} missing 'id'"
        if not nvr.get("ip"):
            return f"NVR #{i} ({nvr.get('id', '?')}) missing 'ip'"
        if not isinstance(nvr.get("channels"), int) or nvr["channels"] < 1:
            return f"NVR #{i} ({nvr['id']}) 'channels' must be a positive integer"
        src = nvr.get("stream_source", "")
        if src and src not in valid_sources:
            return f"NVR #{i} ({nvr['id']}) invalid stream_source: {src}"
    return None


# ── Request handler ───────────────────────────────────────────────────────────

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, format, *args):
        if self.path.startswith("/api/"):
            super().log_message(format, *args)

    # ── Auth check ──

    def _is_authenticated(self):
        return get_session(self.headers.get("Cookie")) is not None

    def _require_auth(self):
        if self._is_authenticated():
            return True
        if self.path.startswith("/api/"):
            self._send(401, {"error": "Unauthorized"})
        else:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
        return False

    # ── Routing ──

    def do_GET(self):
        if self.path == "/login":
            return self._serve_login()
        parsed = urlparse(self.path)
        if parsed.path == "/api/inventory":
            if not self._require_auth():
                return
            return self._get_inventory()
        if parsed.path == "/api/events":
            if not self._require_auth():
                return
            return self._get_events(parsed.query)
        if parsed.path == "/api/lockouts":
            if not self._require_auth():
                return
            return self._get_lockouts()
        if parsed.path == "/api/debug-log":
            if not self._require_auth():
                return
            return self._get_debug_log(parsed.query)
        if not self._require_auth():
            return
        super().do_GET()

    def do_PUT(self):
        if not self._require_auth():
            return
        if self.path == "/api/inventory":
            return self._put_inventory()
        self._send(405, {"error": "Method not allowed"})

    def do_DELETE(self):
        if not self._require_auth():
            return
        if self.path == "/api/lockouts":
            return self._delete_lockouts()
        self._send(405, {"error": "Method not allowed"})

    def do_PATCH(self):
        if not self._require_auth():
            return
        if self.path == "/api/inventory":
            return self._patch_inventory()
        self._send(405, {"error": "Method not allowed"})

    def do_POST(self):
        if self.path == "/api/login":
            return self._post_login()
        if self.path == "/api/logout":
            return self._post_logout()
        if not self._require_auth():
            return
        if self.path == "/api/restart":
            return self._post_restart()
        if self.path == "/api/change-password":
            return self._post_change_password()
        if self.path == "/api/test-nvr":
            return self._post_test_nvr()
        if self.path == "/api/test-all-nvrs":
            return self._post_test_all_nvrs()
        if self.path == "/api/health":
            return self._post_health()
        if self.path == "/api/auto-disable-nvr":
            return self._post_auto_disable_nvr()
        if self.path == "/api/client-log":
            return self._post_client_log()
        self._send(405, {"error": "Method not allowed"})

    # ── Auth handlers ──

    def _serve_login(self):
        if self._is_authenticated():
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return
        body = LOGIN_PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _post_login(self):
        client_ip = self.client_address[0]

        # Rate limiting
        allowed, retry_after = check_login_rate(client_ip)
        if not allowed:
            self._send(429, {
                "error": f"Too many login attempts. Try again in {retry_after}s",
                "retry_after": retry_after,
            })
            return

        try:
            body = self._read_body()
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send(400, {"error": "Invalid JSON"})
            return

        username = data.get("username", "")
        password = data.get("password", "")

        record_login_attempt(client_ip)

        if not verify_login(username, password):
            self._send(401, {"error": "Invalid credentials"})
            return

        token = create_session(username)
        resp = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(resp))
        cookie_flags = "Path=/; HttpOnly; SameSite=Strict"
        if use_tls:
            cookie_flags += "; Secure"
        self.send_header("Set-Cookie", f"dss_session={token}; {cookie_flags}")
        self.end_headers()
        self.wfile.write(resp)

    def _post_logout(self):
        cookie_header = self.headers.get("Cookie")
        if cookie_header:
            cookie = SimpleCookie()
            cookie.load(cookie_header)
            if "dss_session" in cookie:
                token = cookie["dss_session"].value
                sessions.pop(token, None)
        resp = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(resp))
        self.send_header("Set-Cookie", "dss_session=; Path=/; HttpOnly; Max-Age=0")
        self.end_headers()
        self.wfile.write(resp)

    def _post_change_password(self):
        try:
            body = self._read_body()
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send(400, {"error": "Invalid JSON"})
            return

        current = data.get("current_password", "")
        new_pw = data.get("new_password", "")

        if not new_pw or len(new_pw) < 4:
            self._send(400, {"error": "Password must be at least 4 characters"})
            return

        creds = load_credentials()
        if hash_password(current) != creds["password_hash"]:
            self._send(401, {"error": "Current password is incorrect"})
            return

        creds["password_hash"] = hash_password(new_pw)
        CREDENTIALS.write_text(json.dumps(creds, indent=2) + "\n")
        self._send(200, {"ok": True})

    # ── NVR test / health handlers ──

    def _post_test_nvr(self):
        try:
            body = self._read_body()
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send(400, {"error": "Invalid JSON"})
            return

        ip = data.get("ip", "")
        port = data.get("port", 554)
        username = data.get("username", "admin")
        password = data.get("password", "")
        channel = data.get("channel", 1)
        nvr_id = data.get("nvr_id", "")

        if not ip:
            self._send(400, {"error": "IP is required"})
            return

        ok, message, extra = test_nvr_rtsp(ip, port, username, password, channel, nvr_id=nvr_id)
        result = {"ok": ok, "message": message}
        result.update(extra)
        self._send(200, result)

    def _post_test_all_nvrs(self):
        """Test RTSP credentials for all enabled NVRs in the provided inventory."""
        try:
            body = self._read_body()
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send(400, {"error": "Invalid JSON"})
            return

        defaults = data.get("global", {})
        results = []
        for nvr in data.get("nvrs", []):
            if not nvr.get("enabled", True):
                results.append({"id": nvr.get("id", "?"), "ok": None, "message": "Disabled (skipped)"})
                continue

            ip = nvr.get("ip", "")
            port = nvr.get("port", defaults.get("default_port", 554))
            username = nvr.get("username", defaults.get("default_username", "admin"))
            password = nvr.get("password", defaults.get("default_password", ""))
            nvr_id = nvr.get("id", "")

            ok, message, extra = test_nvr_rtsp(ip, port, username, password, nvr_id=nvr_id)
            result = {"id": nvr_id, "ok": ok, "message": message}
            result.update(extra)
            results.append(result)

        failed = [r for r in results if r["ok"] is False]
        self._send(200, {"results": results, "failed_count": len(failed)})

    def _post_health(self):
        try:
            inv = json.loads(INVENTORY.read_text())
        except Exception as e:
            self._send(500, {"error": str(e)})
            return

        defaults = inv.get("global", {})
        results = []
        for nvr in inv.get("nvrs", []):
            if not nvr.get("enabled", True):
                results.append({"id": nvr["id"], "ok": False, "message": "Disabled"})
                continue
            port = nvr.get("port", defaults.get("default_port", 554))
            ok, msg = check_nvr_reachable(nvr["ip"], port)
            results.append({"id": nvr["id"], "ok": ok, "message": msg})
        self._send(200, {"results": results})

    def _post_auto_disable_nvr(self):
        """Auto-disable an NVR due to auth failure. Called by frontend."""
        try:
            body = self._read_body()
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send(400, {"error": "Invalid JSON"})
            return

        nvr_id = data.get("nvr_id", "")
        reason = data.get("reason", "Auth failure detected by client")

        if not nvr_id:
            self._send(400, {"error": "nvr_id is required"})
            return

        try:
            inv = json.loads(INVENTORY.read_text())
        except Exception as e:
            self._send(500, {"error": str(e)})
            return

        found = False
        for nvr in inv.get("nvrs", []):
            if nvr["id"] == nvr_id:
                if not nvr.get("enabled", True):
                    self._send(200, {"ok": True, "message": "Already disabled"})
                    return
                nvr["enabled"] = False
                found = True
                log_nvr_event(nvr_id, nvr.get("ip", ""), "auto_disabled", reason)
                break

        if not found:
            self._send(404, {"error": f"NVR '{nvr_id}' not found"})
            return

        # Backup and save
        if INVENTORY.exists():
            shutil.copy2(INVENTORY, str(INVENTORY) + ".bak")
        INVENTORY.write_text(json.dumps(inv, indent=2) + "\n")

        self._send(200, {"ok": True, "message": f"NVR '{nvr_id}' disabled: {reason}"})

    # ── Event log handlers ──

    def _get_events(self, query_string):
        params = parse_qs(query_string)
        nvr_id = params.get("nvr_id", [None])[0]
        limit = min(int(params.get("limit", [200])[0]), 1000)
        events = read_events(nvr_id=nvr_id, limit=limit)
        self._send(200, {"events": events})

    def _get_lockouts(self):
        now = time.time()
        result = {}
        for ip, info in list(nvr_lockouts.items()):
            remaining = info["cooldown"] - (now - info["banned_at"])
            if remaining > 0:
                result[ip] = {
                    "banned_at": info["banned_at"],
                    "banned_until": info["banned_at"] + info["cooldown"],
                    "remaining": int(remaining),
                }
            else:
                nvr_lockouts.pop(ip, None)
        self._send(200, {"lockouts": result})

    def _delete_lockouts(self):
        count = len(nvr_lockouts)
        nvr_lockouts.clear()
        log.info("Cleared %d lockouts", count)
        self._send(200, {"ok": True, "cleared": count})

    # ── Client log handler ──

    def _post_client_log(self):
        """Receive diagnostic log entries from the browser client."""
        try:
            body = self._read_body()
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send(400, {"error": "Invalid JSON"})
            return

        client_log = logging.getLogger("dss.client")
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            level = entry.get("level", "info").upper()
            msg = entry.get("msg", "")
            path = entry.get("path", "")
            detail = entry.get("detail", "")
            ts = entry.get("ts", "")
            log_msg = f"[{ts}] {path} {msg}"
            if detail:
                log_msg += f" | {detail}"
            lvl = getattr(logging, level, logging.INFO)
            client_log.log(lvl, log_msg)
        self._send(200, {"ok": True})

    # ── Debug log viewer ──

    def _get_debug_log(self, query_string):
        """Return last N lines of dss_debug.log."""
        params = parse_qs(query_string)
        lines = int(params.get("lines", [500])[0])
        lines = min(lines, 5000)
        try:
            if not DEBUG_LOG.exists():
                self._send(200, {"lines": [], "total": 0})
                return
            all_lines = DEBUG_LOG.read_text(encoding="utf-8").strip().split("\n")
            tail = all_lines[-lines:]
            self._send(200, {"lines": tail, "total": len(all_lines)})
        except Exception as e:
            self._send(500, {"error": str(e)})

    # ── API handlers ──

    def _get_inventory(self):
        try:
            data = json.loads(INVENTORY.read_text())
            self._send(200, data)
        except Exception as e:
            self._send(500, {"error": str(e)})

    def _put_inventory(self):
        try:
            body = self._read_body()
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError) as e:
            self._send(400, {"error": f"Invalid JSON: {e}"})
            return

        err = validate_inventory(data)
        if err:
            self._send(400, {"error": err})
            return

        # Backup
        if INVENTORY.exists():
            shutil.copy2(INVENTORY, str(INVENTORY) + ".bak")

        # Write
        INVENTORY.write_text(json.dumps(data, indent=2) + "\n")

        # Regenerate config
        result = subprocess.run(
            [sys.executable, str(GENERATE_SCRIPT)],
            cwd=str(DIR),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            bak = str(INVENTORY) + ".bak"
            if os.path.exists(bak):
                shutil.copy2(bak, INVENTORY)
            self._send(500, {"error": f"generate_config.py failed (rolled back): {result.stderr}"})
            return

        # Restart MediaMTX
        try:
            restart_mediamtx()
        except RuntimeError as e:
            self._send(500, {"error": str(e)})
            return

        self._send(200, {"ok": True, "message": result.stdout.strip()})

    def _patch_inventory(self):
        try:
            body = self._read_body()
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError) as e:
            self._send(400, {"error": f"Invalid JSON: {e}"})
            return

        err = validate_inventory(data)
        if err:
            self._send(400, {"error": err})
            return

        if INVENTORY.exists():
            shutil.copy2(INVENTORY, str(INVENTORY) + ".bak")

        INVENTORY.write_text(json.dumps(data, indent=2) + "\n")
        self._send(200, {"ok": True})

    def _post_restart(self):
        try:
            restart_mediamtx()
            self._send(200, {"ok": True})
        except RuntimeError as e:
            self._send(500, {"error": str(e)})

    # ── Helpers ──

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length).decode()

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


class DSSHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global use_tls

    def shutdown(sig, frame):
        print("\nShutting down...")
        stop_mediamtx()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  DSS Server")

    load_credentials()
    start_mediamtx()

    server = DSSHTTPServer(("", PORT), Handler)

    # HTTPS/TLS support
    if TLS_CERT.exists() and TLS_KEY.exists():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(TLS_CERT), str(TLS_KEY))
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        use_tls = True
        proto = "https"
    else:
        proto = "http"

    print(f"  Web UI:    {proto}://localhost:{PORT}")
    print(f"  Login:     {proto}://localhost:{PORT}/login")
    if not use_tls:
        print("  TLS:       off (add cert.pem + key.pem for HTTPS)")
    else:
        print("  TLS:       on")
    print("  MediaMTX:  http://localhost:9997")
    print(f"  Sessions:  expire after {SESSION_TTL // 3600}h")
    print("  Press Ctrl+C to stop")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    server.serve_forever()


if __name__ == "__main__":
    main()
