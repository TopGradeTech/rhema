"""Dependency-free pre-flight checks for Rhema.

Deliberately imports nothing outside the standard library, so this runs in a
bare CI container in seconds without installing torch. It cannot verify the
app actually works - live speech in, correct translation out, on the right
monitor is still a manual check (see CLAUDE.md). What it does catch is the
class of mistake that is invisible locally and only surfaces after a release:
a syntax error in a rarely-imported mixin, a version bumped in one file but
not the other, or a requirements file that no longer parses.

Run locally the same way CI does:
    python scripts/smoke_check.py
Exits non-zero on the first category that fails, printing every failure.
"""

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []
notes = []


def fail(msg):
    failures.append(msg)


def check_syntax():
    """Parse every tracked .py file. main.spec is Python too, despite the
    extension, and it is only ever executed by PyInstaller - so a syntax
    error there stays hidden until a release build."""
    targets = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [
            d for d in dirnames
            if d not in {".git", ".venv", "build", "dist", "__pycache__", "logs"}
        ]
        for name in filenames:
            if name.endswith(".py") or name == "main.spec":
                targets.append(os.path.join(dirpath, name))
    for path in sorted(targets):
        rel = os.path.relpath(path, ROOT)
        try:
            with open(path, "rb") as handle:
                ast.parse(handle.read(), filename=rel)
        except SyntaxError as exc:
            fail("syntax: %s:%s %s" % (rel, exc.lineno, exc.msg))
    notes.append("parsed %d Python files" % len(targets))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
        return handle.read()


def check_version_sync():
    """version.py and installer.iss are not linked by tooling. A mismatch
    ships an installer whose recorded version disagrees with what the update
    checker compares against, so upgrades misbehave in ways that are painful
    to diagnose from the outside."""
    app = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', _read("version.py"))
    iss = re.search(
        r'#define\s+MyAppVersion\s+"([^"]+)"', _read("installer.iss")
    )
    if not app:
        return fail("version: could not find APP_VERSION in version.py")
    if not iss:
        return fail("version: could not find MyAppVersion in installer.iss")
    if app.group(1) != iss.group(1):
        return fail(
            "version: version.py APP_VERSION=%s != installer.iss MyAppVersion=%s"
            % (app.group(1), iss.group(1))
        )
    notes.append("version.py and installer.iss agree on %s" % app.group(1))


def check_requirements_parse():
    """A hand-edited requirements file that no longer parses fails at install
    time for a contributor, not here, where it is cheap to notice."""
    seen = 0
    for rel in ("requirements.txt", "requirements.lock"):
        for lineno, raw in enumerate(_read(rel).splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            seen += 1
            # Not a full PEP 508 parser (that would need `packaging`, and this
            # script stays stdlib-only): assert the shapes this project uses -
            # a bare name, a pinned name, a direct URL reference, or an
            # environment marker.
            ok = re.match(
                r"^[A-Za-z0-9][A-Za-z0-9._-]*"          # project name
                r"(\[[A-Za-z0-9,._-]+\])?"              # optional extras
                r"\s*(@\s*\S+|[=<>!~]=\s*\S+)?"         # URL or version pin
                r"\s*(;\s*.+)?$",                       # optional marker
                line,
            )
            if not ok:
                fail("requirements: %s:%s unparseable: %r" % (rel, lineno, line))
    notes.append("validated %d requirement lines" % seen)


def check_update_repo_is_not_the_retired_one():
    """v1.1.4 and earlier polled TopGradeTech/rhema-releases, which is now
    retired and read-only. Pointing a new build back at it would publish
    updates into a repo nothing new listens to."""
    text = _read("update_mixin.py")
    match = re.search(r'GITHUB_RELEASES_REPO\s*=\s*["\']([^"\']+)["\']', text)
    if not match:
        return fail("update: could not find GITHUB_RELEASES_REPO")
    if match.group(1).endswith("/rhema-releases"):
        return fail(
            "update: GITHUB_RELEASES_REPO still points at the retired "
            "rhema-releases repo (%s)" % match.group(1)
        )
    notes.append("update checker targets %s" % match.group(1))


def check_no_personal_contact_details():
    """The app ships to end users and the repo is public; a personal address
    baked into a constant is not something to rediscover after the fact."""
    pattern = re.compile(r"zachary\.price|topgradetel\.com", re.I)
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [
            d for d in dirnames
            if d not in {".git", ".venv", "build", "dist", "__pycache__", "logs"}
        ]
        for name in filenames:
            if not name.endswith((".py", ".md", ".iss", ".spec", ".txt", ".lock", ".yml")):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    for lineno, line in enumerate(handle, 1):
                        if pattern.search(line):
                            fail(
                                "privacy: %s:%s contains a personal address"
                                % (os.path.relpath(path, ROOT), lineno)
                            )
            except OSError:
                continue
    notes.append("scanned for personal contact details")


def main():
    for check in (
        check_syntax,
        check_version_sync,
        check_requirements_parse,
        check_update_repo_is_not_the_retired_one,
        check_no_personal_contact_details,
    ):
        check()

    for note in notes:
        print("  ok   %s" % note)
    if failures:
        print("")
        for failure in failures:
            print("  FAIL %s" % failure)
        print("\n%d check(s) failed" % len(failures))
        return 1
    print("\nall smoke checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
