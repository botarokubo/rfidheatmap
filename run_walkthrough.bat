@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import PySide6, hid" >nul 2>&1
    if not errorlevel 1 (
        ".venv\Scripts\python.exe" walkthrough.py
        goto :finished
    )
)

py -3.13 -c "import PySide6, hid" >nul 2>&1
if not errorlevel 1 (
    py -3.13 walkthrough.py
    goto :finished
)

py -3 -c "import PySide6, hid" >nul 2>&1
if not errorlevel 1 (
    py -3 walkthrough.py
    goto :finished
)

python -c "import PySide6, hid" >nul 2>&1
if not errorlevel 1 (
    python walkthrough.py
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
