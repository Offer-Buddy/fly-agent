@echo off
REM One-click start: PostgreSQL + FastAPI (see scripts/local/start.ps1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\local\start.ps1"
if errorlevel 1 (
    echo.
    echo Startup failed. Read the message above.
    pause
)
