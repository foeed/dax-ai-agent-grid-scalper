@echo off
echo ========================================
echo DAX V2 Backend - Docker Startup
echo ========================================
echo.

REM Check if Docker is running
docker info >nul 2>&1
if errorlevel 1 (
    echo Docker is not running! Please start Docker Desktop.
    pause
    exit /b 1
)

echo Building and starting container...
echo.

docker-compose down
docker-compose build --no-cache
docker-compose up -d

echo.
echo ========================================
echo Backend starting at: http://localhost:8000
echo API docs: http://localhost:8000/docs
echo ========================================
echo.
echo To view logs: docker-compose logs -f
echo To stop: docker-compose down
echo.
pause
