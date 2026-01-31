@echo off
:: Lily Remote Agent - Start Script
:: Runs with admin rights for full system control

:: Check for admin rights
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Requesting Administrator privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"

echo.
echo ========================================================
echo        Lily Remote Agent (Administrator Mode)
echo ========================================================
echo.

:: Check if venv exists
if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found!
    echo.
    echo Please run install.bat first.
    echo.
    pause
    exit /b 1
)

:: Activate and run
call venv\Scripts\activate.bat

:: Check if dependencies are installed
python -c "import fastapi, uvicorn, mss, cryptography" 2>nul
if %errorLevel% neq 0 (
    echo [ERROR] Dependencies not installed properly!
    echo.
    echo Please run install.bat again.
    echo.
    pause
    exit /b 1
)

echo Starting Lily Remote Agent...
echo.
echo Server will be available at:
echo   https://[YOUR-IP]:8765
echo.
echo Press Ctrl+C to stop.
echo.

python run_server.py
