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

"""Report how far the installed environment has drifted from requirements.lock.

Informational only - it exits 0 even when everything has drifted, because drift
is expected: requirements.txt is deliberately loose while releases are built
from the lockfile. The point is to make the gap visible, so a green dependency
check still tells you how far the contributor experience has moved from the
environment your installers are built in.

Run it anywhere the project is installed:
    python scripts/report_drift.py
"""

import importlib.metadata as md
import io
import os
import sys

# The packages worth watching: the model runtime, the UI toolkit, and the
# imaging/vision libraries. A major bump in any of these has a realistic chance
# of breaking the app for someone installing from requirements.txt.
WATCH = [
    "torch",
    "torchaudio",
    "transformers",
    "tokenizers",
    "ttkbootstrap",
    "faster-whisper",
    "ctranslate2",
    "silero-vad",
    "numpy",
    "scipy",
    "opencv-python",
    "pillow",
]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def normalise(name):
    return name.lower().replace("_", "-")


def read_lock(path):
    pinned = {}
    with io.open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "==" not in line:
                continue
            name, version = line.split("==", 1)
            pinned[normalise(name)] = version.strip()
    return pinned


def main():
    lock_path = os.path.join(ROOT, "requirements.lock")
    if not os.path.exists(lock_path):
        print("requirements.lock not found - nothing to compare against")
        return 0
    pinned = read_lock(lock_path)

    rows = []
    for package in WATCH:
        key = normalise(package)
        try:
            installed = md.version(package)
        except md.PackageNotFoundError:
            installed = "ABSENT"
        expected = pinned.get(key, "(unpinned)")
        rows.append((package, installed, expected))

    width = max(len(r[0]) for r in rows)
    header = "%-*s  %-18s %-18s %s" % (width, "package", "installed", "in lock", "")
    print(header)
    print("-" * len(header))

    drifted = 0
    for package, installed, expected in rows:
        note = ""
        if expected not in ("(unpinned)", installed):
            drifted += 1
            # Flag a differing leading component separately: that is where an
            # incompatible API change is most likely to be hiding.
            if installed != "ABSENT" and installed.split(".")[0] != expected.split(".")[0]:
                note = "MAJOR DRIFT"
            else:
                note = "drift"
        print("%-*s  %-18s %-18s %s" % (width, package, installed, expected, note))

    print(
        "\n%d of %d watched packages differ from the lockfile."
        % (drifted, len(rows))
    )
    print(
        "This is informational: requirements.txt is loose by design, and "
        "releases are built from requirements.lock."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
