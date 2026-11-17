@echo off
rem Stage A1 console test. Run setup first if .venv is missing.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Setting up Jarvis for the first time, please wait...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python -m jarvis
pause
