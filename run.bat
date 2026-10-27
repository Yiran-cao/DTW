@echo off
setlocal enabledelayedexpansion

:: ============================================================
:: DTW 自动选波 — run.bat（开机自动运行脚本）
:: 由 Windows 任务计划在开机时自动触发，无需人工干预。
:: 职责：激活虚拟环境 → 运行 DTW 选波 → 写日志。
:: ============================================================

cd /d "%~dp0"
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

:: ---- 日志目录 ----
set "LOG_DIR=%SCRIPT_DIR%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f "usebackq delims=" %%i in (`powershell -Command "Get-Date -Format 'yyyy-MM-dd'"`) do set "LOG_DATE=%%i"
set "LOG_FILE=%LOG_DIR%\%LOG_DATE%.log"

:: ---- 运行开始 ----
echo.>> "%LOG_FILE%"
echo ============================================================>> "%LOG_FILE%"
echo [开始] %date% %time%>> "%LOG_FILE%"
echo ============================================================>> "%LOG_FILE%"

:: ============================================================
:: 步骤 1：激活虚拟环境
:: ============================================================
set "ACTIVATE=%SCRIPT_DIR%\deji\Scripts\activate.bat"

if not exist "%ACTIVATE%" (
    echo [错误] 虚拟环境激活脚本不存在: %ACTIVATE%>> "%LOG_FILE%"
    echo [错误] 请先运行 install.bat 完成一键部署。>> "%LOG_FILE%"
    exit /b 1
)

call "%ACTIVATE%" 2>> "%LOG_FILE%"
if %errorlevel% neq 0 (
    echo [错误] 虚拟环境激活失败！exit code=%errorlevel%>> "%LOG_FILE%"
    echo [错误] 时间: %date% %time%>> "%LOG_FILE%"
    exit /b 1
)

:: ============================================================
:: 步骤 2：运行 DTW 选波脚本
:: ============================================================
set "PYTHON=%SCRIPT_DIR%\deji\Scripts\python.exe"
set "LAUNCHER=%SCRIPT_DIR%\launcher.py"

if not exist "%PYTHON%" (
    echo [错误] Python 解释器不存在: %PYTHON%>> "%LOG_FILE%"
    echo [错误] 请先运行 install.bat 完成一键部署。>> "%LOG_FILE%"
    exit /b 1
)

if not exist "%LAUNCHER%" (
    echo [错误] 启动脚本不存在: %LAUNCHER%>> "%LOG_FILE%"
    exit /b 1
)

echo [运行] 正在启动 DTW 选波服务...>> "%LOG_FILE%"
"%PYTHON%" "%LAUNCHER%" >> "%LOG_FILE%" 2>&1
set EXIT_CODE=%errorlevel%

:: ============================================================
:: 结束标记
:: ============================================================
echo.>> "%LOG_FILE%"
echo ------------------------------------------------------------>> "%LOG_FILE%"
echo [结束] %date% %time%>> "%LOG_FILE%"
if %EXIT_CODE% equ 0 (
    echo [状态] 正常完成 (exit code=0)>> "%LOG_FILE%"
) else (
    echo [错误] 脚本运行异常 (exit code=%EXIT_CODE%)>> "%LOG_FILE%"
)
echo ============================================================>> "%LOG_FILE%"

exit /b %EXIT_CODE%
