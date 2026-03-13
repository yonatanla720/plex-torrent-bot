@echo off
REM Entry point for Windows — installs Python if needed, then runs _setup_wizard.py
cd /d "%~dp0"

REM --- Find Python 3.10+ ---

where python >nul 2>&1
if %errorlevel% equ 0 (
    python -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" 2>nul
    if %errorlevel% equ 0 (
        echo [OK] Python found
        python _setup_wizard.py
        pause
        exit /b 0
    )
)

where python3 >nul 2>&1
if %errorlevel% equ 0 (
    python3 -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" 2>nul
    if %errorlevel% equ 0 (
        echo [OK] Python found
        python3 _setup_wizard.py
        pause
        exit /b 0
    )
)

REM --- Python not found, try to install ---

echo [!] Python 3.10+ not found.
echo.

where winget >nul 2>&1
if %errorlevel% equ 0 (
    echo     Installing Python via winget...
    winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    if %errorlevel% equ 0 (
        echo.
        echo [OK] Python installed. Please restart this script.
        pause
        exit /b 0
    )
)

echo [!] Could not auto-install Python.
echo     Download from: https://www.python.org/downloads/
echo     IMPORTANT: Check "Add Python to PATH" during installation.
echo     Then re-run this script.
pause
exit /b 1
