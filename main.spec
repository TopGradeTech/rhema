# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


def collect_tcl_tk_datas():
    tcl_root = Path(sys.base_prefix) / "tcl"
    datas = []
    mappings = [
        ("tcl8.6", "_tcl_data"),
        ("tk8.6", "_tk_data"),
        ("tcl8", "tcl8"),
    ]
    for source_name, target_name in mappings:
        source_path = tcl_root / source_name
        if source_path.exists():
            datas.append((str(source_path), target_name))
    return datas


faster_whisper_datas = collect_data_files(
    "faster_whisper",
    includes=["assets/*.onnx"],
)
tcl_tk_datas = collect_tcl_tk_datas()

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=faster_whisper_datas + tcl_tk_datas,
    hiddenimports=['tkinter', 'tkinter.ttk', '_tkinter'],
    hookspath=['pyinstaller_hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='main',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
