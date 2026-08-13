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
