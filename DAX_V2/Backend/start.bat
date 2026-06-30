@echo off
echo ========================================
echo DAX V2 AI Trading Backend
echo ========================================
echo.

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found! Please install Python 3.11+
    pause
    exit /b 1
)

REM Check if virtual environment exists
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Upgrade pip first
echo Upgrading pip...
python -m pip install --upgrade pip

REM Install dependencies
echo Installing dependencies...
pip install -r requirements.txt

REM Create .env file if not exists
if not exist ".env" (
    echo Creating .env file...
    echo DEEPSEEK_API_KEY=YOUR_KEY_HERE> .env
    echo NEWS_API_KEY=YOUR_KEY_HERE>> .env
)

echo.
echo Starting server...
echo Backend will be available at: http://localhost:8000
echo API docs: http://localhost:8000/docs
echo.
echo Press Ctrl+C to stop
echo.

python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
