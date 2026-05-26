"""
Authentication: cookie sessions, login rate limiting, credentials persistence,
and the bundled login HTML page.

State is module-level (in-memory): `sessions`, `login_attempts`.
"""

import hashlib
import json
import secrets
import time
from http.cookies import SimpleCookie

from . import config


# Active sessions: token -> { username, created }
sessions = {}

# Login rate limiting: client_ip -> [timestamp, ...]
login_attempts = {}


# ── Rate limiting ────────────────────────────────────────────────────────────

def check_login_rate(client_ip):
    """Returns (allowed: bool, retry_after_seconds: int)."""
    now = time.time()
    attempts = login_attempts.get(client_ip, [])
    # Prune old attempts
    attempts = [t for t in attempts if now - t < config.LOGIN_RATE_WINDOW]
    login_attempts[client_ip] = attempts
    if len(attempts) >= config.LOGIN_RATE_MAX:
        retry_after = int(config.LOGIN_RATE_WINDOW - (now - attempts[0]))
        return False, max(1, retry_after)
    return True, 0


def record_login_attempt(client_ip):
    login_attempts.setdefault(client_ip, []).append(time.time())


# ── Credentials ──────────────────────────────────────────────────────────────

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def load_credentials():
    if config.CREDENTIALS.exists():
        return json.loads(config.CREDENTIALS.read_text())
    creds = {"username": "admin", "password_hash": hash_password("admin")}
    config.CREDENTIALS.write_text(json.dumps(creds, indent=2) + "\n")
    print("  Created default credentials (admin:admin)")
    return creds


def save_credentials(creds):
    config.CREDENTIALS.write_text(json.dumps(creds, indent=2) + "\n")


def verify_login(username, password):
    creds = load_credentials()
    return username == creds["username"] and hash_password(password) == creds["password_hash"]


# ── Sessions ─────────────────────────────────────────────────────────────────

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
    if time.time() - session["created"] > config.SESSION_TTL:
        sessions.pop(token, None)
        return None
    return session


def drop_session(cookie_header):
    """Remove the session referenced by the cookie header (logout)."""
    if not cookie_header:
        return
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    if "dss_session" in cookie:
        sessions.pop(cookie["dss_session"].value, None)


# ── Login page ───────────────────────────────────────────────────────────────

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
