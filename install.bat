@echo off
chcp 65001 >nul
setlocal EnableExtensions
set "PYTHONIOENCODING=utf-8"

where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo Windows PowerShell is required.
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_windows.ps1" %*
set "INSTALL_EXIT=%ERRORLEVEL%"
echo.
if "%INSTALL_EXIT%"=="0" (
    echo Installation or environment check completed.
    echo Run start.bat to open Draw Agent Studio at http://127.0.0.1:8000
) else (
    echo Installation failed. Exit code: %INSTALL_EXIT%
    echo Logs: "%~dp0runs\windows-install"
)
pause
exit /b %INSTALL_EXIT%
