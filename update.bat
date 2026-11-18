@echo off
rem Updates an existing Jarvis install from this folder. Your settings, notes and keys are kept.
rem (New install instead? Use setup.bat.)
title Jarvis update
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1"
echo.
pause
