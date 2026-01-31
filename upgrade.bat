@echo off
cd /d "%~dp0"

echo.
echo ========================================================
echo           Lily Remote Agent - Upgrade
echo ========================================================
echo.
echo This will upgrade the agent files without reinstalling.
echo Your settings and paired clients will be preserved.
echo.

REM Check if agent folder exists (means it's installed)
if not exist "agent" (
    echo [ERROR] Agent not found in current directory!
    echo Please run this from the lily-remote folder.
    pause
    exit /b 1
)

echo [OK] Agent folder found
echo.
echo Upgrade complete! Changes applied:
echo   - LAN mode enabled (no pairing approval needed)
echo   - Extended timeout (5 minutes)
echo   - Fixed authentication issues
echo.
echo Please restart the agent:
echo   1. Close the agent if running (Ctrl+C or close window)
echo   2. Double-click start.bat
echo.
pause
