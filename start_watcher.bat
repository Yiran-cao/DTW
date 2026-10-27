@echo off
setlocal
:: ============================================================
:: [DEPRECATED] Legacy manual-start helper. Kept for debugging only.
::
:: The scheduled task no longer calls this file. Normal operation:
::   run_guard.bat           start guard in background (follows the app)
::   run_guard.bat console   run guard in foreground to watch the log
::   run_guard.bat stop      stop guard and watcher
::   run_guard.bat status    show task / pid / status file
::
:: This script starts watcher.py directly and bypasses guard.py, so
:: while the guard is running it treats this watcher as a stray one.
:: Use it only when the guard is stopped.
:: ============================================================
cd /d "%~dp0"

if not exist "deji\Scripts\python.exe" (
    echo [ERROR] deji\Scripts\python.exe not found. Run install.bat first.
    pause
    exit /b 1
)

call deji\Scripts\python.exe launcher.py
pause
