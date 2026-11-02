# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller Specification File for TallyPrime 2026 Enterprise Offline
Bundles the Python runtime, SQLite engine, TDL runtime, static assets, and definitions
into a fully standalone, zero-dependency offline executable application.
"""

import os

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
        'sqlite3',
        'json',
        'hashlib',
        'decimal',
        'datetime',
        'http.server',
        'urllib.parse',
        'webbrowser'
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

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TallyPrime',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=True,
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
    name='TallyPrime',
)

app = BUNDLE(
    coll,
    name='TallyPrime.app',
    icon=None,
    bundle_identifier='com.tallyprime.offline.enterprise',
    info_plist={
        'CFBundleName': 'TallyPrime',
        'CFBundleDisplayName': 'TallyPrime Enterprise 2026',
        'CFBundleIdentifier': 'com.tallyprime.offline.enterprise',
        'CFBundleVersion': '7.2.0',
        'CFBundleShortVersionString': '7.2.0',
        'NSHighResolutionCapable': 'True',
        'LSMinimumSystemVersion': '11.0.0'
    }
)
