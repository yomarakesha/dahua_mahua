# Build the NATIVE (no-Docker) offline bundle for Kanagatly VMS - WINDOWS target.
#
# Run this ONCE on an internet-connected Windows build box that MATCHES the
# target server (Windows x64). It gathers everything the air-gapped server needs
# - Python wheels, the go2rtc + ffmpeg + Caddy binaries, the built web UI, and
# the backend source - into a self-contained bundle folder you copy across and
# run install.ps1 from.
#
#   powershell -ExecutionPolicy Bypass -File deploy\native\build-bundle.ps1 [-Out <dir>]
#
# The target server then needs ONLY a Python 3.12 interpreter (see README for
# bundling Python too). No internet, no pip index, no Docker, no Node.
[CmdletBinding()]
param(
  [string]$Out = "",
  [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# ── pinned upstream versions (keep in sync with build-bundle.sh) ─────────────
$GO2RTC_VER = "v1.9.14"
$CADDY_VER  = "2.8.4"
$FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"  # gyan.dev static Windows build
$NSSM_URL   = "https://nssm.cc/release/nssm-2.24.zip"                              # service wrapper (win64)

function Ok($m){ Write-Host "[ok] $m" -ForegroundColor Green }
function Step($m){ Write-Host "-> $m" -ForegroundColor Cyan }
function Err($m){ Write-Host "[x] $m" -ForegroundColor Red }

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $here "..\..")).Path
if (-not $Out) { $Out = Join-Path $root "kanagatly-vms-native" }

Ok "Target: windows/amd64"

foreach ($d in @("wheelhouse","bin","www","backend","deploy")) {
  New-Item -ItemType Directory -Force -Path (Join-Path $Out $d) | Out-Null
}

# ── (a) pip wheels ───────────────────────────────────────────────────────────
# Same-OS build -> plain download. For a CROSS-OS wheelhouse (build on Linux for
# a Windows target) add:  --platform win_amd64 --python-version 3.12 --abi cp312
#   --implementation cp --only-binary=:all:
Step "Downloading Python wheels -> wheelhouse\"
& $Python -m pip download -r (Join-Path $root "backend\requirements.txt") `
    -d (Join-Path $Out "wheelhouse") --only-binary=:all:
if ($LASTEXITCODE -ne 0) { Err "pip download failed"; exit 1 }
& $Python -m pip download pip setuptools wheel -d (Join-Path $Out "wheelhouse") --only-binary=:all:
Ok "wheels downloaded"

# ── (b) go2rtc binary (pinned) ───────────────────────────────────────────────
Step "Downloading go2rtc $GO2RTC_VER"
$g2zip = Join-Path $Out "bin\go2rtc.zip"
Invoke-WebRequest -Uri "https://github.com/AlexxIT/go2rtc/releases/download/$GO2RTC_VER/go2rtc_win64.zip" -OutFile $g2zip
Expand-Archive -Path $g2zip -DestinationPath (Join-Path $Out "bin") -Force
Remove-Item $g2zip -Force
if (-not (Test-Path (Join-Path $Out "bin\go2rtc.exe"))) { Err "go2rtc.exe not found after extract"; exit 1 }
Ok "go2rtc -> bin\go2rtc.exe"

# ── (c) static ffmpeg (gyan.dev) ─────────────────────────────────────────────
Step "Downloading static ffmpeg (gyan.dev)"
$ffzip = Join-Path $Out "bin\ffmpeg.zip"
Invoke-WebRequest -Uri $FFMPEG_URL -OutFile $ffzip
$fftmp = Join-Path $Out "bin\_fftmp"
Expand-Archive -Path $ffzip -DestinationPath $fftmp -Force
# archive lays out ffmpeg-*-essentials_build\bin\ffmpeg.exe
Get-ChildItem -Path $fftmp -Recurse -Include ffmpeg.exe,ffprobe.exe |
  ForEach-Object { Copy-Item $_.FullName -Destination (Join-Path $Out "bin") -Force }
Remove-Item $ffzip -Force; Remove-Item $fftmp -Recurse -Force
if (-not (Test-Path (Join-Path $Out "bin\ffmpeg.exe"))) { Err "ffmpeg.exe not found after extract"; exit 1 }
Ok "ffmpeg.exe + ffprobe.exe -> bin\"

# ── (d) Caddy TLS ingress binary (pinned) ────────────────────────────────────
Step "Downloading Caddy $CADDY_VER"
$cdzip = Join-Path $Out "bin\caddy.zip"
Invoke-WebRequest -Uri "https://github.com/caddyserver/caddy/releases/download/v$CADDY_VER/caddy_${CADDY_VER}_windows_amd64.zip" -OutFile $cdzip
$cdtmp = Join-Path $Out "bin\_cdtmp"
Expand-Archive -Path $cdzip -DestinationPath $cdtmp -Force
Copy-Item (Join-Path $cdtmp "caddy.exe") -Destination (Join-Path $Out "bin") -Force
Remove-Item $cdzip -Force; Remove-Item $cdtmp -Recurse -Force
Ok "caddy -> bin\caddy.exe"

# ── NSSM service wrapper (so install.ps1 needs nothing preinstalled) ─────────
Step "Downloading NSSM (service wrapper)"
$nszip = Join-Path $Out "bin\nssm.zip"
Invoke-WebRequest -Uri $NSSM_URL -OutFile $nszip
$nstmp = Join-Path $Out "bin\_nstmp"
Expand-Archive -Path $nszip -DestinationPath $nstmp -Force
$nssrc = Get-ChildItem -Path $nstmp -Recurse -Filter nssm.exe |
  Where-Object { $_.FullName -match 'win64' } | Select-Object -First 1
if (-not $nssrc) { $nssrc = Get-ChildItem -Path $nstmp -Recurse -Filter nssm.exe | Select-Object -First 1 }
Copy-Item $nssrc.FullName -Destination (Join-Path $Out "bin") -Force
Remove-Item $nszip -Force; Remove-Item $nstmp -Recurse -Force
Ok "nssm -> bin\nssm.exe"

# ── (e) built web UI (web-react\dist) ────────────────────────────────────────
Step "Building the web UI (npm ci && npm run build)..."
Push-Location (Join-Path $root "web-react")
try {
  & npm ci
  if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
  & npm run build
  if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
} finally { Pop-Location }
Copy-Item -Recurse -Force (Join-Path $root "web-react\dist\*") (Join-Path $Out "www")
Ok "web UI -> www\"

# ── backend source + runtime templates ───────────────────────────────────────
Step "Staging backend source + templates"
Copy-Item -Recurse -Force (Join-Path $root "backend\app")     (Join-Path $Out "backend\app")
Copy-Item -Recurse -Force (Join-Path $root "backend\alembic") (Join-Path $Out "backend\alembic")
Copy-Item -Force (Join-Path $root "backend\alembic.ini")      (Join-Path $Out "backend\alembic.ini")
Copy-Item -Force (Join-Path $root "backend\requirements.txt") (Join-Path $Out "backend\requirements.txt")
Get-ChildItem -Path (Join-Path $Out "backend") -Recurse -Directory -Filter __pycache__ |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Copy-Item -Force (Join-Path $root "go2rtc.base.yaml") (Join-Path $Out "go2rtc.base.yaml")
Copy-Item -Force (Join-Path $here "Caddyfile")        (Join-Path $Out "Caddyfile")
Copy-Item -Force (Join-Path $here "install.ps1")      (Join-Path $Out "install.ps1")
Copy-Item -Force (Join-Path $here "install.sh")       (Join-Path $Out "install.sh")
if (Test-Path (Join-Path $here "README.md")) { Copy-Item -Force (Join-Path $here "README.md") (Join-Path $Out "README.md") }
Copy-Item -Recurse -Force (Join-Path $here "systemd") (Join-Path $Out "deploy\systemd")

Write-Host ""
Write-Host "────────────────────────────────────────────────────────────────────────────"
Write-Host "  Native offline bundle ready:  $Out"
Write-Host "    wheelhouse\   pip wheels (offline install)"
Write-Host "    bin\          go2rtc.exe, ffmpeg.exe, ffprobe.exe, caddy.exe"
Write-Host "    www\          built web UI"
Write-Host "    backend\      app, alembic, requirements.txt"
Write-Host "    Caddyfile, go2rtc.base.yaml, install.ps1"
Write-Host ""
Write-Host "  Copy the whole folder to the target Windows server, then there (as Admin):"
Write-Host "    powershell -ExecutionPolicy Bypass -File install.ps1"
Write-Host "────────────────────────────────────────────────────────────────────────────"
