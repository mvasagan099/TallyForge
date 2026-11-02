"""
Windows Standalone Executable Packaging Script for TallyForge
TallyForge — Reverse Engineered. Rebuilt. Reimagined.
Automates the build of a standalone Windows distribution using PyInstaller,
verifying assets and structuring the portable distribution with clean bin/data separation.
"""

import os
import sys
import shutil
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def check_prerequisites():
    print("[1/5] Checking build prerequisites...")
    required_paths = [
        os.path.join(BASE_DIR, "static"),
        os.path.join(BASE_DIR, "definitions"),
        os.path.join(BASE_DIR, "server.py"),
        os.path.join(BASE_DIR, "desktop_app.py"),
        os.path.join(BASE_DIR, "engine", "accounting_db.py"),
        os.path.join(BASE_DIR, "engine", "tdl_parser.py"),
        os.path.join(BASE_DIR, "engine", "runtime.py"),
        os.path.join(BASE_DIR, "pyinstaller_windows.spec")
    ]
    for p in required_paths:
        if not os.path.exists(p):
            raise FileNotFoundError(f"Missing essential build file: {p}")
    print("  ✓ All required source files and definitions found.")

def build_windows_spec():
    print("[2/5] Running PyInstaller build using pyinstaller_windows.spec...")
    spec_path = os.path.join(BASE_DIR, "pyinstaller_windows.spec")
    
    # Check if pyinstaller executable is accessible
    pyinstaller_cmd = shutil.which("pyinstaller")
    if not pyinstaller_cmd:
        print("  [!] 'pyinstaller' not found in PATH.")
        print("  [*] You can install it on Windows via: pip install pyinstaller")
        print("  [*] Then execute: pyinstaller pyinstaller_windows.spec")
        return False
    
    cmd = [pyinstaller_cmd, "--clean", "-y", spec_path]
    print(f"  Executing: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=BASE_DIR)
    if result.returncode != 0:
        print(f"  [ERROR] PyInstaller failed with exit code {result.returncode}")
        return False
    print("  ✓ PyInstaller build completed successfully.")
    return True

def assemble_portable_distribution():
    print("[3/5] Assembling portable Windows package structure...")
    dist_dir = os.path.join(BASE_DIR, "dist", "TallyForge_Windows_Portable")
    os.makedirs(dist_dir, exist_ok=True)
    
    # 1. Ensure writable user data directory exists
    data_dir = os.path.join(dist_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    backups_dir = os.path.join(data_dir, "backups")
    os.makedirs(backups_dir, exist_ok=True)
    
    # 2. Copy initial seed database if available
    seed_db = os.path.join(BASE_DIR, "tally_data.db")
    target_db = os.path.join(data_dir, "tally_data.db")
    if os.path.exists(seed_db) and not os.path.exists(target_db):
        shutil.copy2(seed_db, target_db)
        print(f"  ✓ Copied template database to portable data directory: {target_db}")

    # 3. Create Windows Launcher Batch Script
    bat_content = """@echo off
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

if exist "%SCRIPT_DIR%TallyForge\\TallyForge.exe" (
    start "" "%SCRIPT_DIR%TallyForge\\TallyForge.exe" --data-dir "%TALLY_DATA_DIR%"
) else if exist "%SCRIPT_DIR%TallyPrime\\TallyPrime.exe" (
    start "" "%SCRIPT_DIR%TallyPrime\\TallyPrime.exe" --data-dir "%TALLY_DATA_DIR%"
) else (
    echo [*] Launching via python desktop_app.py
    python "%SCRIPT_DIR%..\\desktop_app.py" --data-dir "%TALLY_DATA_DIR%"
)
"""
    bat_path = os.path.join(dist_dir, "run_tallyforge.bat")
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat_content)
    with open(os.path.join(dist_dir, "run_tally.bat"), "w", encoding="utf-8") as f:
        f.write(bat_content)
    print(f"  ✓ Created launcher script: {bat_path}")

    # 4. Create README_WINDOWS.txt
    readme_content = """============================================================
TallyForge — Reverse Engineered. Rebuilt. Reimagined.
An independently rebuilt accounting and business management application
inspired by TallyPrime's functionality.
============================================================

1. QUICK START:
   Double-click 'run_tallyforge.bat' or 'run_tally.bat' to launch TallyForge.
   The application starts an isolated local HTTP server (127.0.0.1:8080)
   and immediately opens your default browser or native window.

2. PORTABLE DATA DIRECTORY:
   All your companies, vouchers, ledgers, and audit logs are safely stored
   in the '.\\data\\tally_data.db' file.
   Local automated backups are saved in '.\\data\\backups\\'.

3. 100% OFFLINE & PRIVACY:
   This software operates strictly on localhost (127.0.0.1).
   Zero data is transmitted to any cloud servers or third-party CDNs.

4. SYSTEM REQUIREMENTS:
   - Windows 10, 11 or Windows Server 2016+ (64-bit)
   - 50 MB Free Disk Space
   - Modern Web Browser (Chrome, Edge, Firefox, or Brave)
"""
    readme_path = os.path.join(dist_dir, "README_WINDOWS.txt")
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme_content)
    print(f"  ✓ Created Windows documentation: {readme_path}")
    print(f"\n[✓] Windows Portable Distribution configured at: {dist_dir}")

def main():
    print("=" * 60)
    print(" TallyForge - Windows Build & Packaging Wizard")
    print("=" * 60)
    try:
        check_prerequisites()
        build_windows_spec()
        assemble_portable_distribution()
        print("=" * 60)
        print(" BUILD COMPLETE")
        print("=" * 60)
    except Exception as e:
        print(f"\n[BUILD ERROR] {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
