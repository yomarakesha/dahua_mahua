#!/usr/bin/env bash
#
# License Manager launcher — starts the vendor-side panel with one command.
#
#   ./license-manager/run.sh
#
# Runs the panel inside the backend's virtualenv (creating it + installing deps
# on first run if needed), then opens http://127.0.0.1:7070 in your browser.
# Localhost-only; it holds your PRIVATE signing key — never expose it.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="$REPO_ROOT/backend/.venv"
PY="$VENV/bin/python"
URL="http://127.0.0.1:7070"

# 1. Ensure the virtualenv exists.
if [ ! -x "$PY" ]; then
  echo "==> Backend virtualenv not found — creating $VENV"
  python3 -m venv "$VENV"
fi

# 2. Ensure the panel's dependencies are present (fast no-op if already there).
if ! "$PY" -c "import fastapi, uvicorn, cryptography" >/dev/null 2>&1; then
  echo "==> Installing dependencies..."
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -r "$SCRIPT_DIR/requirements.txt"
fi

# 3. Open the browser a moment after the server starts (best-effort).
(
  sleep 2
  if command -v open >/dev/null 2>&1; then open "$URL"          # macOS
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" # Linux
  fi
) >/dev/null 2>&1 &

# 4. Run the panel (foreground; Ctrl-C to stop).
echo "==> License Manager starting at $URL  (Ctrl-C to stop)"
exec "$PY" "$SCRIPT_DIR/app.py"
