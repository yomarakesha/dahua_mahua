<#
.SYNOPSIS
  Unattended NVR / network packet-loss measurement. RUN WITH THE VPN OFF.

.DESCRIPTION
  Launches an isolated MediaMTX that pulls 1..N main streams from the NVR and
  records bitrate, "RTP packets lost", and Ethernet throughput per phase, to
  tell apart "NVR output limit" vs "100 Mbps link saturation".
  Also runs link diagnostics (sec 3.5) and, if a camera IP is given, the
  DECISIVE sec 3.1 test (same stream via NVR vs straight from the camera).
  Takes ~3-5 minutes. Writes the report to .\netcheck-result.md

.NOTES
  env vars (all optional):

  Point at a different NVR (else uses the one seeded in dss.db, 192.168.20.58):
    $env:NETCHECK_NVR_IP   = "192.168.20.58"
    $env:NETCHECK_NVR_USER = "admin"        # only if NOT in dss.db
    $env:NETCHECK_NVR_PASS = "secret"       # only if NOT in dss.db

  DECISIVE sec 3.1 camera-vs-NVR test (needs the camera reachable - set a
  working secondary IP in the camera subnet first, VPN off):
    $env:NETCHECK_CAM_IP   = "192.168.20.122"   # the camera's own IP
    $env:NETCHECK_CAM_CH   = "1"                 # that camera's CHANNEL on the NVR
    $env:NETCHECK_CAM_USER = "admin"             # camera creds (default: reuse NVR's)
    $env:NETCHECK_CAM_PASS = "secret"

  Contention test (sec 3.6) - label the run so peak vs night are comparable:
    $env:NETCHECK_LABEL = "peak"     # then later: "night"

.EXAMPLE
  $env:NETCHECK_CAM_IP="192.168.20.122"; $env:NETCHECK_CAM_CH="1"; $env:NETCHECK_LABEL="peak"
  .\run-netcheck.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py   = Join-Path $root "backend\.venv\Scripts\python.exe"

if (-not (Test-Path $py)) { throw "venv python not found at $py - run start.ps1 once first." }

Write-Host "Running NVR/network measurement (~3-5 min). Make sure the VPN is OFF." -ForegroundColor Yellow
if ($env:NETCHECK_CAM_IP) {
    Write-Host "  sec 3.1 decisive test ENABLED - camera $($env:NETCHECK_CAM_IP) (NVR ch $($env:NETCHECK_CAM_CH))" -ForegroundColor Cyan
} else {
    Write-Host "  sec 3.1 decisive test SKIPPED - set `$env:NETCHECK_CAM_IP to enable." -ForegroundColor DarkGray
}
if ($env:NETCHECK_LABEL) { Write-Host "  run label: $($env:NETCHECK_LABEL)" -ForegroundColor Cyan }

Push-Location (Join-Path $root "backend")
try {
    & $py netcheck.py
} finally {
    Pop-Location
}
Write-Host ""
Write-Host "Done. Report: $root\netcheck-result.md" -ForegroundColor Green
