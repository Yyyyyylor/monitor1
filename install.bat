@echo off
title Steam CS2 Monitor - Install
echo.
echo ============================================
echo   Steam CS2 Inventory Monitor v2.1 - Install
echo ============================================
echo.

python --version >nul 2>&1
if %errorlevel% equ 0 (
    echo [1/5] Python found
    goto :py_ok
)

echo [1/5] Python not found, installing...
if not exist "python-3.12.9-amd64.exe" (
    echo [ERROR] python-3.12.9-amd64.exe not found in this folder
    pause
    exit /b 1
)
echo       Please wait 1-3 minutes...
start /wait "" python-3.12.9-amd64.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
set "PATH="
for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "PATH=%%B"
for /f "tokens=2*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "PATH=%%PATH%;%%B"
for /d %%P in ("%LOCALAPPDATA%\Programs\Python\Python31*") do if exist "%%P\python.exe" set "PATH=%%P;%%P\Scripts;%PATH%"
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python install failed. Please restart PC and try again.
    pause
    exit /b 1
)
echo [OK] Python installed

:py_ok
if not exist "venv\Scripts\python.exe" (
    echo [2/5] Creating virtual environment...
    python -m venv venv
    echo [OK] Done
) else (
    echo [2/5] venv exists, skip
)

echo [3/5] Installing dependencies, please wait...
call venv\Scripts\pip install --upgrade pip --quiet >nul 2>&1
call venv\Scripts\python -c "import aiohttp" 2>nul
if %errorlevel% equ 0 (
    echo [OK] Dependencies already installed, skip
) else (
    call venv\Scripts\pip install -e . --quiet 2>nul
    if %errorlevel% neq 0 (
        echo       Trying China mirror...
        call venv\Scripts\pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet 2>nul
        if %errorlevel% neq 0 (
            echo [ERROR] Install failed. Check your network.
            pause
            exit /b 1
        )
    )
    echo [OK] Dependencies installed
)

if not exist ".env" (
    echo [4/5] Creating .env...
    copy .env.example .env >nul
    echo [OK] .env created
) else (
    echo [4/5] .env exists, skip
)

if not exist "data" mkdir data
if not exist "data\logs" mkdir data\logs
echo [5/5] Initializing database...
call venv\Scripts\python -c "import asyncio; from src.db.database import init_db; asyncio.run(init_db())"
echo [OK] Database ready

echo.
echo ============================================
echo   Installation complete!
echo ============================================
echo.
echo   Next: double-click "start.bat" to run.
echo.
pause
