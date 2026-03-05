#!/usr/bin/env python3
"""
DSS Server — Static file server + REST API + MediaMTX process management.

Replaces the old run.sh (python3 -m http.server + manual mediamtx launch).
Provides API endpoints for NVR inventory management and MediaMTX control.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

DIR = Path(__file__).resolve().parent
INVENTORY = DIR / "nvr_inventory.json"
MEDIAMTX_BIN = DIR / "mediamtx"
MEDIAMTX_CFG = DIR / "mediamtx.yml"
GENERATE_SCRIPT = DIR / "generate_config.py"
WEB_DIR = DIR / "web"
PORT = 8080

mtx_proc = None


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

    # ── Routing ──

    def do_GET(self):
        if self.path == "/api/inventory":
            return self._get_inventory()
        super().do_GET()

    def do_PUT(self):
        if self.path == "/api/inventory":
            return self._put_inventory()
        self._send(405, {"error": "Method not allowed"})

    def do_POST(self):
        if self.path == "/api/restart":
            return self._post_restart()
        self._send(405, {"error": "Method not allowed"})

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
            self._send(500, {"error": f"generate_config.py failed: {result.stderr}"})
            return

        # Restart MediaMTX
        try:
            restart_mediamtx()
        except RuntimeError as e:
            self._send(500, {"error": str(e)})
            return

        self._send(200, {"ok": True, "message": result.stdout.strip()})

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

    start_mediamtx()

    server = HTTPServer(("", PORT), Handler)
    print(f"  Web UI:    http://localhost:{PORT}")
    print("  MediaMTX:  http://localhost:9997")
    print("  Press Ctrl+C to stop")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    server.serve_forever()


if __name__ == "__main__":
    main()
