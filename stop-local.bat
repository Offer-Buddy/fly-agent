@echo off
REM Stop the API and the local PostgreSQL instance (see scripts/local/stop.ps1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\local\stop.ps1"
pause
