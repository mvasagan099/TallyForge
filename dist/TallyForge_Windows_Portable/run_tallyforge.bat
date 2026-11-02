@echo off
title TallyForge — Reverse Engineered. Rebuilt. Reimagined.
echo ============================================================
echo   TallyForge - Standalone Desktop Launcher
echo   Reverse Engineered. Rebuilt. Reimagined.
echo   Mode: 100%% Fully Offline
echo ============================================================

set SCRIPT_DIR=%~dp0
set TALLY_DATA_DIR=%SCRIPT_DIR%data

echo [*] Data Directory: %TALLY_DATA_DIR%
echo [*] Starting TallyForge Server and Opening Browser...

if exist "%SCRIPT_DIR%TallyForge\TallyForge.exe" (
    start "" "%SCRIPT_DIR%TallyForge\TallyForge.exe" --data-dir "%TALLY_DATA_DIR%"
) else if exist "%SCRIPT_DIR%TallyPrime\TallyPrime.exe" (
    start "" "%SCRIPT_DIR%TallyPrime\TallyPrime.exe" --data-dir "%TALLY_DATA_DIR%"
) else (
    echo [*] Launching via python desktop_app.py
    python "%SCRIPT_DIR%..\desktop_app.py" --data-dir "%TALLY_DATA_DIR%"
)
