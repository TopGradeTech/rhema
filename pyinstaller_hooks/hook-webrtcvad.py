from PyInstaller.utils.hooks import copy_metadata

# Overrides pyinstaller-hooks-contrib's hook-webrtcvad.py, which does
# copy_metadata('webrtcvad') and aborts the whole build with
# PackageNotFoundError here: the actually-installed distribution is
# webrtcvad-wheels (a different PyPI package that provides the same
# `webrtcvad` module), not `webrtcvad`. webrtcvad.py's own __version__
# lookup already queries 'webrtcvad-wheels' by name (falling back to
# "unknown" if missing), so that's the metadata that actually needs to be
# bundled for it to resolve correctly.
datas = copy_metadata('webrtcvad-wheels')
