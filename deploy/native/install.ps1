# Kanagatly VMS - NATIVE (no-Docker) offline installer for WINDOWS (NSSM services).
#
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   powershell -ExecutionPolicy Bypass -File install.ps1 -HostIp 10.0.0.5
#   powershell -ExecutionPolicy Bypass -File install.ps1 -InstallDir D:\KanagatlyVMS
#
# Run from inside the bundle folder produced by build-bundle.ps1, in an ELEVATED
# (Administrator) PowerShell. Fully offline: installs Python deps from
# wheelhouse\, drops the bundled go2rtc/ffmpeg/caddy binaries, generates secrets,
# runs migrations, and registers + starts three NSSM services (dahua-go2rtc,
# dahua-backend, dahua-caddy).
#
# Idempotent: re-runs reuse an existing backend\.env (secrets kept) and refresh
# binaries/code + the WebRTC candidate for the current host IP.
[CmdletBinding()]
param(
  [string]$HostIp = "",
  [string]$InstallDir = "C:\KanagatlyVMS",
  [int]$HttpsPort = 8443
)
$ErrorActionPreference = "Stop"
# UTF-8 for bundled-python calls — a cp1251/Russian console else crashes on
# Unicode prints (go2rtc_config's "→") with UnicodeEncodeError.
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"

function Ok($m){ Write-Host "[ok] $m" -ForegroundColor Green }
function Step($m){ Write-Host "-> $m" -ForegroundColor Cyan }
function Err($m){ Write-Host "[x] $m" -ForegroundColor Red }

$src = Split-Path -Parent $MyInvocation.MyCommand.Path      # the bundle folder

# ── admin check (needed to register services) ────────────────────────────────
$isAdmin = ([Security.Principal.WindowsPrincipal] `
  [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Err "Run in an ELEVATED (Administrator) PowerShell."; exit 1 }

# ── locate NSSM (bundled bin\ preferred, else PATH) ──────────────────────────
$nssm = Join-Path $src "bin\nssm.exe"
if (-not (Test-Path $nssm)) { $c = Get-Command nssm.exe -ErrorAction SilentlyContinue; if ($c) { $nssm = $c.Source } }
if (-not (Test-Path $nssm)) { Err "nssm.exe not found (bundle bin\ or PATH). Install NSSM (https://nssm.cc) or re-run build-bundle.ps1 to bundle it."; exit 1 }
Ok "NSSM: $nssm"

# ── locate a Python 3.12+ interpreter (target must provide one; see README) ──
function Find-Python {
  foreach ($cand in @(@("py","-3.12"), @("py","-3.13"), @("python"), @("python3"))) {
    $exe = $cand[0]; $pre = if ($cand.Count -gt 1) { $cand[1] } else { $null }
    try {
      $args = @(); if ($pre) { $args += $pre }
      $args += @("-c","import sys;print('%d.%d'%sys.version_info[:2]) if sys.version_info[:2]>=(3,12) else sys.exit(1)")
      $v = & $exe @args 2>$null
      if ($LASTEXITCODE -eq 0 -and $v) { return ,@($exe,$pre) }
    } catch {}
  }
  return $null
}
$pyspec = Find-Python
if (-not $pyspec) { Err "Python 3.12+ not found. Install it from python.org (or bundle it - see README)."; exit 1 }
$pyExe = $pyspec[0]; $pyPre = $pyspec[1]
$pyInvoke = if ($pyPre) { "$pyExe $pyPre" } else { $pyExe }
Ok "Python launcher: $pyInvoke"

foreach ($f in @("wheelhouse","bin\go2rtc.exe","bin\ffmpeg.exe","bin\caddy.exe","backend\requirements.txt","www\index.html","Caddyfile","go2rtc.base.yaml")) {
  if (-not (Test-Path (Join-Path $src $f))) { Err "bundle is incomplete: missing $f (re-run build-bundle.ps1)"; exit 1 }
}

# ── stage files into InstallDir (preserve an existing .env) ──────────────────
Step "Installing into $InstallDir"
foreach ($d in @("bin","backend","www",".go2rtc",".caddy")) { New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir $d) | Out-Null }
Copy-Item -Recurse -Force (Join-Path $src "bin\*") (Join-Path $InstallDir "bin")
Copy-Item -Recurse -Force (Join-Path $src "www\*") (Join-Path $InstallDir "www")
Copy-Item -Force (Join-Path $src "Caddyfile")       (Join-Path $InstallDir "Caddyfile")
Copy-Item -Force (Join-Path $src "go2rtc.base.yaml") (Join-Path $InstallDir "go2rtc.base.yaml")
# backend code (never clobber a live .env)
Copy-Item -Recurse -Force (Join-Path $src "backend\app")     (Join-Path $InstallDir "backend\app")
Copy-Item -Recurse -Force (Join-Path $src "backend\alembic") (Join-Path $InstallDir "backend\alembic")
Copy-Item -Force (Join-Path $src "backend\alembic.ini")      (Join-Path $InstallDir "backend\alembic.ini")
Copy-Item -Force (Join-Path $src "backend\requirements.txt") (Join-Path $InstallDir "backend\requirements.txt")

# Bake the SPA root + HTTPS port straight into the Caddyfile (forward-slash path,
# quoted) so Caddy needs NO service env — the NSSM AppEnvironmentExtra path for
# WWW_ROOT is the least-reliable link; backend/go2rtc upstreams keep their
# localhost defaults inside the Caddyfile.
$wwwFwd = '"' + (((Join-Path $InstallDir "www")) -replace '\\','/') + '"'
$caddyfilePath = Join-Path $InstallDir "Caddyfile"
$cf = Get-Content $caddyfilePath -Raw
$cf = $cf -replace '\{\$WWW_ROOT\}', $wwwFwd
$cf = $cf -replace ':\{\$CADDY_HTTPS_PORT:8443\}', ":$HttpsPort"
Set-Content -Encoding ascii $caddyfilePath $cf
Ok "files staged (Caddyfile rendered: root=$wwwFwd port=$HttpsPort)"

$envFile = Join-Path $InstallDir "backend\.env"
$ffmpegBin = Join-Path $InstallDir "bin\ffmpeg.exe"
$venv = Join-Path $InstallDir "venv"
$venvPy = Join-Path $venv "Scripts\python.exe"

# ── venv from the offline wheelhouse ─────────────────────────────────────────
if (-not (Test-Path $venvPy)) {
  Step "Creating venv"
  if ($pyPre) { & $pyExe $pyPre -m venv $venv } else { & $pyExe -m venv $venv }
}
Step "Installing backend deps (offline, from wheelhouse)"
& $venvPy -m pip install --no-index --find-links (Join-Path $src "wheelhouse") --upgrade pip setuptools wheel | Out-Null
& $venvPy -m pip install --no-index --find-links (Join-Path $src "wheelhouse") -r (Join-Path $InstallDir "backend\requirements.txt")
if ($LASTEXITCODE -ne 0) { Err "offline pip install failed"; exit 1 }
Ok "deps installed"

# ── host LAN IP ──────────────────────────────────────────────────────────────
function Detect-Ip {
  if ($HostIp) { return $HostIp }
  # Skip Docker/WSL/Hyper-V/VM virtual switches — they hand out a bogus 172.x
  # gateway (seen: 172.18.0.1) no real client can reach.
  $skip = 'vEthernet|WSL|Docker|Hyper-V|Loopback|VirtualBox|VMware|Npcap|TAP|Bluetooth'
  $ip = Get-NetIPConfiguration | Where-Object {
          $_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq "Up" -and
          $_.NetAdapter.InterfaceDescription -notmatch $skip -and $_.InterfaceAlias -notmatch $skip } |
        Select-Object -First 1 -ExpandProperty IPv4Address | Select-Object -First 1 -ExpandProperty IPAddress
  if (-not $ip) {
    $ip = (Get-NetIPAddress -AddressFamily IPv4 |
      Where-Object { $_.IPAddress -match '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)' -and
                     $_.IPAddress -notmatch '^169\.254' -and $_.InterfaceAlias -notmatch $skip } |
      Select-Object -First 1 -ExpandProperty IPAddress)
  }
  return $ip
}
$hostIpVal = Detect-Ip
if (-not $hostIpVal) { Err "Could not detect host LAN IP - re-run with -HostIp <ip>"; exit 1 }
Ok "Host LAN IP: $hostIpVal"

# ── secrets + .env (generate once; keep secrets on re-run) ───────────────────
function Rand-Hex([int]$bytes){ -join ((1..$bytes) | ForEach-Object { '{0:x2}' -f (Get-Random -Max 256) }) }
if (-not (Test-Path $envFile)) {
  Step "Generating $envFile (secrets + WebRTC candidates)"
  $jwt = Rand-Hex 48
  $fernet = (& $venvPy -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())").Trim()
  $adminpw = Rand-Hex 8
  @"
# Generated by deploy\native\install.ps1 - keep private. Re-running reuses this file.
DATABASE_URL=sqlite+aiosqlite:///./dss.db
JWT_SECRET=$jwt
NVR_SECRET_KEY=$fernet
BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=$adminpw
RELAY=go2rtc
# Bundled static ffmpeg (playback + re-encode).
REENCODE_FFMPEG_BIN=$ffmpegBin
# WebRTC: this box's viewer-facing LAN IP advertised as a go2rtc ICE candidate.
GO2RTC_WEBRTC_CANDIDATES=$($hostIpVal):8556
# NVR clock offset (minutes east of UTC). Turkmenistan/UTC+5 = 300.
PLAYBACK_TZ_OFFSET_MINUTES=0
"@ | Set-Content -Encoding ascii $envFile
  Ok "backend admin password: $adminpw  (you'll change it on first login)"
} else {
  Ok "Reusing existing $envFile"
  $lines = Get-Content $envFile
  if ($lines -match '^GO2RTC_WEBRTC_CANDIDATES=') { $lines = $lines -replace '^GO2RTC_WEBRTC_CANDIDATES=.*', "GO2RTC_WEBRTC_CANDIDATES=$($hostIpVal):8556" }
  else { $lines += "GO2RTC_WEBRTC_CANDIDATES=$($hostIpVal):8556" }
  if ($lines -match '^REENCODE_FFMPEG_BIN=') { $lines = $lines -replace '^REENCODE_FFMPEG_BIN=.*', "REENCODE_FFMPEG_BIN=$ffmpegBin" }
  else { $lines += "REENCODE_FFMPEG_BIN=$ffmpegBin" }
  $lines | Set-Content -Encoding ascii $envFile
}

# ── render go2rtc runtime config (WebRTC candidates for THIS box) ────────────
Step "Rendering go2rtc config"
Push-Location (Join-Path $InstallDir "backend")
try {
  & $venvPy -m app.services.go2rtc_config --base (Join-Path $InstallDir "go2rtc.base.yaml") --out (Join-Path $InstallDir ".go2rtc\go2rtc.yaml")
  if ($LASTEXITCODE -ne 0) { throw "render failed" }
} catch {
  Err "go2rtc config render failed - copying template as-is"
  Copy-Item -Force (Join-Path $InstallDir "go2rtc.base.yaml") (Join-Path $InstallDir ".go2rtc\go2rtc.yaml")
} finally { Pop-Location }

# ── DB migrations + admin ────────────────────────────────────────────────────
Step "Applying database migrations (alembic upgrade head)"
Push-Location (Join-Path $InstallDir "backend")
try {
  & (Join-Path $venv "Scripts\alembic.exe") upgrade head
  if ($LASTEXITCODE -ne 0) { throw "alembic failed" }
  Step "Ensuring the admin account exists"
  & $venvPy -m app.create_admin
} finally { Pop-Location }

# ── NSSM services ─────────────────────────────────────────────────────────────
function Reinstall-Service($name, $exe, $argline, $appdir, $envPairs) {
  # nssm writes "Can't open service!" to stderr when the service doesn't exist;
  # under $ErrorActionPreference='Stop' PS 5.1 turns that into a TERMINATING error
  # that kills the installer before any service registers. Relax + use Get-Service.
  $ErrorActionPreference = 'SilentlyContinue'
  if (Get-Service -Name $name -ErrorAction SilentlyContinue) {
    & $nssm stop $name 2>&1 | Out-Null
    & $nssm remove $name confirm 2>&1 | Out-Null
    Start-Sleep -Milliseconds 500
  }
  & $nssm install $name $exe $argline 2>&1 | Out-Null
  & $nssm set $name AppDirectory $appdir 2>&1 | Out-Null
  & $nssm set $name Start SERVICE_AUTO_START 2>&1 | Out-Null
  & $nssm set $name AppStdout (Join-Path $InstallDir "$name.log") 2>&1 | Out-Null
  & $nssm set $name AppStderr (Join-Path $InstallDir "$name.log") 2>&1 | Out-Null
  if ($envPairs) { & $nssm set $name AppEnvironmentExtra $envPairs 2>&1 | Out-Null }
}

Step "Registering NSSM services"
Reinstall-Service "dahua-go2rtc" (Join-Path $InstallDir "bin\go2rtc.exe") `
  ("-config `"" + (Join-Path $InstallDir ".go2rtc\go2rtc.yaml") + "`"") $InstallDir $null

Reinstall-Service "dahua-backend" $venvPy `
  "-m uvicorn app.main:app --host 0.0.0.0 --port 8000" (Join-Path $InstallDir "backend") $null

$caddyEnv = @(
  "WWW_ROOT=$(Join-Path $InstallDir 'www')",
  "CADDY_HTTPS_PORT=$HttpsPort",
  "BACKEND_UPSTREAM=127.0.0.1:8000",
  "GO2RTC_UPSTREAM=127.0.0.1:1984",
  "XDG_DATA_HOME=$(Join-Path $InstallDir '.caddy\data')",
  "XDG_CONFIG_HOME=$(Join-Path $InstallDir '.caddy\config')"
)
Reinstall-Service "dahua-caddy" (Join-Path $InstallDir "bin\caddy.exe") `
  ("run --config `"" + (Join-Path $InstallDir "Caddyfile") + "`" --adapter caddyfile") $InstallDir $caddyEnv

Step "Starting services"
& $nssm start dahua-go2rtc  | Out-Null
& $nssm start dahua-backend | Out-Null
& $nssm start dahua-caddy   | Out-Null
Ok "services registered + started"

# ── wait for readiness (through Caddy TLS, self-signed) ──────────────────────
Step "Waiting for the backend to become ready..."
$ps7 = $PSVersionTable.PSVersion.Major -ge 6
if (-not $ps7) { [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true } }
$ready = $false
for ($i=0; $i -lt 60; $i++) {
  try {
    if ($ps7) { Invoke-WebRequest -Uri "https://127.0.0.1:$HttpsPort/readyz" -SkipCertificateCheck -TimeoutSec 4 -UseBasicParsing | Out-Null }
    else      { Invoke-WebRequest -Uri "https://127.0.0.1:$HttpsPort/readyz" -TimeoutSec 4 -UseBasicParsing | Out-Null }
    $ready = $true; break
  } catch { Start-Sleep -Seconds 2 }
}

Write-Host ""
if ($ready) {
  Ok "Kanagatly VMS is running"
  Write-Host "  -> Open the desktop app and connect to:  https://$($hostIpVal):$HttpsPort"
  Write-Host "  -> Sign in as 'admin' (password shown above / in $envFile); you'll be asked to change it."
  Write-Host "  -> Services: Get-Service dahua-go2rtc,dahua-backend,dahua-caddy   Logs: $InstallDir\*.log"
} else {
  Err "Backend did not report ready in time. Check the logs in $InstallDir\dahua-*.log"
  exit 1
}
