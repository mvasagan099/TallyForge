@echo off
title TallyPrime 2026 Enterprise Offline
echo ============================================================
echo   TallyPrime 2026 Enterprise - Standalone Desktop Launcher
echo   Mode: 100%% Fully Offline
echo ============================================================

set SCRIPT_DIR=%~dp0
set TALLY_DATA_DIR=%SCRIPT_DIR%data

echo [*] Data Directory: %TALLY_DATA_DIR%
echo [*] Starting Tally Server and Opening Browser...

if exist "%SCRIPT_DIR%TallyPrime\TallyPrime.exe" (
    start "" "%SCRIPT_DIR%TallyPrime\TallyPrime.exe" --data-dir "%TALLY_DATA_DIR%"
) else (
    echo [!] Binary not found at %SCRIPT_DIR%TallyPrime\TallyPrime.exe
    echo [*] Falling back to python desktop_app.py
    python "%SCRIPT_DIR%..\desktop_app.py" --data-dir "%TALLY_DATA_DIR%"
)
