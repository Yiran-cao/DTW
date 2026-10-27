@echo off
setlocal enabledelayedexpansion
title DTW Auto Wave Selection - Setup

:: ============================================================
:: DTW Auto Wave Selection - install.bat
:: One-click deployment (offline, no network needed)
:: PREREQUISITE: Install VC++ Redistributable manually first!
::   Download: https://aka.ms/vs/17/release/vc_redist.x86.exe
::   Or use the one in offline_packages\VC_redist.x86.exe
:: ============================================================

:: Step 0: Admin check
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Requesting admin privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs -Wait"
    exit /b
)
echo [INFO] Admin OK.

cd /d "%~dp0"
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
echo [INFO] Install dir: %SCRIPT_DIR%

:: ============================================================
:: Step 1: Check / Create venv
:: ============================================================
echo.
echo ============================================================
echo  [Step 1/4] Check Python virtual env
echo ============================================================

if exist "deji\Scripts\python.exe" (
    echo [SKIP] deji already exists.
) else (
    echo [RUN]  Creating deji ...
    echo       Path: %SCRIPT_DIR%\deji
    python -m venv deji
    if !errorlevel! neq 0 (
        echo.
        echo [ERROR] Failed to create venv!
        echo [FIX] Install Python 3.6 32-bit, check "Add to PATH".
        echo.
        pause
        exit /b 1
    )
    echo [OK]   Venv created.
)

:: ============================================================
:: Step 2: Install dependencies
:: ============================================================
echo.
echo ============================================================
echo  [Step 2/4] Install Python deps
echo ============================================================

set "PIP=%SCRIPT_DIR%\deji\Scripts\pip.exe"

if exist "offline_packages\" (
    echo [INFO] Offline packages found, installing...
    "%PIP%" install -r requirements.txt --no-index --find-links=offline_packages
    if !errorlevel! neq 0 (
        echo.
        echo [ERROR] Offline install failed!
        echo [HINT] Check offline_packages\ has correct .whl files.
        echo.
        choice /c yn /n /m "Try online install? [Y/N] "
        if !errorlevel! equ 1 (
            echo [INFO] Trying online...
            "%PIP%" install -r requirements.txt
            if !errorlevel! neq 0 (
                echo [ERROR] Online also failed.
                pause
                exit /b 1
            )
        ) else (
            echo [CANCEL] Aborted.
            pause
            exit /b 1
        )
    ) else (
        echo [OK]   Offline install done.
    )
) else (
    echo [WARN] offline_packages\ not found.
    echo [INFO] Trying online install...
    "%PIP%" install -r requirements.txt
    if !errorlevel! neq 0 (
        echo.
        echo [ERROR] Install failed! No network?
        pause
        exit /b 1
    )
    echo [OK]   Online install done.
)

:: Quick numpy check
echo [INFO] Verifying numpy...
"%SCRIPT_DIR%\deji\Scripts\python.exe" -c "import numpy" >nul 2>&1
if !errorlevel! neq 0 (
    echo [ERROR] numpy import failed! (exit=!errorlevel!)
    echo.
    echo [FIX] VC++ Runtime may not be installed.
    echo        Run offline_packages\VC_redist.x86.exe manually, then re-run install.
    echo.
    echo.
    pause
    exit /b 1
)
echo [OK]   numpy verified.

:: ============================================================
:: Step 3: Register auto-start task
:: ============================================================
echo.
echo ============================================================
echo  [Step 3/4] Register scheduled task
echo ============================================================

schtasks /query /tn "DTW_AutoRun" >nul 2>&1
if !errorlevel! equ 0 (
    echo [INFO] Removing old DTW_AutoRun task...
    schtasks /delete /tn "DTW_AutoRun" /f >nul 2>&1
    echo [OK]   Old task removed.
)

set "RUN_BAT=%SCRIPT_DIR%\run.bat"
echo [INFO] Registering new task...

schtasks /create /tn "DTW_AutoRun" /tr "%RUN_BAT%" /sc onstart /rl highest /f
set TASK_RESULT=!errorlevel!

if !TASK_RESULT! neq 0 (
    echo [WARN] onstart failed, trying onlogon...
    schtasks /create /tn "DTW_AutoRun" /tr "%RUN_BAT%" /sc onlogon /rl highest /f
    set TASK_RESULT=!errorlevel!
    if !TASK_RESULT! neq 0 (
        echo [ERROR] Task registration failed (code: !TASK_RESULT!)
        echo [MANUAL] Run taskschd.msc:
        echo          Name: DTW_AutoRun
        echo          Trigger: At startup
        echo          Action: %RUN_BAT%
        pause
        exit /b 1
    )
    echo [OK]   Task registered (onlogon trigger).
)
echo [OK]   Task DTW_AutoRun registered.

:: ============================================================
:: Step 4: Create log dir
:: ============================================================
echo.
echo ============================================================
echo  [Step 4/4] Create log directory
echo ============================================================

if not exist "logs" (
    mkdir logs
    echo [OK]   Log dir created: %SCRIPT_DIR%\logs
) else (
    echo [SKIP] Log dir exists.
)

:: ============================================================
:: Summary
:: ============================================================
echo.
echo ============================================================
echo.
echo    ***** DTW Setup Complete *****
echo.
echo ============================================================
echo    Install dir : %SCRIPT_DIR%
echo    Virtual env : %SCRIPT_DIR%\deji
echo    Dependencies: numpy (verified OK)
echo    Task name   : DTW_AutoRun
echo    Trigger     : At system startup
echo    Run script  : %RUN_BAT%
echo    Log dir     : %SCRIPT_DIR%\logs
echo ============================================================
echo.
echo [NEXT] To test:
echo         1. Double-click run.bat
echo         2. Reboot to verify auto-start
echo         3. Check logs\YYYY-MM-DD.log
echo.
echo ============================================================
pause
