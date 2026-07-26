@echo off
title Steam CS2 Inventory Monitor v2.0

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Not installed. Run install.bat first.
    pause
    exit /b 1
)
if not exist ".env" (
    echo [ERROR] .env not found. Run install.bat first.
    pause
    exit /b 1
)

echo.
echo   Steam CS2 Inventory Monitor v2.0
echo   Browser will open. Ctrl+C to stop.
echo.
call venv\Scripts\python run_web.py
pause
