@echo off
REM ============================================================
REM  SET filings monitor — continuous loop mode
REM
REM  Polls SET for new งบ and XD/XM/XR/etc. announcements every
REM  30 seconds. When a new filing arrives for any symbol in
REM  reference/set50.json, the full ingest pipeline runs and the
REM  chart is pushed to your Telegram chat within ~1 minute.
REM
REM  Usage:
REM    1. Copy this file to run_monitor.bat (gitignored)
REM    2. Paste your bot token + chat id below
REM    3. Double-click run_monitor.bat
REM    4. Leave the window open — Ctrl+C in the window to stop
REM ============================================================

cd /d "%~dp0"

set TELEGRAM_BOT_TOKEN=PASTE_YOUR_BOT_TOKEN_HERE
REM Channel chat_id (e.g. -1001234567890) OR your own DM chat_id
set TELEGRAM_CHAT_ID=PASTE_YOUR_CHAT_ID_HERE

set PYTHONIOENCODING=utf-8
chcp 65001 > nul

set PY_EXE="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist %PY_EXE% set PY_EXE=python

echo =====================================================
echo  SET Filings Monitor - starting loop (30s interval)
echo  Target: watchlist from reference/set50.json
echo =====================================================
echo.

%PY_EXE% -u -m src.cli.monitor --loop --interval 30 --lookback 2

pause
