# Kanagatly VMS Server — one-shot diagnostics collector.
# Run in an ELEVATED PowerShell (right-click PowerShell → Run as administrator):
#   powershell -ExecutionPolicy Bypass -File collect-diagnostics.ps1
# It writes  Desktop\kanagatly-diag.txt  and opens it — share that file.
$ErrorActionPreference = 'SilentlyContinue'
$out = Join-Path ([Environment]::GetFolderPath('Desktop')) 'kanagatly-diag.txt'
"=== Kanagatly VMS Server diagnostics  $(Get-Date)  (PS $($PSVersionTable.PSVersion)) ===" | Set-Content $out

"`n--- services (Windows SCM) ---" | Add-Content $out
$svcs = Get-CimInstance Win32_Service -Filter "Name like 'dahua-%'"
if ($svcs) { $svcs | Select-Object Name,State,StartMode,PathName | Format-List | Out-String | Add-Content $out }
else { "NO dahua-* services registered — postinstall never got to service registration." | Add-Content $out }

# locate install dir from the backend service path, else the default
$dir = 'C:\Program Files\Kanagatly VMS Server'
$bk  = $svcs | Where-Object Name -eq 'dahua-backend'
if ($bk -and $bk.PathName -match '([A-Za-z]:\\.*?)\\(python|backend|bin)\\') { $dir = $Matches[1] }
"`n--- install dir: $dir  (exists: $(Test-Path $dir)) ---" | Add-Content $out
Get-ChildItem $dir | Select-Object Name,Length,LastWriteTime | Out-String | Add-Content $out

"`n--- listening ports (8443 caddy / 8000 backend / 1984 go2rtc-api / 8556 webrtc) ---" | Add-Content $out
Get-NetTCPConnection -State Listen | Where-Object { 8443,8000,1984,8556 -contains $_.LocalPort } |
  Select-Object LocalAddress,LocalPort,OwningProcess | Out-String | Add-Content $out

"`n--- https readyz test (self-signed OK) ---" | Add-Content $out
if ($PSVersionTable.PSVersion.Major -lt 6) {
  [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }; $skip = @{}
} else { $skip = @{ SkipCertificateCheck = $true } }
try {
  $r = Invoke-WebRequest 'https://127.0.0.1:8443/readyz' @skip -TimeoutSec 5 -UseBasicParsing
  "readyz -> HTTP $($r.StatusCode)  body=$($r.Content)" | Add-Content $out
} catch { "readyz FAILED: $($_.Exception.Message)" | Add-Content $out }

"`n--- bundled python + backend import test ---" | Add-Content $out
$py = Join-Path $dir 'python\python.exe'
if (Test-Path $py) {
  Push-Location (Join-Path $dir 'backend')
  (& $py -c "import sys; print(sys.version); import app.main; print('import app.main OK')" 2>&1) | Out-String | Add-Content $out
  Pop-Location
} else { "bundled python MISSING at $py" | Add-Content $out }

"`n--- .env present (secrets masked) ---" | Add-Content $out
$envf = Join-Path $dir 'backend\.env'
if (Test-Path $envf) {
  (Get-Content $envf) -replace '(SECRET|PASSWORD|KEY)=.*', '$1=<masked>' | Add-Content $out
} else { ".env MISSING at $envf" | Add-Content $out }

foreach ($n in 'dahua-go2rtc','dahua-backend','dahua-caddy') {
  "`n--- log: $n.log (last 80 lines) ---" | Add-Content $out
  $lf = Join-Path $dir "$n.log"
  if (Test-Path $lf) { Get-Content $lf -Tail 80 | Add-Content $out } else { "(no log file — service likely never started)" | Add-Content $out }
}
$it = Join-Path $dir 'install-log.txt'
if (Test-Path $it) { "`n--- install-log.txt (postinstall transcript, last 120) ---" | Add-Content $out; Get-Content $it -Tail 120 | Add-Content $out }

"`n=== end ===" | Add-Content $out
Write-Host "Wrote $out" -ForegroundColor Green
notepad $out
