@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" heatmap.py
) else (
    py -3 heatmap.py
)

if errorlevel 1 pause
