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
:: Step 3: Register logon guard task
::   OLD: DTW_AutoRun used a BOOT trigger that started numpy + folder
::        polling during boot. On old machines with slow disks this
::        caused lag and BSOD. That task is removed below.
::   NEW: DTW_Guard starts a tiny guard at logon. The guard starts the
::        watcher only while the detection software is running, and
::        stops it when the software exits.
:: ============================================================
echo.
echo ============================================================
echo  [Step 3/4] Register logon guard task
echo ============================================================

set "PYTHONW=%SCRIPT_DIR%\deji\Scripts\pythonw.exe"
set "GUARD_PY=%SCRIPT_DIR%\guard.py"
set "TEMPLATE_XML=%SCRIPT_DIR%\task_guard_template.xml"
set "TASK_XML=%SCRIPT_DIR%\task_guard.xml"
set "TASK_USER=%USERDOMAIN%\%USERNAME%"

if not exist "%PYTHONW%" (
    echo [ERROR] pythonw.exe not found: %PYTHONW%
    echo [FIX]   Re-run install.bat after Step 1 succeeds.
    pause
    exit /b 1
)
if not exist "%GUARD_PY%" (
    echo [ERROR] guard.py not found: %GUARD_PY%
    pause
    exit /b 1
)
if not exist "guard_config.ini" (
    echo [WARN] guard_config.ini not found.
    echo [INFO] guard.py will create it with default settings on first run.
)

:: Remove legacy boot task (root cause of the boot-time lag / BSOD)
schtasks /query /tn "DTW_AutoRun" >nul 2>&1
if !errorlevel! equ 0 (
    echo [INFO] Removing legacy boot task DTW_AutoRun...
    schtasks /delete /tn "DTW_AutoRun" /f >nul 2>&1
    if !errorlevel! equ 0 (
        echo [OK]   Legacy boot task removed.
    ) else (
        echo [WARN] Could not remove DTW_AutoRun. Delete it manually in taskschd.msc
    )
)

:: Remove previous guard task so re-install is always clean
schtasks /delete /tn "DTW_Guard" /f >nul 2>&1

set "TASK_RESULT=1"
if not exist "%TEMPLATE_XML%" goto TASK_FALLBACK

echo [INFO] Generating task_guard.xml ...
del /q "%TASK_XML%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t=[IO.File]::ReadAllText('%TEMPLATE_XML%'); $t=$t.Replace('__TASK_USER__','%TASK_USER%'); $t=$t.Replace('__PYTHONW_PATH__','%PYTHONW%'); $t=$t.Replace('__GUARD_SCRIPT__','%GUARD_PY%'); $t=$t.Replace('__WORK_DIR__','%SCRIPT_DIR%'); [IO.File]::WriteAllText('%TASK_XML%',$t,[Text.Encoding]::Unicode)"

if not exist "%TASK_XML%" goto TASK_FALLBACK

echo [INFO] Importing task from XML ...
schtasks /create /tn "DTW_Guard" /xml "%TASK_XML%" /f
set "TASK_RESULT=!errorlevel!"
if !TASK_RESULT! equ 0 goto TASK_DONE

:TASK_FALLBACK
echo [WARN] XML import not available, falling back to schtasks command line...
schtasks /create /tn "DTW_Guard" /tr "\"%PYTHONW%\" \"%GUARD_PY%\"" /sc onlogon /rl highest /f
set "TASK_RESULT=!errorlevel!"
if !TASK_RESULT! equ 0 (
    echo [NOTE] Command-line task has a 3-day run limit by default.
    echo        If this machine runs for weeks without reboot, open
    echo        taskschd.msc -^> DTW_Guard -^> Settings and clear
    echo        "Stop the task if it runs longer than".
)

:TASK_DONE
if !TASK_RESULT! neq 0 (
    echo [ERROR] Task registration failed, code=!TASK_RESULT!
    echo [MANUAL] Run taskschd.msc and create a task:
    echo          Name   : DTW_Guard
    echo          Trigger: At log on
    echo          Action : "%PYTHONW%" "%GUARD_PY%"
    echo          Option : Run with highest privileges
    pause
    exit /b 1
)
echo [OK]   Task DTW_Guard registered (at log on).

:: ============================================================
:: Step 3.5: Start the guard right now
::   A LogonTrigger task only fires at logon. Since install.bat is
::   usually run after logging in, nothing would be watching the
::   detection app until the next logon. Start it immediately so the
::   verification steps in the manual work right away.
::   --no-delay skips startup_delay (that delay only matters at logon).
:: ============================================================
echo.
echo ============================================================
echo  [Step 3.5] Start guard now (no need to reboot)
echo ============================================================

set "PY=%SCRIPT_DIR%\deji\Scripts\python.exe"
set "GUARD_PID_FILE=%SCRIPT_DIR%\guard.pid"

:: 说明：
:: 1. LogonTrigger 任务只在登录时触发，而 install.bat 通常在登录后才运行，
::    因此这里必须立即启动一次守护，否则要到下次登录才开始监测。
:: 2. --no-delay 跳过 startup_delay（该延迟只为避开登录时的磁盘高峰）。
:: 3. 必须用 Start-Process 彻底脱离：start /b 会让子进程继承本控制台的
::    输出管道，脚本结束时会挂住。
:: 注意：本 if 块内不要再写 :: 注释，cmd 解析块内标签会出错。
if exist "%PY%" (
    echo [INFO] Stopping any previous guard instance...
    "%PY%" "%GUARD_PY%" --stop >nul 2>&1
    del /q "%GUARD_PID_FILE%" >nul 2>&1
    echo [INFO] Starting guard in background...
    powershell -NoProfile -Command "Start-Process -FilePath '%PYTHONW%' -ArgumentList '\"%GUARD_PY%\"','--no-delay' -WindowStyle Hidden" >nul 2>&1
    echo [INFO] Waiting for the guard to come up...
    set "GPID="
    for /l %%n in (1,1,20) do (
        if not defined GPID (
            if exist "%GUARD_PID_FILE%" set /p GPID=<"%GUARD_PID_FILE%"
            if not defined GPID ping -n 2 127.0.0.1 >nul
        )
    )
    if defined GPID (
        echo [OK]   Guard running, pid=!GPID!
    ) else (
        echo [WARN] Guard did not report a pid within ~20s.
        echo [HINT] See logs\guard_YYYYMMDD.log, or run: run_guard.bat console
    )
) else (
    echo [WARN] python.exe not found, skip immediate start.
    echo [INFO] The guard will start automatically at next logon.
)

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
echo    Task name   : DTW_Guard  (trigger: at log on)
echo    Task account: %TASK_USER%
echo    Guard       : %SCRIPT_DIR%\guard.py  (already started now, also starts at logon)
echo    Watcher     : started only while the detection app is running
echo    App process : see guard_config.ini  -^> app_process
echo    Run script  : %SCRIPT_DIR%\run.bat  (manual / used by guard)
echo    Log dir     : %SCRIPT_DIR%\logs
echo ============================================================
echo.
echo [NOTE] No task runs at boot anymore, so the machine starts clean.
echo        The guard is running NOW and will also start at each logon.
echo        It launches the watcher when the detection software opens,
echo        and stops it when it closes. You do NOT need to run
echo        run.bat or run_guard.bat in daily use.
echo.
echo [NEXT] To verify (no reboot needed):
echo         1. Open the detection software, wait 5-10s, then check
echo            d:\data\watcher_status.txt shows status=ready
echo         2. Close the detection software, watcher stops within ~5s
echo         3. If it does not react: run  run_guard.bat test
echo            then run  run_guard.bat list  to find the real exe name
echo            and correct app_process in guard_config.ini
echo.
echo ============================================================
pause
