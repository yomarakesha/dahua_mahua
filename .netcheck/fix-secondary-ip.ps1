# Elevated helper: replace dead Duplicate 192.168.20.50 with a free secondary IP
# on the Ethernet adapter, then verify NVR reachability. ASCII only (PS 5.1).
$ErrorActionPreference = 'Continue'
$log = 'C:\Users\yomarakesha\Desktop\projects\dss\.netcheck\fix-result.txt'
function W($m) { $m | Out-File -FilePath $log -Append -Encoding ascii; Write-Host $m }
('=== secondary IP fix ' + (Get-Date -Format s) + ' ===') | Out-File -FilePath $log -Encoding ascii

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
W ("admin=" + $admin)
if (-not $admin) { W 'ABORT: not elevated'; W 'DONE'; exit 1 }

$ad = Get-NetAdapter -Name 'Ethernet' -EA SilentlyContinue
if (-not $ad) { $ad = Get-NetAdapter -Physical | Where-Object { $_.InterfaceDescription -match 'I219|Ethernet Connection' } | Select-Object -First 1 }
if (-not $ad) { W 'ABORT: Ethernet adapter not found'; W 'DONE'; exit 1 }
$ifx = $ad.ifIndex
W ("adapter: ifIndex=" + $ifx + " " + $ad.InterfaceDescription + " " + $ad.Status + " " + $ad.LinkSpeed)

# remove stale 192.168.20.* (the Duplicate .50 blocks nothing but is dead weight)
Get-NetIPAddress -InterfaceIndex $ifx -AddressFamily IPv4 -EA SilentlyContinue |
  Where-Object { $_.IPAddress -like '192.168.20.*' } | ForEach-Object {
    W ("removing stale " + $_.IPAddress + " state=" + $_.AddressState)
    Remove-NetIPAddress -InterfaceIndex $ifx -IPAddress $_.IPAddress -Confirm:$false -EA SilentlyContinue
}

$set = $null
foreach ($n in ((240..254) + (230..239))) {
    $ip = '192.168.20.' + $n
    try {
        New-NetIPAddress -InterfaceIndex $ifx -IPAddress $ip -PrefixLength 24 -EA Stop | Out-Null
    } catch { W ('add ' + $ip + ' FAILED: ' + $_.Exception.Message); continue }
    $state = 'Tentative'; $tries = 0
    while ($state -eq 'Tentative' -and $tries -lt 20) {
        Start-Sleep -Milliseconds 250; $tries++
        $a = Get-NetIPAddress -InterfaceIndex $ifx -IPAddress $ip -EA SilentlyContinue
        if ($a) { $state = [string]$a.AddressState } else { $state = 'Gone' }
    }
    W ($ip + ' -> ' + $state)
    if ($state -eq 'Preferred') { $set = $ip; break }
    Remove-NetIPAddress -InterfaceIndex $ifx -IPAddress $ip -Confirm:$false -EA SilentlyContinue
}

if ($set) {
    W ('OK secondary IP: ' + $set)
    $p = @(Test-Connection 192.168.20.58 -Count 4 -EA SilentlyContinue)
    W ('ping 192.168.20.58 replies: ' + $p.Count + '/4')
    $r = @(Find-NetRoute -RemoteIPAddress 192.168.20.58 -EA SilentlyContinue)
    if ($r.Count -gt 0) { W ('egress now: ifIndex=' + $r[0].InterfaceIndex + ' src=' + $r[0].IPAddress) }
} else {
    W 'FAILED: no free IP found in 192.168.20.240-254 / 230-239'
}
W 'DONE'
