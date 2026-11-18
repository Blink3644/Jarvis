@echo off
rem Jarvis setup: finds (or installs) Python 3.12, creates .venv, installs requirements, adds a Start Menu shortcut.
rem Safe to run again: it repairs or updates the install. Your settings in %APPDATA%\Jarvis are never touched.
setlocal
cd /d "%~dp0"
title Jarvis setup
echo.
echo  Setting up Jarvis. This downloads about 1 GB the first time and can take 5-15 minutes.
echo.

set "PY="
call :findpy
if defined PY goto havepy

echo  Jarvis needs Python 3.12, and it wasn't found on this PC.
choice /c YN /m " Install Python 3.12 now (free, from Microsoft's winget)"
if errorlevel 2 goto manual
winget install -e --id Python.Python.3.12 --scope user
call :findpy
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY goto manual

:havepy
echo  Using Python: %PY%
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1 || rmdir /s /q .venv
)
if not exist .venv\Scripts\python.exe (
  echo  Creating Jarvis's own Python folder ^(.venv^)...
  %PY% -m venv .venv || goto fail
)
echo  Installing the parts Jarvis needs...
.venv\Scripts\python -m pip install --upgrade pip --quiet --disable-pip-version-check
.venv\Scripts\python -m pip install -r requirements.txt --disable-pip-version-check || goto fail
if not defined JARVIS_NO_SHORTCUT .venv\Scripts\python -m jarvis.core.shortcuts
echo.
echo  All done! Start Jarvis from the Start Menu ("Jarvis") or by double-clicking run.bat.
echo  The settings window opens from the tray icon (bottom-right, near the clock).
echo.
pause
exit /b 0

:findpy
rem Jarvis's pinned parts are built for Python 3.12, so look for exactly that version.
for %%C in ("py -3.12" "python" "python3") do (
  if not defined PY (
    %%~C -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1 && set "PY=%%~C"
  )
)
exit /b 0

:manual
echo.
echo  Please install Python 3.12 yourself:
echo   1. Go to https://www.python.org/downloads/windows/ and download the latest "Python 3.12" Windows installer (64-bit).
echo   2. Run it and TICK "Add python.exe to PATH" at the bottom of the first screen, then click "Install Now".
echo   3. Close this window and double-click setup.bat again.
echo.
pause
exit /b 1

:fail
echo.
echo  Setup didn't finish. Check your internet connection and run setup.bat again.
echo  If it keeps failing, copy the red text above and send it to whoever gave you Jarvis.
echo.
pause
exit /b 1
