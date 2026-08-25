# MT5 Backtesting Script
# Exports data from a running MT5 terminal and runs the backtest engine.
#
# Usage:
#   .\mt5_backtest.ps1                    # Default: XAUUSD M5, 30 days
#   .\mt5_backtest.ps1 -Symbol EURUSD -TF M15 -Days 60 -Equity 100
#
# Prerequisites:
#   - MT5 terminal running and logged into ANY account (demo or live)
#   - Python MetaTrader5 package: pip install MetaTrader5==5.0.45

param(
    [string]$Symbol = "XAUUSD",
    [string]$TF = "M5",
    [string]$TrendTF = "M15",
    [int]$Days = 30,
    [float]$Equity = 100,
    [string]$MT5Path = "",
    [switch]$SaveCsv,
    [switch]$Compare
)

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  MT5 LIVE BACKTEST ENGINE" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Symbol:     $Symbol"
Write-Host "  Timeframe:  $TF (trend: $TrendTF)"
Write-Host "  Period:     $Days days"
Write-Host "  Equity:     `$$Equity"
Write-Host "============================================================"
Write-Host ""

# Check prerequisites
Write-Host "[1/3] Checking prerequisites..." -ForegroundColor Yellow

$pythonCheck = python -c "import MetaTrader5; print('OK')" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  MetaTrader5 Python package not found. Installing..." -ForegroundColor Red
    pip install MetaTrader5==5.0.45
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: Failed to install MetaTrader5. Install manually:" -ForegroundColor Red
        Write-Host "    pip install MetaTrader5==5.0.45" -ForegroundColor Red
        exit 1
    }
}
Write-Host "  MetaTrader5 package: OK" -ForegroundColor Green

# Check MT5 is running
$mt5Running = Get-Process -Name "terminal64" -ErrorAction SilentlyContinue
if (-not $mt5Running) {
    $mt5Running = Get-Process -Name "metatrader" -ErrorAction SilentlyContinue
}
if (-not $mt5Running) {
    Write-Host "  WARNING: MT5 terminal not detected. Make sure it's running." -ForegroundColor Yellow
    Write-Host "  Continue anyway? (y/n): " -NoNewline
    $response = Read-Host
    if ($response -ne "y") { exit 0 }
} else {
    Write-Host "  MT5 terminal: Running" -ForegroundColor Green
}

# Build command
Write-Host ""
Write-Host "[2/3] Fetching data from MT5 and running backtest..." -ForegroundColor Yellow
Write-Host ""

$cmdArgs = @(
    "-m", "trend_scalper.mt5_backtest",
    "--symbol", $Symbol,
    "--timeframe", $TF,
    "--trend-tf", $TrendTF,
    "--days", $Days,
    "--equity", $Equity
)

if ($MT5Path) { $cmdArgs += "--mt5-path"; $cmdArgs += $MT5Path }
if ($SaveCsv) { $cmdArgs += "--save-csv" }

if ($Compare) {
    Write-Host "[2/3] Running comparison mode..." -ForegroundColor Yellow
    $cmdArgs = @(
        "-m", "trend_scalper.backtest_runner",
        "--compare",
        "--equity", $Equity,
        "--days", $Days
    )
}

$env:PYTHONPATH = "$PSScriptRoot\src"
$result = python @cmdArgs 2>&1
$exitCode = $LASTEXITCODE

Write-Host $result

Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "[3/3] Backtest complete!" -ForegroundColor Green
} else {
    Write-Host "[3/3] Backtest completed with warnings." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  NEXT STEPS:" -ForegroundColor Cyan
Write-Host "  1. Review the trade log above" -ForegroundColor White
Write-Host "  2. If PF < 1.0, tune parameters or switch symbol/TF" -ForegroundColor White
Write-Host "  3. Paper trade on demo for 2 weeks before going live" -ForegroundColor White
Write-Host "  4. Run with --auto-account for automatic sizing" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Cyan
