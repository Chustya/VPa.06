@echo off
rem Double-click to run the check generator on Windows
chcp 65001 > nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py %*
) else (
    python main.py %*
)
echo.
pause
