<#
  run-dss.ps1 — запуск DSS (MediaMTX + backend + frontend) с анти-фриз перекодированием.
  Останавливает старые процессы и поднимает заново в свёрнутых окнах.
  Запуск:  .\run-dss.ps1      Остановка:  .\run-dss.ps1 -Stop
#>
param([switch]$Stop)

$ErrorActionPreference = "SilentlyContinue"
$root = $PSScriptRoot
$venv = Join-Path $root "backend\.venv\Scripts\python.exe"

Write-Host "Останавливаю старые сервисы DSS..." -ForegroundColor Yellow
Get-Process mediamtx, ffmpeg -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match 'uvicorn|http\.server' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

if ($Stop) { Write-Host "DSS остановлен." -ForegroundColor Green; return }

Write-Host "MediaMTX (релей + перекодирование)..." -ForegroundColor Cyan
Start-Process -FilePath (Join-Path $root "mediamtx.exe") -ArgumentList (Join-Path $root "mediamtx.yml") -WorkingDirectory $root -WindowStyle Minimized
Start-Sleep -Seconds 3

Write-Host "Backend (:8000)..." -ForegroundColor Cyan
Start-Process -FilePath $venv -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8000" -WorkingDirectory (Join-Path $root "backend") -WindowStyle Minimized
Start-Sleep -Seconds 4

Write-Host "Frontend (:8081)..." -ForegroundColor Cyan
Start-Process -FilePath $venv -ArgumentList "-m","http.server","8081","--bind","0.0.0.0" -WorkingDirectory (Join-Path $root "web") -WindowStyle Minimized
Start-Sleep -Seconds 3

Write-Host ""
Write-Host "DSS поднят:" -ForegroundColor Green
Write-Host "  Локально:  http://localhost:8081"
Write-Host "  По сети:   http://10.10.1.221:8081"
Write-Host "  Логин:     admin / admin123"
Write-Host ""
Write-Host "Остановить всё:  .\run-dss.ps1 -Stop" -ForegroundColor Yellow
