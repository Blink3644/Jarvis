@echo off
rem For the Jarvis owner: builds dist\Jarvis-<version>.zip from the last git commit, to attach to a GitHub Release.
rem Only committed files go in (no .venv, no settings, no keys). Dev notes are left out via .gitattributes.
cd /d "%~dp0"
for /f %%V in ('.venv\Scripts\python -c "import jarvis; print(jarvis.__version__)"') do set VER=%%V
if not exist dist mkdir dist
git archive --format=zip --prefix=Jarvis/ -o "dist\Jarvis-%VER%.zip" HEAD || (echo Building the zip failed. & pause & exit /b 1)
rem Friends who tinker get a short CLAUDE.md for Claude Code (packaging\CLAUDE_zip.md), not the developers' one.
powershell -NoProfile -Command "Add-Type -A System.IO.Compression.FileSystem; $z=[IO.Compression.ZipFile]::Open('dist\Jarvis-%VER%.zip','Update'); [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($z,'packaging\CLAUDE_zip.md','Jarvis/CLAUDE.md'); $z.Dispose()" || (echo Adding CLAUDE.md to the zip failed. & pause & exit /b 1)
echo Made dist\Jarvis-%VER%.zip
pause
