"""
HTTP request handler — REST API + static file serving.

The Handler subclasses SimpleHTTPRequestHandler so we get static-file serving
for `/web/` for free. All `/api/*` routes are dispatched by method-specific
`do_*` callbacks below.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import auth, config, mediamtx, nvr


http_log = logging.getLogger("dss.http")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self._req_start = None
        super().__init__(*args, directory=str(config.WEB_DIR), **kwargs)

    # ── Connection lifecycle ────────────────────────────────────────────────

    def setup(self):
        super().setup()
        http_log.debug("TCP connect from %s:%d", *self.client_address)

    def finish(self):
        try:
            super().finish()
        finally:
            try:
                http_log.debug("TCP disconnect %s:%d", *self.client_address)
            except (AttributeError, TypeError):
                pass

    def handle_one_request(self):
        self._req_start = time.monotonic()
        super().handle_one_request()

    def log_request(self, code='-', size='-'):
        dur_ms = 0.0
        if self._req_start is not None:
            dur_ms = (time.monotonic() - self._req_start) * 1000
        method = getattr(self, "command", "?")
        path = getattr(self, "path", "?")
        ua = "-"
        ref = "-"
        if self.headers:
            ua = self.headers.get("User-Agent", "-")
            ref = self.headers.get("Referer", "-")
        ip = self.client_address[0] if self.client_address else "?"
        # Static-file traffic at DEBUG; /api/* and auth pages at INFO.
        is_api = isinstance(path, str) and (path.startswith("/api/") or path == "/login")
        level = logging.INFO if is_api else logging.DEBUG
        http_log.log(
            level,
            "%s %s %s -> %s %s %.1fms ua=%r ref=%r",
            ip, method, path, code, size, dur_ms, ua, ref,
        )

    def log_error(self, fmt, *args):
        http_log.warning(
            "HTTP error from %s: " + fmt,
            self.client_address[0] if self.client_address else "?",
            *args,
        )

    def log_message(self, fmt, *args):
        # Suppress the stdlib default — log_request and log_error now handle output.
        pass

    # ── Auth guard ──────────────────────────────────────────────────────────

    def _is_authenticated(self):
        return auth.get_session(self.headers.get("Cookie")) is not None

    def _require_auth(self):
        if self._is_authenticated():
            return True
        ip = self.client_address[0] if self.client_address else "?"
        http_log.warning(
            "Unauthorized access ip=%s method=%s path=%s ua=%r",
            ip, getattr(self, "command", "?"), self.path,
            self.headers.get("User-Agent", "-") if self.headers else "-",
        )
        if self.path.startswith("/api/"):
            self._send(401, {"error": "Unauthorized"})
        else:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
        return False

    # ── Method dispatch ─────────────────────────────────────────────────────

    def do_GET(self):
        if self.path == "/login":
            return self._serve_login()
        parsed = urlparse(self.path)
        if parsed.path == "/api/inventory":
            if not self._require_auth(): return
            return self._get_inventory()
        if parsed.path == "/api/events":
            if not self._require_auth(): return
            return self._get_events(parsed.query)
        if parsed.path == "/api/lockouts":
            if not self._require_auth(): return
            return self._get_lockouts()
        if parsed.path == "/api/debug-log":
            if not self._require_auth(): return
            return self._get_debug_log(parsed.query)
        if not self._require_auth(): return
        super().do_GET()

    def do_PUT(self):
        if not self._require_auth(): return
        if self.path == "/api/inventory":
            return self._put_inventory()
        self._send(405, {"error": "Method not allowed"})

    def do_DELETE(self):
        if not self._require_auth(): return
        if self.path == "/api/lockouts":
            return self._delete_lockouts()
        self._send(405, {"error": "Method not allowed"})

    def do_PATCH(self):
        if not self._require_auth(): return
        if self.path == "/api/inventory":
            return self._patch_inventory()
        self._send(405, {"error": "Method not allowed"})

    def do_POST(self):
        if self.path == "/api/login":
            return self._post_login()
        if self.path == "/api/logout":
            return self._post_logout()
        if not self._require_auth(): return
        routes = {
            "/api/restart":           self._post_restart,
            "/api/change-password":   self._post_change_password,
            "/api/test-nvr":          self._post_test_nvr,
            "/api/test-all-nvrs":     self._post_test_all_nvrs,
            "/api/health":            self._post_health,
            "/api/auto-disable-nvr":  self._post_auto_disable_nvr,
            "/api/client-log":        self._post_client_log,
        }
        handler = routes.get(self.path)
        if handler:
            return handler()
        self._send(405, {"error": "Method not allowed"})

    # ── Auth handlers ───────────────────────────────────────────────────────

    def _serve_login(self):
        if self._is_authenticated():
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return
        body = auth.LOGIN_PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _post_login(self):
        client_ip = self.client_address[0]
        ua = self.headers.get("User-Agent", "-") if self.headers else "-"

        allowed, retry_after = auth.check_login_rate(client_ip)
        if not allowed:
            config.log.warning(
                "Login blocked (rate-limit) ip=%s retry_after=%ds ua=%r",
                client_ip, retry_after, ua,
            )
            self._send(429, {
                "error": f"Too many login attempts. Try again in {retry_after}s",
                "retry_after": retry_after,
            })
            return

        try:
            data = json.loads(self._read_body())
        except (json.JSONDecodeError, ValueError):
            config.log.warning("Login bad JSON ip=%s ua=%r", client_ip, ua)
            self._send(400, {"error": "Invalid JSON"})
            return

        username = data.get("username", "")
        password = data.get("password", "")

        auth.record_login_attempt(client_ip)

        if not auth.verify_login(username, password):
            config.log.warning(
                "Login FAIL ip=%s user=%r ua=%r",
                client_ip, username, ua,
            )
            self._send(401, {"error": "Invalid credentials"})
            return

        token = auth.create_session(username)
        config.log.info(
            "Login OK ip=%s user=%s ua=%r",
            client_ip, username, ua,
        )
        resp = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(resp))
        cookie_flags = "Path=/; HttpOnly; SameSite=Strict"
        if config.use_tls:
            cookie_flags += "; Secure"
        self.send_header("Set-Cookie", f"dss_session={token}; {cookie_flags}")
        self.end_headers()
        self.wfile.write(resp)

    def _post_logout(self):
        client_ip = self.client_address[0]
        sess = auth.get_session(self.headers.get("Cookie"))
        config.log.info(
            "Logout ip=%s user=%s",
            client_ip, sess["username"] if sess else "?",
        )
        auth.drop_session(self.headers.get("Cookie"))
        resp = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(resp))
        self.send_header("Set-Cookie", "dss_session=; Path=/; HttpOnly; Max-Age=0")
        self.end_headers()
        self.wfile.write(resp)

    def _post_change_password(self):
        client_ip = self.client_address[0]
        sess = auth.get_session(self.headers.get("Cookie"))
        actor = sess["username"] if sess else "?"
        try:
            data = json.loads(self._read_body())
        except (json.JSONDecodeError, ValueError):
            self._send(400, {"error": "Invalid JSON"})
            return

        current = data.get("current_password", "")
        new_pw = data.get("new_password", "")

        if not new_pw or len(new_pw) < 4:
            config.log.warning(
                "Password change rejected (too short) ip=%s user=%s", client_ip, actor,
            )
            self._send(400, {"error": "Password must be at least 4 characters"})
            return

        creds = auth.load_credentials()
        if auth.hash_password(current) != creds["password_hash"]:
            config.log.warning(
                "Password change FAIL (wrong current) ip=%s user=%s", client_ip, actor,
            )
            self._send(401, {"error": "Current password is incorrect"})
            return

        creds["password_hash"] = auth.hash_password(new_pw)
        auth.save_credentials(creds)
        config.log.info(
            "Password changed ip=%s user=%s", client_ip, actor,
        )
        self._send(200, {"ok": True})

    # ── NVR test / health handlers ──────────────────────────────────────────

    def _post_test_nvr(self):
        client_ip = self.client_address[0]
        sess = auth.get_session(self.headers.get("Cookie"))
        actor = sess["username"] if sess else "?"
        try:
            data = json.loads(self._read_body())
        except (json.JSONDecodeError, ValueError):
            self._send(400, {"error": "Invalid JSON"})
            return

        ip       = data.get("ip", "")
        port     = data.get("port", 554)
        username = data.get("username", "admin")
        password = data.get("password", "")
        channel  = data.get("channel", 1)
        nvr_id   = data.get("nvr_id", "")
        vendor   = data.get("vendor", "dahua")

        if not ip:
            self._send(400, {"error": "IP is required"})
            return

        config.log.info(
            "NVR test triggered by=%s@%s target=%s:%s ch=%s vendor=%s nvr_id=%s",
            actor, client_ip, ip, port, channel, vendor, nvr_id or "?",
        )
        ok, message, extra = nvr.test_nvr_rtsp(
            ip, port, username, password, channel,
            nvr_id=nvr_id, vendor=vendor,
        )
        config.log.info(
            "NVR test result nvr_id=%s target=%s:%s ok=%s msg=%s",
            nvr_id or "?", ip, port, ok, message,
        )
        result = {"ok": ok, "message": message}
        result.update(extra)
        self._send(200, result)

    def _post_test_all_nvrs(self):
        """Test RTSP credentials for all enabled NVRs in the provided inventory."""
        client_ip = self.client_address[0]
        sess = auth.get_session(self.headers.get("Cookie"))
        actor = sess["username"] if sess else "?"
        try:
            data = json.loads(self._read_body())
        except (json.JSONDecodeError, ValueError):
            self._send(400, {"error": "Invalid JSON"})
            return

        defaults = data.get("global", {})
        nvr_count = sum(1 for n in data.get("nvrs", []) if n.get("enabled", True))
        config.log.info(
            "NVR test-all triggered by=%s@%s enabled_nvrs=%d",
            actor, client_ip, nvr_count,
        )
        results = []
        for n in data.get("nvrs", []):
            if not n.get("enabled", True):
                results.append({"id": n.get("id", "?"), "ok": None, "message": "Disabled (skipped)"})
                continue

            ip       = n.get("ip", "")
            port     = n.get("port", defaults.get("default_port", 554))
            username = n.get("username", defaults.get("default_username", "admin"))
            password = n.get("password", defaults.get("default_password", ""))
            nvr_id   = n.get("id", "")
            vendor   = n.get("vendor", defaults.get("default_vendor", "dahua"))

            ok, message, extra = nvr.test_nvr_rtsp(
                ip, port, username, password,
                nvr_id=nvr_id, vendor=vendor,
            )
            result = {"id": nvr_id, "ok": ok, "message": message}
            result.update(extra)
            results.append(result)

        failed = [r for r in results if r["ok"] is False]
        config.log.info(
            "NVR test-all complete tested=%d failed=%d",
            len(results), len(failed),
        )
        self._send(200, {"results": results, "failed_count": len(failed)})

    def _post_health(self):
        client_ip = self.client_address[0]
        try:
            inv = json.loads(config.INVENTORY.read_text())
        except Exception as e:
            config.log.warning("Health check failed to load inventory: %s", e)
            self._send(500, {"error": str(e)})
            return

        defaults = inv.get("global", {})
        targets = []
        results = []
        for n in inv.get("nvrs", []):
            if not n.get("enabled", True):
                results.append({"id": n["id"], "ok": False, "message": "Disabled"})
                continue
            port = n.get("port", defaults.get("default_port", 554))
            targets.append((n["id"], n["ip"], port))

        # Parallel TCP probes — sequential would be O(N × 3s timeout) on dead NVRs.
        if targets:
            t0 = time.monotonic()

            def _probe(t):
                nvr_id, ip, port = t
                ok, msg = nvr.check_nvr_reachable(ip, port)
                config.log.debug(
                    "Health probe nvr_id=%s %s:%s ok=%s msg=%s",
                    nvr_id, ip, port, ok, msg,
                )
                return {"id": nvr_id, "ok": ok, "message": msg}

            workers = min(32, len(targets))
            with ThreadPoolExecutor(max_workers=workers) as ex:
                results.extend(ex.map(_probe, targets))
            dur = time.monotonic() - t0
        else:
            dur = 0.0

        ok_count = sum(1 for r in results if r["ok"])
        config.log.info(
            "Health check by=%s total=%d ok=%d probed=%d in %.2fs",
            client_ip, len(results), ok_count, len(targets), dur,
        )
        self._send(200, {"results": results})

    def _post_auto_disable_nvr(self):
        """Auto-disable an NVR after repeated auth failures (called by client)."""
        client_ip = self.client_address[0]
        sess = auth.get_session(self.headers.get("Cookie"))
        actor = sess["username"] if sess else "?"
        try:
            data = json.loads(self._read_body())
        except (json.JSONDecodeError, ValueError):
            self._send(400, {"error": "Invalid JSON"})
            return

        nvr_id = data.get("nvr_id", "")
        reason = data.get("reason", "Auth failure detected by client")

        if not nvr_id:
            self._send(400, {"error": "nvr_id is required"})
            return

        config.log.warning(
            "Auto-disable NVR requested by=%s@%s nvr_id=%s reason=%r",
            actor, client_ip, nvr_id, reason,
        )

        try:
            inv = json.loads(config.INVENTORY.read_text())
        except Exception as e:
            self._send(500, {"error": str(e)})
            return

        found = False
        for n in inv.get("nvrs", []):
            if n["id"] == nvr_id:
                if not n.get("enabled", True):
                    self._send(200, {"ok": True, "message": "Already disabled"})
                    return
                n["enabled"] = False
                found = True
                nvr.log_nvr_event(nvr_id, n.get("ip", ""), "auto_disabled", reason)
                break

        if not found:
            self._send(404, {"error": f"NVR '{nvr_id}' not found"})
            return

        if config.INVENTORY.exists():
            shutil.copy2(config.INVENTORY, str(config.INVENTORY) + ".bak")
        config.INVENTORY.write_text(json.dumps(inv, indent=2) + "\n")

        self._send(200, {"ok": True, "message": f"NVR '{nvr_id}' disabled: {reason}"})

    # ── Event log / lockouts handlers ───────────────────────────────────────

    def _get_events(self, query_string):
        params = parse_qs(query_string)
        nvr_id = params.get("nvr_id", [None])[0]
        limit = min(int(params.get("limit", [200])[0]), 1000)
        events = nvr.read_events(nvr_id=nvr_id, limit=limit)
        self._send(200, {"events": events})

    def _get_lockouts(self):
        import time
        now = time.time()
        result = {}
        for ip, info in list(nvr.nvr_lockouts.items()):
            remaining = info["cooldown"] - (now - info["banned_at"])
            if remaining > 0:
                result[ip] = {
                    "banned_at": info["banned_at"],
                    "banned_until": info["banned_at"] + info["cooldown"],
                    "remaining": int(remaining),
                }
            else:
                nvr.nvr_lockouts.pop(ip, None)
        self._send(200, {"lockouts": result})

    def _delete_lockouts(self):
        client_ip = self.client_address[0]
        sess = auth.get_session(self.headers.get("Cookie"))
        actor = sess["username"] if sess else "?"
        count = len(nvr.nvr_lockouts)
        nvr.nvr_lockouts.clear()
        nvr._save_lockouts()
        config.log.info("Cleared %d lockouts by=%s@%s", count, actor, client_ip)
        self._send(200, {"ok": True, "cleared": count})

    # ── Client log handler ──────────────────────────────────────────────────

    def _post_client_log(self):
        """Receive diagnostic log entries from the browser client."""
        try:
            data = json.loads(self._read_body())
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

    # ── Debug log viewer ────────────────────────────────────────────────────

    def _get_debug_log(self, query_string):
        """Return last N lines of dss_debug.log."""
        params = parse_qs(query_string)
        lines = int(params.get("lines", [500])[0])
        lines = min(lines, 5000)
        try:
            if not config.DEBUG_LOG.exists():
                self._send(200, {"lines": [], "total": 0})
                return
            all_lines = config.DEBUG_LOG.read_text(encoding="utf-8").strip().split("\n")
            tail = all_lines[-lines:]
            self._send(200, {"lines": tail, "total": len(all_lines)})
        except Exception as e:
            self._send(500, {"error": str(e)})

    # ── Inventory handlers ──────────────────────────────────────────────────

    def _get_inventory(self):
        try:
            data = json.loads(config.INVENTORY.read_text())
            self._send(200, data)
        except Exception as e:
            self._send(500, {"error": str(e)})

    def _put_inventory(self):
        client_ip = self.client_address[0]
        sess = auth.get_session(self.headers.get("Cookie"))
        actor = sess["username"] if sess else "?"
        try:
            data = json.loads(self._read_body())
        except (json.JSONDecodeError, ValueError) as e:
            config.log.warning("Inventory PUT bad JSON by=%s@%s: %s", actor, client_ip, e)
            self._send(400, {"error": f"Invalid JSON: {e}"})
            return

        err = nvr.validate_inventory(data)
        if err:
            config.log.warning(
                "Inventory PUT validation failed by=%s@%s: %s", actor, client_ip, err,
            )
            self._send(400, {"error": err})
            return

        nvr_count = len(data.get("nvrs", []))
        enabled_count = sum(1 for n in data.get("nvrs", []) if n.get("enabled", True))
        config.log.info(
            "Inventory PUT by=%s@%s nvrs=%d enabled=%d (regenerating config + restarting MediaMTX)",
            actor, client_ip, nvr_count, enabled_count,
        )

        if config.INVENTORY.exists():
            shutil.copy2(config.INVENTORY, str(config.INVENTORY) + ".bak")
        config.INVENTORY.write_text(json.dumps(data, indent=2) + "\n")

        # Regenerate MediaMTX config
        result = subprocess.run(
            [sys.executable, str(config.GENERATE_SCRIPT)],
            cwd=str(config.DIR),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            bak = str(config.INVENTORY) + ".bak"
            if os.path.exists(bak):
                shutil.copy2(bak, config.INVENTORY)
            config.log.error(
                "generate_config.py failed (rolled back) by=%s rc=%d stderr=%s",
                actor, result.returncode, result.stderr.strip(),
            )
            self._send(500, {"error": f"generate_config.py failed (rolled back): {result.stderr}"})
            return

        try:
            mediamtx.restart()
        except RuntimeError as e:
            config.log.error("MediaMTX restart failed after inventory PUT: %s", e)
            self._send(500, {"error": str(e)})
            return

        config.log.info("Inventory PUT applied by=%s — MediaMTX restarted", actor)
        self._send(200, {"ok": True, "message": result.stdout.strip()})

    def _patch_inventory(self):
        """Save inventory without regenerating config / restarting MediaMTX."""
        client_ip = self.client_address[0]
        sess = auth.get_session(self.headers.get("Cookie"))
        actor = sess["username"] if sess else "?"
        try:
            data = json.loads(self._read_body())
        except (json.JSONDecodeError, ValueError) as e:
            self._send(400, {"error": f"Invalid JSON: {e}"})
            return

        err = nvr.validate_inventory(data)
        if err:
            config.log.warning(
                "Inventory PATCH validation failed by=%s@%s: %s", actor, client_ip, err,
            )
            self._send(400, {"error": err})
            return

        if config.INVENTORY.exists():
            shutil.copy2(config.INVENTORY, str(config.INVENTORY) + ".bak")
        config.INVENTORY.write_text(json.dumps(data, indent=2) + "\n")
        config.log.info(
            "Inventory PATCH saved by=%s@%s nvrs=%d (no restart)",
            actor, client_ip, len(data.get("nvrs", [])),
        )
        self._send(200, {"ok": True})

    def _post_restart(self):
        client_ip = self.client_address[0]
        sess = auth.get_session(self.headers.get("Cookie"))
        actor = sess["username"] if sess else "?"
        config.log.info("MediaMTX restart requested by=%s@%s", actor, client_ip)
        try:
            mediamtx.restart()
            self._send(200, {"ok": True})
        except RuntimeError as e:
            config.log.error("MediaMTX restart failed: %s", e)
            self._send(500, {"error": str(e)})

    # ── Helpers ─────────────────────────────────────────────────────────────

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
