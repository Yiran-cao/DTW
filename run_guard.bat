@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "PY=%~dp0deji\Scripts\python.exe"
set "PYW=%~dp0deji\Scripts\pythonw.exe"
set "GUARD=%~dp0guard.py"
set "PIDFILE=%~dp0watcher.pid"

if /i "%~1"=="list"    goto LIST
if /i "%~1"=="stop"    goto STOP
if /i "%~1"=="status"  goto STATUS
if /i "%~1"=="console" goto CONSOLE
if /i "%~1"=="test"    goto ONCE
goto START

:START
if not exist "%PYW%" goto NOVENV
if not exist "%GUARD%" goto NOGUARD
echo [INFO] Starting guard in background (no window)...
start "" "%PYW%" "%GUARD%" --no-delay
ping -n 4 127.0.0.1 >nul
if exist "%~dp0guard.pid" (
    set /p GPID=<"%~dp0guard.pid"
    echo [OK]   Guard running, pid=!GPID!
) else (
    echo [WARN] guard.pid not found. Check logs\guard_YYYYMMDD.log
)
echo [INFO] It starts the watcher only while the detection app is running.
echo [INFO] Log: logs\guard_YYYYMMDD.log
goto END

:CONSOLE
if not exist "%PY%" goto NOVENV
echo [INFO] Running guard in this window. Press Ctrl+C to stop.
echo.
"%PY%" "%GUARD%" --no-delay
echo.
pause
goto END

:LIST
if not exist "%PY%" goto NOVENV
"%PY%" "%GUARD%" --list
pause
goto END

:ONCE
if not exist "%PY%" goto NOVENV
"%PY%" "%GUARD%" --once
pause
goto END

:STOP
if not exist "%PY%" goto NOVENV
"%PY%" "%GUARD%" --stop
goto END

:STATUS
echo ============================================================
echo  Scheduled task DTW_Guard
echo ============================================================
schtasks /query /tn "DTW_Guard" 2>nul
echo.
echo ============================================================
echo  Local state
echo ============================================================
if exist "%~dp0guard.pid" (
    set /p GPID=<"%~dp0guard.pid"
    echo guard.pid   : !GPID!
) else (
    echo guard.pid   : not present
)
if exist "%PIDFILE%" (
    set /p WPID=<"%PIDFILE%"
    echo watcher.pid : !WPID!
) else (
    echo watcher.pid : not present
)
if exist "d:\data\watcher_status.txt" (
    echo.
    type "d:\data\watcher_status.txt"
) else (
    echo.
    echo status file : not present
)
echo.
pause
goto END

:NOVENV
echo [ERROR] deji\Scripts\python.exe not found.
echo [FIX]   Run install.bat first.
pause
goto END

:NOGUARD
echo [ERROR] guard.py not found next to this script.
pause
goto END

:END
endlocal
exit /b 0
