@echo off
chcp 65001 >nul
echo ========================================
echo   YummyAnime Downloader
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found! Install Python 3.8+
    pause
    exit /b 1
)

ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo [WARNING] ffmpeg not found! Downloads may fail.
    echo.
)

echo Installing dependencies...
python -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies!
    pause
    exit /b 1
)

echo Installing Playwright browsers...
python -m playwright install chromium 2>nul

echo.
echo ========================================
echo   Server starting...
echo   Open: http://localhost:8000
echo   Ctrl+C to stop
echo ========================================
echo.

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
pause