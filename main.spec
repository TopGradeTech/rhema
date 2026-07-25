# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


def _editable_package_root(package_name):
    """Resolve the real source directory for a package that may be pip
    installed in editable mode (as RealtimeSTT is here, from the local
    TopGradeTelecom fork). Editable installs are wired in via a PEP 660
    custom import finder (a .pth file that installs a MetaPathFinder), which
    PyInstaller's static modulegraph analysis can't see through - without
    this, RealtimeSTT is silently missing from the build entirely (no
    warning, no error at build time; it just isn't there at runtime).
    Importing it here runs in this same venv, so the editable finder
    resolves it normally; the returned parent directory can then be added to
    pathex so PyInstaller's own analysis can find and trace it like any
    other on-disk package.
    """
    module = __import__(package_name)
    return str(Path(module.__file__).resolve().parent.parent)


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
realtimestt_pathex = [_editable_package_root("RealtimeSTT")]
# Belt-and-suspenders alongside pathex above: RealtimeSTT's transcription
# engine backends (transcription_engines/factory.py) are selected via
# importlib.import_module() from a string lookup table, which static
# analysis can't follow even once the package itself is discoverable.
realtimestt_hiddenimports = collect_submodules("RealtimeSTT")
# warmup_audio.wav is opened at recorder construction, resolved relative to
# the RealtimeSTT package dir (core/transcription.py, core/initialization.py:
# dirname(dirname(__file__)) + assets/...). Missing it doesn't fail the
# build - it fails at runtime, and viciously: recorder construction has
# already spawned its two worker processes by the time the open() raises, so
# every 5s retry (realtime_stt_mixin.py cooldown) leaked two more orphaned
# processes. Built manually instead of via collect_data_files, which can't
# see through this package's PEP 660 editable install.
realtimestt_datas = [
    (
        str(Path(_editable_package_root("RealtimeSTT")) / "RealtimeSTT" / "assets"),
        "RealtimeSTT/assets",
    )
]
# The in-app "Setup guide" link (video overlay settings, _open_doc) opens
# this file via sys._MEIPASS at runtime - was never actually bundled
# before, so that link has been broken in every packaged build to date.
doc_datas = [("README.md", ".")]

a = Analysis(
    ['main.py'],
    pathex=realtimestt_pathex,
    binaries=[],
    datas=faster_whisper_datas + tcl_tk_datas + realtimestt_datas + doc_datas,
    hiddenimports=['tkinter', 'tkinter.ttk', '_tkinter'] + realtimestt_hiddenimports,
    hookspath=['pyinstaller_hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# Onedir build: the exe holds only the bootloader + bytecode archive, with
# binaries/datas in the surrounding folder (COLLECT below). Chosen over
# onefile because onefile re-extracted the ~2.5 GB archive to %TEMP% on
# every launch, delaying the window by ~40 s; onedir launches in seconds.
# The folder is delivered to users via the Inno Setup installer
# (installer.iss), so onefile's single-file portability is no longer needed.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Rhema',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Rhema',
)
