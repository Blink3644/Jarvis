@echo off
rem Stage A4 test: tray icon + on-screen panel + settings window (no console window).
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
  echo Run console_test.bat once first to set Jarvis up.
  pause
  exit /b
)
start "" .venv\Scripts\pythonw.exe -m jarvis --gui
