# Project instructions

**Read `ARCHITECTURE.md` before making changes.** It is the single source of
truth for this project: how the code is laid out, the commands to build and
verify it, the conventions to follow, and the release process.

This file is a pointer rather than a copy, so it cannot drift out of step with
the document it refers to. It uses plain prose instead of an import directive
because import syntax is specific to individual tools, and this filename is the
vendor-neutral convention that several of them read.

The points most often got wrong, all covered in more detail there:

- Work on the `development` branch. Do not push to `main`; open a pull request
  from `development` instead.
- Do not add co-author trailers or tool attribution to commits, pull request
  bodies, or release notes.
- Run `python scripts/smoke_check.py` before pushing. It needs no dependencies.
- RealtimeSTT is installed from a **fork**, and that is load-bearing rather than
  incidental — stock upstream silently loses offline model loading. See the note
  in `requirements.txt`.
- There is no automated test suite. Real verification means running the app and
  speaking into it.
