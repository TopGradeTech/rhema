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

"""Single source of truth for the app's version string, read by the
in-app update checker (update_mixin.py). Bump this alongside
installer.iss's MyAppVersion on every release - the two aren't linked
by tooling, so keeping them in sync is a manual step.
"""

APP_VERSION = "1.1.6"
