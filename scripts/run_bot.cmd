@echo off
REM Wrapper for Sport News Bot — used by Windows Task Scheduler.
REM Activates venv, runs the bot, logs everything to logs\stdout.log.

cd /d "C:\Users\Administrator\Desktop\Sport News Qyran"

REM Ensure logs/ exists
if not exist "logs" mkdir "logs"

REM Run bot, append stdout/stderr to a single rolling file (Python loguru also writes its own rotated logs)
"%~dp0..\.venv\Scripts\python.exe" -m src.main >> "logs\stdout.log" 2>&1
