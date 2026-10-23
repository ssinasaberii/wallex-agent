<#
Keeps the shadow test running until the challenge end. If the Python process exits (crash, network
error), it is restarted after 15 s with the same session id, so state (positions, cash) resumes from
reports/shadow/<session>/state.sqlite. Also restarts laya-serve if it is not answering.
Usage: powershell -ExecutionPolicy Bypass -File scripts\run_shadow_forever.ps1 -Session challenge
#>
param([string]$Session = "challenge", [string]$EndUtc = "2026-10-26T20:30:00Z")
Set-Location (Split-Path $PSScriptRoot -Parent)
New-Item -ItemType Directory -Force logs | Out-Null
$end = [DateTime]::Parse($EndUtc).ToUniversalTime()

function Ensure-Laya {
    try { if ((Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200) { return } } catch {}
    $env:LAYA_HOST = "127.0.0.1"; $env:LAYA_PORT = "8000"; $env:LAYA_MODELS = "english"
    $env:LAYA_DEFAULT_MODEL = "english"; $env:LAYA_DEVICE = "cpu"; $env:LAYA_THREADS = "8"; $env:LAYA_LOG_LEVEL = "warning"
    Start-Process -FilePath (Resolve-Path .\.venv-laya\Scripts\laya-serve.exe) -WindowStyle Hidden `
        -RedirectStandardOutput logs\laya.out.log -RedirectStandardError logs\laya.err.log | Out-Null
    Start-Sleep 30
}

while ([DateTime]::UtcNow -lt $end.AddMinutes(5)) {
    Ensure-Laya
    $stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmss")
    Add-Content logs\shadow_watchdog.log "$stamp start session=$Session"
    & .\.venv\Scripts\python.exe -m wallex_agent shadow --session $Session *>> "logs\shadow_$Session.log"
    Add-Content logs\shadow_watchdog.log "$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmss')) exit code=$LASTEXITCODE"
    if ([DateTime]::UtcNow -ge $end) { break }
    Start-Sleep 15
}
