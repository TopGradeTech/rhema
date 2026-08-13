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

"""Regenerate requirements.lock from the active venv.

Written as a real file rather than a bash heredoc: the heredoc collapsed
doubled backslashes before Python saw them, so '\\Scripts\\activate' became
'\a' (a bell char) and mangled the header.
"""
import io
import re
import subprocess
import sys

frozen = subprocess.check_output(
    [sys.executable, "-m", "pip", "freeze"], text=True
).splitlines()

pkgs = []
fork_line = None
for line in frozen:
    s = line.strip()
    if not s:
        continue
    if "realtimestt" in s.lower():
        m = re.search(r"git\+(\S+?)@([0-9a-f]{40})", s)
        assert m, "could not parse fork line: %r" % s
        # Pin the exact commit, not the editable local path: a lockfile that
        # describes the maintainer's working copy is useless to anyone else.
        fork_line = "RealtimeSTT @ git+%s@%s" % (m.group(1), m.group(2))
        continue
    pkgs.append(s)

assert fork_line, "did not find the RealtimeSTT line"
pkgs.append(fork_line)
pkgs.sort(key=str.lower)

ACTIVATE = ".venv" + chr(92) + "Scripts" + chr(92) + "activate"
CONT = " " + chr(92)  # trailing line-continuation backslash

HEADER = [
    "# " + "-" * 73,
    "# requirements.lock - the EXACT dependency set the shipped v1.1.4 Windows",
    "# installer was built and verified against (Python 3.12, Windows 11).",
    "#",
    "# This is a record, not the install spec. requirements.txt stays the loose,",
    "# human-edited list; this file exists so a build can be reproduced, and so",
    '# "it worked on my machine" can be diffed against something concrete.',
    "#",
    "# Reproduce this environment:",
    "#     python -m venv .venv",
    "#     " + ACTIVATE,
    "#     pip install -r requirements.lock",
    "#",
    "# TWO CAVEATS, both of which will bite a plain install from default PyPI:",
    "#",
    "#  1. torch/torchaudio are pinned to +cu124 local-version builds, which do",
    "#     NOT exist on the default PyPI index. Install them from the CUDA index",
    "#     FIRST, then this file:",
    "#         pip install torch==2.6.0+cu124 torchaudio==2.6.0+cu124" + CONT,
    "#             --index-url https://download.pytorch.org/whl/cu124",
    "#     For a CPU-only environment, drop the +cu124 suffixes and use plain",
    "#     PyPI; the app runs on CPU, just slower (see the Device settings).",
    "#",
    "#  2. RealtimeSTT is pinned to an exact commit on our fork rather than a",
    "#     PyPI release, because the app depends on that commit's engine_options",
    "#     pass-through to keep startup offline. See the long note in",
    "#     requirements.txt for why stock upstream fails silently without it.",
    "#",
    "# Regenerate with scripts/gen_lock.py (or `pip freeze`, rewriting the",
    "# editable RealtimeSTT line back into the pinned git+...@<sha> form below).",
    "# " + "-" * 73,
]

out = "\n".join(HEADER) + "\n" + "\n".join(pkgs) + "\n"
with io.open("requirements.lock", "w", encoding="utf-8", newline="\r\n") as f:
    f.write(out)

print("wrote requirements.lock: %d pinned packages" % len(pkgs))
print("fork line -> %s" % fork_line)
