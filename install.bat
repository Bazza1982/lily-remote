@echo off
:: Lily Remote Agent - Installation Script
:: Requests admin rights for full functionality

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
echo        Lily Remote Agent - Installation
echo        (Running as Administrator)
echo ========================================================
echo.

:: Check Python
echo [1/4] Checking Python...
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Python not found!
    echo.
    echo Please install Python 3.10+ from:
    echo   https://www.python.org/downloads/
    echo.
    echo IMPORTANT: Check "Add Python to PATH" during install!
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo        Found Python %PYVER%

:: Check Python version >= 3.10
python -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" 2>nul
if %errorLevel% neq 0 (
    echo [ERROR] Python 3.10 or higher is required!
    echo        You have Python %PYVER%
    pause
    exit /b 1
)
echo        [OK] Version check passed

:: Check pip
echo.
echo [2/4] Checking pip...
python -m pip --version >nul 2>&1
if %errorLevel% neq 0 (
    echo        Installing pip...
    python -m ensurepip --default-pip
)
echo        [OK] pip is available

:: Create virtual environment
echo.
echo [3/4] Creating virtual environment...
if exist "venv" (
    echo        venv already exists, skipping...
) else (
    python -m venv venv
    if %errorLevel% neq 0 (
        echo [ERROR] Failed to create virtual environment!
        pause
        exit /b 1
    )
)
echo        [OK] Virtual environment ready

:: Install dependencies
echo.
echo [4/4] Installing dependencies...
call venv\Scripts\activate.bat
pip install --upgrade pip >nul 2>&1
pip install -r requirements.txt
if %errorLevel% neq 0 (
    echo.
    echo [ERROR] Failed to install dependencies!
    echo.
    echo If you see compiler errors, you may need:
    echo   Visual C++ Build Tools from:
    echo   https://visualstudio.microsoft.com/visual-cpp-build-tools/
    echo.
    pause
    exit /b 1
)

echo.
echo ========================================================
echo        Installation Complete!
echo ========================================================
echo.
echo To start the agent:
echo   Double-click start.bat
echo.
echo Or run manually:
echo   venv\Scripts\activate
echo   python run_server.py
echo.
echo NOTE: Agent installed with Administrator privileges
echo       for full system control capability.
echo.
pause
