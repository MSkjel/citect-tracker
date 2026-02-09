# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for Citect SCADA Config Tracker
# Build with: pyinstaller citect-tracker.spec

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['entry_point.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'citect_tracker',
        'citect_tracker.core',
        'citect_tracker.core.models',
        'citect_tracker.core.dbf_reader',
        'citect_tracker.core.project_discovery',
        'citect_tracker.core.snapshot_engine',
        'citect_tracker.core.diff_engine',
        'citect_tracker.core.dbf_writer',
        'citect_tracker.storage',
        'citect_tracker.storage.database',
        'citect_tracker.gui',
        'citect_tracker.gui.main_window',
        'citect_tracker.gui.project_tree',
        'citect_tracker.gui.snapshot_panel',
        'citect_tracker.gui.diff_viewer',
        'citect_tracker.gui.record_detail',
        'citect_tracker.gui.filter_bar',
        'citect_tracker.gui.workers',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PIL',
        'test',
        'unittest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='citect-tracker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # No console window for GUI app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
