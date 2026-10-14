# -*- mode: python ; coding: utf-8 -*-
# Phase 2 packaging spike (port plan) - NOT the real app spec. Deliberately
# small: proves pywebview/pythonnet freeze and run standalone via
# PyInstaller before any real port feature work depends on it. See
# web_helloworld.py's own docstring for scope.

from PyInstaller.utils.hooks import collect_data_files

# pythonnet/__init__.py resolves Python.Runtime.dll via
# Path(__file__).parent / "runtime" / "Python.Runtime.dll" - a data
# subfolder PyInstaller's static import analysis cannot see (same class of
# gap as RealtimeSTT's warmup_audio.wav in the real main.spec).
pythonnet_datas = collect_data_files("pythonnet", includes=["runtime/*"])
# webview's WinForms backend loads its own WebView2/interop DLLs from its
# own package "lib" subfolder (webview/platforms/winforms.py) - same class
# of gap.
webview_datas = collect_data_files("webview", includes=["lib/*.dll"])

a = Analysis(
    ['web_helloworld.py'],
    pathex=[],
    binaries=[],
    datas=pythonnet_datas + webview_datas,
    hiddenimports=['clr_loader', 'clr_loader.ffi', 'clr_loader.ffi.netfx'],
    hookspath=[],
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
    [],
    exclude_binaries=True,
    name='RhemaWebviewSpike',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='RhemaWebviewSpike',
)
