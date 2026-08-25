param(
    [switch]$SkipDocker
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$env:PYTHONPATH = Join-Path $Root "src"

python -m compileall src tests
python -m unittest discover -s tests
python -m trend_scalper --check --env-file .env.example
python -m trend_scalper --once --env-file .env.example

if (-not $SkipDocker) {
    docker compose config --quiet
    docker compose run --rm signal-service python -m unittest discover -s tests
    docker compose run --rm bot python -m unittest discover -s tests
    docker compose run --rm bot python -m trend_scalper --check
    docker compose run --rm bot python -m trend_scalper --once
}
