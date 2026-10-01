@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import PySide6, matplotlib, numpy" >nul 2>&1
    if not errorlevel 1 (
        ".venv\Scripts\python.exe" heatmap.py
        goto :finished
    )
)

py -3.13 -c "import PySide6, matplotlib, numpy" >nul 2>&1
if not errorlevel 1 (
    py -3.13 heatmap.py
    goto :finished
)

py -3 -c "import PySide6, matplotlib, numpy" >nul 2>&1
if not errorlevel 1 (
    py -3 heatmap.py
    goto :finished
)

python -c "import PySide6, matplotlib, numpy" >nul 2>&1
if not errorlevel 1 (
    python heatmap.py
    goto :finished
)

echo.
echo No Python environment with all required packages was found.
echo Create the project environment and install the dependencies with:
echo.
echo     py -3.13 -m venv .venv
echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
pause
exit /b 1

:finished
if errorlevel 1 pause
