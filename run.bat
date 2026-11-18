@echo off
rem Starts Jarvis with no console window (tray icon only). If Jarvis is already running, this just brings it forward.
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
  echo Jarvis isn't set up yet. Double-click setup.bat first.
  pause
  exit /b 1
)
start "" .venv\Scripts\pythonw.exe Jarvis.pyw
