# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller Specification File for TallyForge (Windows Standalone)
TallyForge — Reverse Engineered. Rebuilt. Reimagined.
Generates a standalone Windows binary (TallyForge.exe) with embedded SQLite, TDL Engine,
and static frontend assets, maintaining complete isolation between executable binaries
and writable accounting data directories (TALLY_DATA_DIR).
"""

import os
import sys

block_cipher = None
BASE_DIR = os.path.abspath(os.path.dirname(SPEC))

added_data = [
    (os.path.join(BASE_DIR, 'static'), 'static'),
    (os.path.join(BASE_DIR, 'definitions'), 'definitions'),
    (os.path.join(BASE_DIR, 'tally_data.db'), '.'),
]

a = Analysis(
    ['desktop_app.py'],
    pathex=[BASE_DIR],
    binaries=[],
    datas=added_data,
    hiddenimports=[
        'engine.accounting_db',
        'engine.tdl_parser',
        'engine.runtime',
        'engine.tdl_laboratory',
        'engine.statutory_config',
        'sqlite3',
        'json',
        'hashlib',
        'decimal',
        'datetime',
        'http.server',
        'urllib.parse',
        'webbrowser',
        'argparse',
        'shutil'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'scipy', 'pandas', 'torch'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Single-file or Onedir configuration
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TallyForge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # Set to True so enterprise admins can inspect startup logs
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TallyForge',
)
