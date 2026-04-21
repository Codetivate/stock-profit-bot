@echo off
REM Copy this file to run_bot.bat, paste your real token, then double-click to start.
REM run_bot.bat is gitignored so your token won't be pushed to GitHub.

cd /d "%~dp0"

set TELEGRAM_BOT_TOKEN=PASTE_YOUR_BOT_TOKEN_HERE
set PYTHONIOENCODING=utf-8
chcp 65001 > nul

set PY_EXE="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist %PY_EXE% set PY_EXE=python

echo ====================================
echo  NetProfitNews Bot - Starting...
echo  Bot: @NetProtfitNews_bot
echo ====================================
echo.

%PY_EXE% command_handler.py --loop

pause
