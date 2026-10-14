# -*- mode: python ; coding: utf-8 -*-
# Rhema - live speech transcription and translation, run locally.
# Copyright (C) 2026 Zachary Price
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

# Phase 11 of the pywebview port: packages main_webview.py (WebTranslationApp)
# as its OWN separate build, parallel to main.spec/Rhema - not a replacement
# for it. The port's whole architecture keeps main.py/TranslationApp shipping
# and working throughout; this exists to prove the pywebview app packages and
# runs correctly as a real frozen exe, per Phase 2's packaging-spike findings,
# not to cut over the real release. Produces dist/RhemaWebview/RhemaWebview.exe
# - a separate name from Rhema.exe so both builds can sit in dist/
# side by side without colliding.
#
# Almost entirely copy-pasted from main.spec rather than reimagined: the
# STT+NLLB pipeline (RealtimeSTT/faster_whisper/silero_vad), the real hidden
# Tcl interpreter this port's TkVariableInterpreter/FakeRoot genuinely need
# (webview_bridge.py imports real tkinter, and SettingsUIMixin/MonitorMixin
# are still mixed in whole and unmodified for their non-Tk logic, per the
# port's own architecture decision - see main_webview.py's own docstring),
# and the ttkbootstrap import (settings_ui_mixin.py's own `import
# ttkbootstrap as ttkb` at module level, even though nothing in the Web app
# ever constructs a real ttkb.Style()) are all still real dependencies here,
# not vestigial ones removed for this build.
#
# New for this build, versus main.spec: pywebview and pythonnet. Phase 2's
# spike already confirmed PyInstaller finds both packages' own bundled hooks
# (pythonnet's own hook-clr.py, pyinstaller-hooks-contrib's
# hook-clr_loader.py) automatically, with no hookspath entry needed for
# either - hookspath below still only lists this repo's own pyinstaller_hooks
# folder (webrtcvad), unchanged from main.spec, on the same evidence the
# spike already produced once.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


def _editable_package_root(package_name):
    """Resolve the real source directory for a package that may be pip
    installed in editable mode (as RealtimeSTT often is here, from a local
    clone of the TopGradeTech fork - though requirements.txt installs that
    same fork non-editably from git, which this also handles, since the
    import below resolves either layout). Editable installs are wired in
    via a PEP 660
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
silero_vad_datas = collect_data_files(
    "silero_vad",
    includes=["data/*.onnx"],
)
tcl_tk_datas = collect_tcl_tk_datas()
realtimestt_pathex = [_editable_package_root("RealtimeSTT")]
realtimestt_hiddenimports = collect_submodules("RealtimeSTT")
realtimestt_datas = [
    (
        str(Path(_editable_package_root("RealtimeSTT")) / "RealtimeSTT" / "assets"),
        "RealtimeSTT/assets",
    )
]
doc_datas = [("README.md", ".")]
# Included defensively, same as main.spec, even though nothing in the Web
# app's own code path ever calls ttkb.Style() (the only thing that actually
# reads these asset files) - settings_ui_mixin.py/monitor_mixin.py are still
# mixed in whole for their shared logic, and both import ttkbootstrap/tkinter
# at module level regardless of which of their methods ever run.
ttkbootstrap_datas = collect_data_files("ttkbootstrap")

a = Analysis(
    ['main_webview.py'],
    pathex=realtimestt_pathex,
    binaries=[],
    datas=faster_whisper_datas + silero_vad_datas + tcl_tk_datas + realtimestt_datas
    + doc_datas + ttkbootstrap_datas,
    hiddenimports=['tkinter', 'tkinter.ttk', '_tkinter'] + realtimestt_hiddenimports,
    hookspath=['pyinstaller_hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# Onedir, matching main.spec's own reasoning (onefile's ~40s re-extraction
# delay applies just as much here - nothing about swapping Tk for pywebview
# changes that tradeoff).
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RhemaWebview',
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
    name='RhemaWebview',
)
