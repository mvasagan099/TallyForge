@echo off
title Build TallyPrime Windows Executable
echo ============================================================
echo   Building TallyPrime 2026 Enterprise Windows Executable
echo ============================================================

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    pause
    exit /b 1
)

REM Check if PyInstaller is installed, install if missing
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [*] PyInstaller not detected. Installing PyInstaller...
    pip install pyinstaller
)

REM Run automated packaging wizard
python build_windows_exe.py

echo.
echo Build script finished.
pause
