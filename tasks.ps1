<#
Windows task runner (equivalent of the Makefile). Usage:
  .\tasks.ps1 setup          # create .venv and install locked dependencies
  .\tasks.ps1 setup-laya     # create .venv-laya with CPU torch + laya[serve]
  .\tasks.ps1 laya-serve     # run the local Laya judge on 127.0.0.1:8000
  .\tasks.ps1 verify-api     # Phase 0 read-only API checks
  .\tasks.ps1 fetch-data     # update candle cache + hashed snapshot
  .\tasks.ps1 test           # pytest
  .\tasks.ps1 lint           # ruff
  .\tasks.ps1 backtest [-Config config\default.yaml]   # P4
  .\tasks.ps1 shadow   [-Config config\default.yaml]   # P3
#>
param(
    [Parameter(Position = 0, Mandatory = $true)][string]$Task,
    [string]$Config = "config\default.yaml"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Py = ".\.venv\Scripts\python.exe"
$PyLaya = ".\.venv-laya\Scripts\python.exe"

switch ($Task) {
    "setup" {
        py -3.12 -m venv .venv
        & $Py -m pip install --upgrade pip
        & $Py -m pip install -r requirements.lock
        & $Py -m pip install --no-deps -e .
    }
    "setup-laya" {
        py -3.12 -m venv .venv-laya
        & $PyLaya -m pip install --upgrade pip
        & $PyLaya -m pip install -r requirements-laya.lock --extra-index-url https://download.pytorch.org/whl/cpu
    }
    "laya-serve" {
        $env:LAYA_HOST = "127.0.0.1"; $env:LAYA_PORT = "8000"
        $env:LAYA_MODELS = "english"; $env:LAYA_DEFAULT_MODEL = "english"; $env:LAYA_DEVICE = "cpu"
        if (-not $env:LAYA_THREADS) { $env:LAYA_THREADS = "8" }
        & .\.venv-laya\Scripts\laya-serve.exe
    }
    "verify-api" { & $Py -m wallex_agent verify-api; & $Py -m wallex_agent verify-llm }
    "verify-laya" { & $Py -m wallex_agent verify-laya }
    "fetch-data" { & $Py -m wallex_agent fetch-data --config $Config }
    "test" { & $Py -m pytest }
    "lint" { & $Py -m ruff check src tests }
    "backtest" { & $Py -m wallex_agent backtest --config $Config }
    "shadow" { & $Py -m wallex_agent shadow --config $Config }
    "report" { & $Py -m wallex_agent report }
    "dashboard" { & $Py -m wallex_agent dashboard }
    default { throw "unknown task '$Task' (see the header of tasks.ps1)" }
}
exit $LASTEXITCODE
