#!/usr/bin/env bash
#
# Build the NATIVE (no-Docker) offline bundle for Kanagatly VMS — LINUX target.
#
# Run this ONCE on an internet-connected build box that MATCHES the target
# server's OS + CPU arch (Linux x86_64 for a Linux x86_64 server, etc.). It
# gathers everything the air-gapped server needs — Python wheels, the go2rtc +
# ffmpeg + Caddy binaries, the built web UI, and the backend source — into a
# self-contained bundle/ folder you copy across and run install.sh from.
#
#   ./deploy/native/build-bundle.sh [output-dir]
#
# The target server then needs ONLY a Python 3.12 interpreter (see README for
# bundling Python too). No internet, no pip index, no Docker, no Node.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$ROOT/kanagatly-vms-native}"

# ── pinned upstream versions (keep in sync with build-bundle.ps1) ────────────
GO2RTC_VER="v1.9.14"
CADDY_VER="2.8.4"

c_ok(){ printf "\033[32m✓ %s\033[0m\n" "$*"; }
c_step(){ printf "\033[36m→ %s\033[0m\n" "$*"; }
c_err(){ printf "\033[31m✗ %s\033[0m\n" "$*" >&2; }

# ── detect target OS/arch (must match the build box == the target server) ────
OS="$(uname -s | tr 'A-Z' 'a-z')"          # linux / darwin
case "$(uname -m)" in
  x86_64|amd64) ARCH=amd64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  *) c_err "unsupported CPU arch $(uname -m) — edit this script"; exit 1 ;;
esac
[ "$OS" = "linux" ] || { c_err "build-bundle.sh targets Linux; use build-bundle.ps1 on Windows"; exit 1; }
c_ok "Target: linux/$ARCH"

command -v curl  >/dev/null || { c_err "curl required";  exit 1; }
command -v tar   >/dev/null || { c_err "tar required";   exit 1; }
command -v npm   >/dev/null || { c_err "npm required (to build the web UI)"; exit 1; }
PYBIN="${PYTHON:-python3}"
command -v "$PYBIN" >/dev/null || { c_err "python3 required (to download wheels)"; exit 1; }

mkdir -p "$OUT"/{wheelhouse,bin,www,backend,deploy}

# ── (a) pip wheels ───────────────────────────────────────────────────────────
# Same-OS/arch build → plain download is enough. For a CROSS-OS wheelhouse add:
#   --platform manylinux2014_x86_64 --python-version 3.12 --abi cp312 --implementation cp
# (repeat per platform tag). --only-binary=:all: guarantees wheels (no sdists
# that would need a compiler on the air-gapped target).
c_step "Downloading Python wheels → wheelhouse/"
"$PYBIN" -m pip download -r "$ROOT/backend/requirements.txt" \
  -d "$OUT/wheelhouse" --only-binary=:all:
# pip itself (for `python -m ensurepip`-free venvs on the target, offline)
"$PYBIN" -m pip download pip setuptools wheel -d "$OUT/wheelhouse" --only-binary=:all: || true
c_ok "wheels: $(ls "$OUT/wheelhouse" | wc -l | tr -d ' ') files"

# ── (b) go2rtc binary (pinned) ───────────────────────────────────────────────
c_step "Downloading go2rtc $GO2RTC_VER"
curl -fsSL --retry 5 --retry-delay 2 -o "$OUT/bin/go2rtc" \
  "https://github.com/AlexxIT/go2rtc/releases/download/$GO2RTC_VER/go2rtc_linux_$ARCH"
chmod +x "$OUT/bin/go2rtc"
c_ok "go2rtc → bin/go2rtc"

# ── (c) static ffmpeg ────────────────────────────────────────────────────────
# Source: John Van Sickle static builds (https://johnvansickle.com/ffmpeg/) —
# fully static, no system deps. amd64 + arm64 provided. (BtbN static builds at
# https://github.com/BtbN/FFmpeg-Builds are an equivalent alternative.)
c_step "Downloading static ffmpeg (johnvansickle.com)"
FF_TARBALL="ffmpeg-release-${ARCH}-static.tar.xz"
curl -fsSL --retry 5 --retry-delay 2 -o "$OUT/bin/$FF_TARBALL" \
  "https://johnvansickle.com/ffmpeg/releases/$FF_TARBALL"
tar -xJf "$OUT/bin/$FF_TARBALL" -C "$OUT/bin" --strip-components=1 --wildcards '*/ffmpeg' '*/ffprobe'
rm -f "$OUT/bin/$FF_TARBALL"
chmod +x "$OUT/bin/ffmpeg" "$OUT/bin/ffprobe" 2>/dev/null || true
c_ok "ffmpeg + ffprobe → bin/"

# ── (d) Caddy TLS ingress binary (pinned) ────────────────────────────────────
c_step "Downloading Caddy $CADDY_VER"
curl -fsSL --retry 5 --retry-delay 2 -o "$OUT/bin/caddy.tar.gz" \
  "https://github.com/caddyserver/caddy/releases/download/v$CADDY_VER/caddy_${CADDY_VER}_linux_$ARCH.tar.gz"
tar -xzf "$OUT/bin/caddy.tar.gz" -C "$OUT/bin" caddy
rm -f "$OUT/bin/caddy.tar.gz"
chmod +x "$OUT/bin/caddy"
c_ok "caddy → bin/caddy"

# ── (e) built web UI (web-react/dist) ────────────────────────────────────────
c_step "Building the web UI (npm ci && npm run build)…"
( cd "$ROOT/web-react" && npm ci && npm run build )
cp -R "$ROOT/web-react/dist/." "$OUT/www/"
c_ok "web UI → www/"

# ── backend source + runtime templates ───────────────────────────────────────
c_step "Staging backend source + templates"
cp -R "$ROOT/backend/app"        "$OUT/backend/app"
cp -R "$ROOT/backend/alembic"    "$OUT/backend/alembic"
cp    "$ROOT/backend/alembic.ini" "$OUT/backend/alembic.ini"
cp    "$ROOT/backend/requirements.txt" "$OUT/backend/requirements.txt"
# strip caches so the bundle is clean/small
find "$OUT/backend" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

cp "$ROOT/go2rtc.base.yaml"        "$OUT/go2rtc.base.yaml"
cp "$HERE/Caddyfile"               "$OUT/Caddyfile"
cp "$HERE/install.sh"              "$OUT/install.sh"
cp "$HERE/install.ps1"             "$OUT/install.ps1"
cp "$HERE/README.md"               "$OUT/README.md" 2>/dev/null || true
cp -R "$HERE/systemd"              "$OUT/deploy/systemd"
chmod +x "$OUT/install.sh"

SIZE="$(du -sh "$OUT" 2>/dev/null | awk '{print $1}')"
cat <<EOF

────────────────────────────────────────────────────────────────────────────
  Native offline bundle ready:  $OUT  (${SIZE:-?})
    wheelhouse/   pip wheels (offline install)
    bin/          go2rtc, ffmpeg, ffprobe, caddy  (static, no deps)
    www/          built web UI
    backend/      app, alembic, requirements.txt
    Caddyfile, go2rtc.base.yaml, deploy/systemd/*.service
    install.sh (Linux)   install.ps1 (Windows)

  Copy the whole folder to the target Linux server, then there:
    sudo ./install.sh
────────────────────────────────────────────────────────────────────────────
EOF
