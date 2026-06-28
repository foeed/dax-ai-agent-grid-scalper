# How to Run

## Start Docker Signal Service

```powershell
cd D:\DAX
docker compose up -d --build signal-service
```

The service runs at:

```text
http://localhost:8766
```

## Check Status

```powershell
docker compose ps
```

## Set Gold LLM Autopilot

```powershell
$password = (Select-String -Path .env -Pattern '^SIGNAL_PASSWORD=' | Select-Object -First 1).Line -replace '^SIGNAL_PASSWORD=', ''
$body = @{
  symbol = 'XAUUSD'
  use_llm = $true
  auto_tune = $true
  settings_refresh_seconds = 60
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri 'http://localhost:8766/api/settings' `
  -Method Post `
  -Headers @{ 'X-Signal-Password' = $password } `
  -ContentType 'application/json' `
  -Body $body
```

## View Logs

```powershell
docker compose logs -f signal-service
```

## Stop Docker

```powershell
docker compose down
```

## Run One Bot Check

```powershell
cd D:\DAX
docker compose run --rm bot python -m trend_scalper --once
```

## Run Tests in Docker

```powershell
docker compose run --rm signal-service python -m unittest discover -s tests
```
