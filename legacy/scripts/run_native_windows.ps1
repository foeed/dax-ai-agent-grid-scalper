param(
    [switch]$Once,
    [switch]$Check,
    [switch]$Bridge
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".venv")) {
    py -3.11 -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-mt5.txt

$env:PYTHONPATH = Join-Path $Root "src"

if ($Bridge) {
    $argsList = @("-m", "trend_scalper.mt5_bridge")
} else {
    $argsList = @("-m", "trend_scalper")
    if ($Once) { $argsList += "--once" }
    if ($Check) { $argsList += "--check" }
}

.\.venv\Scripts\python.exe @argsList
