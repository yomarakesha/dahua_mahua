#!/usr/bin/env python3
"""
DSS Server — Static file server + REST API + MediaMTX process management.

Replaces the old run.sh (python3 -m http.server + manual mediamtx launch).
Provides API endpoints for NVR inventory management and MediaMTX control.
"""

import hashlib
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DIR = Path(__file__).resolve().parent
INVENTORY = DIR / "nvr_inventory.json"
CREDENTIALS = DIR / "credentials.json"
MEDIAMTX_BIN = DIR / ("mediamtx.exe" if sys.platform == "win32" else "mediamtx")
MEDIAMTX_CFG = DIR / "mediamtx.yml"
GENERATE_SCRIPT = DIR / "generate_config.py"
WEB_DIR = DIR / "web"
PORT = 8080

mtx_proc = None
# Active sessions: token -> { username, created }
sessions = {}


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
    return sessions.get(token)


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
        document.getElementById("error").style.display = "block";
      }
    });
  </script>
</body>
</html>
"""


# ── MediaMTX process management ──────────────────────────────────────────────

def start_mediamtx():
    global mtx_proc
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
        raise RuntimeError(f"MediaMTX exited immediately: {stderr}")
    print(f"  MediaMTX started (PID {mtx_proc.pid})")


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


# ── Validation ────────────────────────────────────────────────────────────────

def validate_inventory(data):
    if not isinstance(data, dict):
        return "Inventory must be a JSON object"
    if "global" not in data:
        return "Missing 'global' key"
    if "nvrs" not in data or not isinstance(data["nvrs"], list):
        return "Missing or invalid 'nvrs' array"
    for i, nvr in enumerate(data["nvrs"]):
        if not isinstance(nvr, dict):
            return f"NVR #{i} is not an object"
        if not nvr.get("id"):
            return f"NVR #{i} missing 'id'"
        if not nvr.get("ip"):
            return f"NVR #{i} ({nvr.get('id', '?')}) missing 'ip'"
        if not isinstance(nvr.get("channels"), int) or nvr["channels"] < 1:
            return f"NVR #{i} ({nvr['id']}) 'channels' must be a positive integer"
    return None


# ── Request handler ───────────────────────────────────────────────────────────

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, format, *args):
        # Quieter logging — only log API calls
        if self.path.startswith("/api/"):
            super().log_message(format, *args)

    # ── Auth check ──

    def _is_authenticated(self):
        return get_session(self.headers.get("Cookie")) is not None

    def _require_auth(self):
        """Returns True if authenticated, False if redirect/401 was sent."""
        if self._is_authenticated():
            return True
        # API calls get 401, page requests get redirect
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
        if self.path == "/api/inventory":
            if not self._require_auth():
                return
            return self._get_inventory()
        # All other pages require auth
        if not self._require_auth():
            return
        super().do_GET()

    def do_PUT(self):
        if not self._require_auth():
            return
        if self.path == "/api/inventory":
            return self._put_inventory()
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
        self._send(405, {"error": "Method not allowed"})

    # ── Auth handlers ──

    def _serve_login(self):
        # If already logged in, redirect to dashboard
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
        try:
            body = self._read_body()
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send(400, {"error": "Invalid JSON"})
            return

        username = data.get("username", "")
        password = data.get("password", "")

        if not verify_login(username, password):
            self._send(401, {"error": "Invalid credentials"})
            return

        token = create_session(username)
        resp = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(resp))
        self.send_header("Set-Cookie", f"dss_session={token}; Path=/; HttpOnly; SameSite=Strict")
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
            # Rollback inventory on config generation failure
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
        """Update inventory JSON only (no config regen or MediaMTX restart)."""
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
    def shutdown(sig, frame):
        print("\nShutting down...")
        stop_mediamtx()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  DSS Server")

    load_credentials()  # Ensure credentials file exists
    start_mediamtx()

    server = DSSHTTPServer(("", PORT), Handler)
    print(f"  Web UI:    http://localhost:{PORT}")
    print(f"  Login:     http://localhost:{PORT}/login")
    print("  MediaMTX:  http://localhost:9997")
    print("  Press Ctrl+C to stop")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    server.serve_forever()


if __name__ == "__main__":
    main()
