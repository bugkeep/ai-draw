@echo off
chcp 65001 >nul
setlocal EnableExtensions
set "PYTHONIOENCODING=utf-8"
set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo Project environment is missing. Run install.bat first.
    pause
    exit /b 1
)

pushd "%~dp0"
if errorlevel 1 exit /b 1
echo Draw Agent Studio: http://127.0.0.1:8000
echo Configure your model in Settings after opening the page.
echo Press Ctrl+C to stop the service.
"%VENV_PYTHON%" run.py
set "START_EXIT=%ERRORLEVEL%"
popd
if not "%START_EXIT%"=="0" (
    echo Service exited with code %START_EXIT%.
    pause
)
exit /b %START_EXIT%
