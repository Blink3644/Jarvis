@echo off
rem Adds "Jarvis" to your Start Menu (just for you, no admin needed).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run console_test.bat once first to set Jarvis up.
  pause
  exit /b
)
.venv\Scripts\python -m jarvis.core.shortcuts
pause
