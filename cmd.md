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

