@echo off
REM View structured logs in UTF-8 (fixes Chinese mojibake on Windows).
REM
REM Why: logs/*.jsonl is UTF-8, but `type` / Get-Content / notepad decode it with
REM the system ANSI code page (cp936) unless a BOM is present, which turns Chinese
REM text into gibberish. This wrapper runs scripts/view_logs.py, which always reads
REM and prints UTF-8.
REM
REM Usage:
REM   view-logs.bat                         today's log, last 50 lines
REM   view-logs.bat -n 200                  last 200 lines
REM   view-logs.bat --level error           error and above
REM   view-logs.bat --grep timeout          filter by keyword
REM   view-logs.bat --list                  list all log files
REM   view-logs.bat --fix-bom               add UTF-8 BOM to old log files
setlocal
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" scripts\view_logs.py %*
set "RC=%ERRORLEVEL%"

REM Keep the window open when launched by double-click (no arguments).
if "%~1"=="" pause
exit /b %RC%
