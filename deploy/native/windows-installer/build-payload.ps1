# Assemble the Kanagatly VMS SERVER installer PAYLOAD (Windows x64).
#
# Produces a self-contained folder that Inno Setup bundles verbatim. The firm
# needs NOTHING preinstalled: this payload ships an EMBEDDABLE CPython 3.12 with
# every backend dependency baked into python\Lib\site-packages, plus the bundled
# native binaries (go2rtc/ffmpeg/caddy/nssm), the pre-built web UI, and the
# runtime templates. postinstall.ps1 (run elevated by the installer) drives the
# rest with the BUNDLED python.exe — never a system Python.
#
#   powershell -ExecutionPolicy Bypass -File build-payload.ps1 -Out <payloadDir>
#
# Designed to run in CI on windows-latest (see .github/workflows/windows-installer.yml).
[CmdletBinding()]
param(
  [string]$Out = ""
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# ── pinned upstream versions ─────────────────────────────────────────────────
$PY_EMBED_VER = "3.12.8"                                                          # embeddable CPython
$GO2RTC_VER   = "v1.9.14"                                                         # GitHub release tag IS v-prefixed
$CADDY_VER    = "2.8.4"
$FFMPEG_URL   = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" # gyan.dev static Windows build
$NSSM_URL     = "https://nssm.cc/release/nssm-2.24.zip"                            # service wrapper (win64)
$GETPIP_URL   = "https://bootstrap.pypa.io/get-pip.py"

function Ok($m){ Write-Host "[ok] $m" -ForegroundColor Green }
function Step($m){ Write-Host "-> $m" -ForegroundColor Cyan }
function Err($m){ Write-Host "[x] $m" -ForegroundColor Red }

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $here "..\..\..")).Path                          # repo root
if (-not $Out) { $Out = Join-Path $root "kanagatly-vms-server-payload" }
if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }

Ok "Repo root : $root"
Ok "Payload   : $Out"
Ok "Target    : windows/amd64  (embeddable CPython $PY_EMBED_VER)"

foreach ($d in @("python","bin","www","backend")) {
  New-Item -ItemType Directory -Force -Path (Join-Path $Out $d) | Out-Null
}

# ── (a) embeddable CPython + PRE-INSTALLED deps ──────────────────────────────
# The embeddable distribution is fully RELOCATABLE (its sys.path comes from the
# python3xx._pth file, using paths relative to python.exe — NOT the build path),
# so unlike a venv it survives being moved to the firm's install dir. We enable
# site + Lib\site-packages, bootstrap pip via get-pip.py, then pip-install the
# backend deps as ordinary win_amd64 wheels (self-contained, no compiler needed).
Step "Downloading embeddable CPython $PY_EMBED_VER"
$pyDir  = Join-Path $Out "python"
$pyZip  = Join-Path $Out "python-embed.zip"
Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$PY_EMBED_VER/python-$PY_EMBED_VER-embed-amd64.zip" -OutFile $pyZip
Expand-Archive -Path $pyZip -DestinationPath $pyDir -Force
Remove-Item $pyZip -Force
$pyExe = Join-Path $pyDir "python.exe"
if (-not (Test-Path $pyExe)) { Err "python.exe missing after extract"; exit 1 }

# Rewrite the ._pth so the runtime sees: the stdlib zip, its own dir, the SIBLING
# backend\ (so `app` imports regardless of CWD — NSSM runs the service from
# backend\, alembic/create_admin run from there too), and Lib\site-packages;
# `import site` makes pip + .pth processing work. `..\backend` is RELATIVE so it
# resolves correctly at whatever path the firm installs to.
$pth = Get-ChildItem -Path $pyDir -Filter "python*._pth" | Select-Object -First 1
if (-not $pth) { Err "python*._pth not found in embeddable dist"; exit 1 }
@"
python312.zip
.
..\backend
Lib\site-packages

import site
"@ | Set-Content -Encoding ascii $pth.FullName
Ok "embeddable python -> python\  (._pth: $($pth.Name))"

Step "Bootstrapping pip (get-pip.py) into the embeddable runtime"
$getpip = Join-Path $Out "get-pip.py"
Invoke-WebRequest -Uri $GETPIP_URL -OutFile $getpip
& $pyExe $getpip --no-warn-script-location
if ($LASTEXITCODE -ne 0) { Err "get-pip failed"; exit 1 }
Remove-Item $getpip -Force

Step "Installing backend deps into python\Lib\site-packages"
& $pyExe -m pip install --no-cache-dir --no-warn-script-location `
    -r (Join-Path $root "backend\requirements.txt")
if ($LASTEXITCODE -ne 0) { Err "pip install of backend deps failed"; exit 1 }
# Smoke-test that the whole import surface loads from the relocated runtime.
& $pyExe -c "import fastapi, uvicorn, sqlalchemy, alembic, cryptography, argon2, asyncpg, aiosqlite, httpx, yaml, jwt; print('deps OK')"
if ($LASTEXITCODE -ne 0) { Err "bundled deps failed to import"; exit 1 }
Ok "backend deps baked into the embeddable runtime"

# ── (b) go2rtc (pinned) ──────────────────────────────────────────────────────
Step "Downloading go2rtc $GO2RTC_VER"
$g2zip = Join-Path $Out "bin\go2rtc.zip"
Invoke-WebRequest -Uri "https://github.com/AlexxIT/go2rtc/releases/download/$GO2RTC_VER/go2rtc_win64.zip" -OutFile $g2zip
Expand-Archive -Path $g2zip -DestinationPath (Join-Path $Out "bin") -Force
Remove-Item $g2zip -Force
if (-not (Test-Path (Join-Path $Out "bin\go2rtc.exe"))) { Err "go2rtc.exe not found after extract"; exit 1 }
Ok "go2rtc -> bin\go2rtc.exe"

# ── (c) static ffmpeg + ffprobe (gyan.dev) ───────────────────────────────────
Step "Downloading static ffmpeg (gyan.dev)"
$ffzip = Join-Path $Out "bin\ffmpeg.zip"
Invoke-WebRequest -Uri $FFMPEG_URL -OutFile $ffzip
$fftmp = Join-Path $Out "bin\_fftmp"
Expand-Archive -Path $ffzip -DestinationPath $fftmp -Force
Get-ChildItem -Path $fftmp -Recurse -Include ffmpeg.exe,ffprobe.exe |
  ForEach-Object { Copy-Item $_.FullName -Destination (Join-Path $Out "bin") -Force }
Remove-Item $ffzip -Force; Remove-Item $fftmp -Recurse -Force
if (-not (Test-Path (Join-Path $Out "bin\ffmpeg.exe"))) { Err "ffmpeg.exe not found after extract"; exit 1 }
Ok "ffmpeg.exe + ffprobe.exe -> bin\"

# ── (d) Caddy TLS ingress (pinned) ───────────────────────────────────────────
Step "Downloading Caddy $CADDY_VER"
$cdzip = Join-Path $Out "bin\caddy.zip"
Invoke-WebRequest -Uri "https://github.com/caddyserver/caddy/releases/download/v$CADDY_VER/caddy_${CADDY_VER}_windows_amd64.zip" -OutFile $cdzip
$cdtmp = Join-Path $Out "bin\_cdtmp"
Expand-Archive -Path $cdzip -DestinationPath $cdtmp -Force
Copy-Item (Join-Path $cdtmp "caddy.exe") -Destination (Join-Path $Out "bin") -Force
Remove-Item $cdzip -Force; Remove-Item $cdtmp -Recurse -Force
Ok "caddy -> bin\caddy.exe"

# ── (e) NSSM service wrapper (win64) ─────────────────────────────────────────
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

# ── (f) built web UI (web-react\dist) ────────────────────────────────────────
Step "Building the web UI (npm ci && npm run build)"
Push-Location (Join-Path $root "web-react")
try {
  & npm ci
  if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
  & npm run build
  if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
} finally { Pop-Location }
Copy-Item -Recurse -Force (Join-Path $root "web-react\dist\*") (Join-Path $Out "www")
if (-not (Test-Path (Join-Path $Out "www\index.html"))) { Err "web UI build produced no index.html"; exit 1 }
Ok "web UI -> www\"

# ── (g) backend source + runtime templates ───────────────────────────────────
Step "Staging backend source + runtime templates"
Copy-Item -Recurse -Force (Join-Path $root "backend\app")     (Join-Path $Out "backend\app")
Copy-Item -Recurse -Force (Join-Path $root "backend\alembic") (Join-Path $Out "backend\alembic")
Copy-Item -Force (Join-Path $root "backend\alembic.ini")      (Join-Path $Out "backend\alembic.ini")
Copy-Item -Force (Join-Path $root "backend\requirements.txt") (Join-Path $Out "backend\requirements.txt")
# Vendor PUBLIC key — lets the VMS verify our signed licenses out of the box (no
# manual drop). Public half only; the private key never leaves the vendor panel.
$pubkey = Join-Path $root "backend\licensing\public_key.pem"
if (Test-Path $pubkey) {
  New-Item -ItemType Directory -Force -Path (Join-Path $Out "backend\licensing") | Out-Null
  Copy-Item -Force $pubkey (Join-Path $Out "backend\licensing\public_key.pem")
  Ok "bundled vendor public_key.pem (licenses verify out of the box)"
} else {
  Err "backend\licensing\public_key.pem missing — installs will need it dropped manually"
}
Get-ChildItem -Path (Join-Path $Out "backend") -Recurse -Directory -Filter __pycache__ |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Copy-Item -Force (Join-Path $root "go2rtc.base.yaml")            (Join-Path $Out "go2rtc.base.yaml")
Copy-Item -Force (Join-Path $here "..\Caddyfile")               (Join-Path $Out "Caddyfile")
Copy-Item -Force (Join-Path $here "postinstall.ps1")           (Join-Path $Out "postinstall.ps1")
Copy-Item -Force (Join-Path $here "gen_cert.py")               (Join-Path $Out "gen_cert.py")
Copy-Item -Force (Join-Path $here "collect-diagnostics.ps1")   (Join-Path $Out "collect-diagnostics.ps1")
Ok "backend + templates + postinstall.ps1 + gen_cert.py staged"

Write-Host ""
Write-Host "────────────────────────────────────────────────────────────────────────────"
Write-Host "  Server installer payload ready:  $Out"
Write-Host "    python\        embeddable CPython $PY_EMBED_VER + backend deps (site-packages)"
Write-Host "    bin\           go2rtc.exe, ffmpeg.exe, ffprobe.exe, caddy.exe, nssm.exe"
Write-Host "    www\           built web UI"
Write-Host "    backend\       app, alembic, alembic.ini, requirements.txt"
Write-Host "    Caddyfile, go2rtc.base.yaml, postinstall.ps1"
Write-Host "  Next: iscc installer.iss  (SourceDir bundles this payload)."
Write-Host "────────────────────────────────────────────────────────────────────────────"
