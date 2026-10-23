<#
Starts the live account (variant LC, real orders) and its monitor. Run by the user only (ADR-033, PLAN_LIVE.md §4).
Refuses to start unless .env has WALLEX_LIVE_APPROVAL=I-APPROVE-LIVE-ORDERS and config live.dry_run is false.
Usage: powershell -ExecutionPolicy Bypass -File scripts\start_live.ps1
Stop:  powershell -ExecutionPolicy Bypass -File scripts\start_live.ps1 -Stop
#>
param([string]$Session = "live-1", [switch]$Stop)
Set-Location (Split-Path $PSScriptRoot -Parent)
if ($Stop) {
    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "$Session" -and $_.CommandLine -match 'run_shadow_forever|wallex_agent shadow|live_status' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false -ErrorAction SilentlyContinue }
    Add-Content logs\shadow_watchdog.log "$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmss')) user stopped $Session"
    Write-Host "Live account stopped. Cancel any open order in the Wallex panel if one is left."
    return
}
$approved = Select-String -Path .env -Pattern '^WALLEX_LIVE_APPROVAL=I-APPROVE-LIVE-ORDERS\s*$' -Quiet
$live = Select-String -Path config\default.yaml -Pattern '^\s+dry_run:\s*false' -Quiet
if (-not $approved) { Write-Host "Not started: add WALLEX_LIVE_APPROVAL=I-APPROVE-LIVE-ORDERS to .env first."; return }
if (-not $live) { Write-Host "Not started: config live.dry_run is not false."; return }
Start-Process powershell.exe -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',"$PSScriptRoot\run_shadow_forever.ps1",'-Session',$Session,'-Variants','LC' -WindowStyle Hidden
Start-Process (Resolve-Path .\.venv\Scripts\python.exe) -ArgumentList '-I','scripts\live_status.py','--session',$Session,'--watch' -WindowStyle Hidden
Start-Sleep 60
$page = Join-Path (Get-Location) "reports\shadow\$Session\status.html"
if (Test-Path $page) { Start-Process $page; Write-Host "Live account started. Monitor: $page" } else { Write-Host "Started; the monitor page appears within a minute: $page" }
