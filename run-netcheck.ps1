<#
.SYNOPSIS
  Unattended NVR / network packet-loss measurement. RUN WITH THE VPN OFF.

.DESCRIPTION
  Launches an isolated MediaMTX that pulls 1 -> N main streams from the NVR and
  records bitrate, "RTP packets lost", and Ethernet throughput per phase, to
  tell apart "NVR output limit" vs "100 Mbps link saturation".
  Takes ~3-4 minutes. Writes the report to .\netcheck-result.md
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py   = Join-Path $root "backend\.venv\Scripts\python.exe"

if (-not (Test-Path $py)) { throw "venv python not found at $py — run start.ps1 once first." }

Write-Host "Running NVR/network measurement (~3-4 min). Make sure the VPN is OFF." -ForegroundColor Yellow
Push-Location (Join-Path $root "backend")
try {
    & $py netcheck.py
} finally {
    Pop-Location
}
Write-Host ""
Write-Host "Done. Report: $root\netcheck-result.md" -ForegroundColor Green
