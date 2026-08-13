# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Summary
**Rhema** (named after ῥῆμα, Greek for "spoken word") is a Windows desktop app that captures live audio, transcribes speech locally with RealtimeSTT, translates it locally with NLLB-200, and renders large text in a fullscreen output window ("Rhema"), controlled from a separate settings window ("Rhema Controller"). The UI is Tkinter (+ttkbootstrap). Built for practical use translating live speech (e.g. sermons, meetings) from Spanish to English, with Bible-specific vocabulary mapping and scripture reference formatting.

All speech recognition and translation runs on-device — no cloud STT/translation calls.

## Commands

```
# Setup (Python 3.11+; repo currently developed on 3.12)
pip install -r requirements.txt

# Reproduce the exact set a shipped build was made from, instead of resolving
# fresh (see requirements.lock's own header for the torch/CUDA caveat):
pip install -r requirements.lock
# Regenerate that lockfile after intentionally changing dependencies:
python scripts/gen_lock.py

# GPU mode for RealtimeSTT and/or Local NLLB requires, before installing deps:
#   NVIDIA driver + CUDA Toolkit 12.x + cuDNN 9.x for CUDA 12
# CPU mode needs none of that.

# Run the app
python main.py

# Minimal correctness check (no test suite/framework is configured)
python -m py_compile main.py

# Manual/full verification: run the app and verify UI/monitor behavior and
# live transcription/translation end-to-end.

# Verify RealtimeSTT works in this environment before integrating changes
python test_realtime_stt.py

# Pure STT->console output check, no filtering/translation (needs `rich`)
python test_realtime_output.py
```

There is no linter, formatter, or pytest suite configured in this repo — don't invent one.

## Architecture

The app is split into `main.py` plus a set of mixin modules, all mixed into `TranslationApp` in `main.py`. Attribute/method access works across mixin boundaries via Python MRO — when adding a setting or helper, put it in the mixin that owns that concern, not in `main.py`, unless it's a constant, part of `__init__`, or a core event handler.

- `main.py`: Constants, `__init__`, core event handlers, entry point.
- `logging_mixin.py`: Exception hooks, app data dir, log paths, log write methods.
- `settings_mixin.py`: load/save settings, normalize helpers, Windows startup registry.
- `monitor_mixin.py`: Multi-monitor enumeration, fullscreen, audio device enumeration.
- `settings_ui_mixin.py`: All settings window UI widgets and apply logic.
- `audio_capture_mixin.py`: Mic/loopback capture loop, queue, chunk autotuning.
- `realtime_stt_mixin.py`: RealtimeSTT integration (owns audio capture, VAD, and model scheduling while active).
- `transcription_mixin.py`: sentence buffer, display/translation worker orchestration.
- `translation_mixin.py`: Local NLLB-200 translation.
- `text_filter_mixin.py`: Bad words, hallucination filtering, custom vocab, scripture formatting, defaults.
- `display_mixin.py`: Word reveal, text rendering, audio level meter.
- `video_capture_mixin.py`: OBS Virtual Camera capture thread and the canvas render tick that draws it behind the captions.
- `update_mixin.py`: In-app "Check for Updates" — queries this repo's latest release and installs newer builds.
- `languages.py`: Display-name tables for all RealtimeSTT/Whisper language codes and all NLLB FLORES-200 codes, plus the reverse map between them. No logic — data only.
- `version.py`: Single source of truth for `APP_VERSION`.
- `tooltip.py`: Tooltip widget.
- `settings.json`: Persisted user settings (auto-written on Apply).

### Speech-to-Text
- RealtimeSTT (`realtime_stt_mixin.py`) is the only speech engine. It runs a small "realtime" faster-whisper model for live preview text and a larger "final" model for accurate finalized output, both locally.
- By default only finalized RealtimeSTT output is displayed — the original always-on interim preview was removed because it caused jerky re-wrap/reflow on every ~100ms partial update. An opt-in "Show live interim text" checkbox re-adds live text on a reserved bottom row that never re-wraps frozen lines: raw partials when translation is off, throttled NLLB translations of stabilized text when translation is on.
- Device (CPU/GPU) and model sizes are configurable in settings.
- Post-speech silence duration is not a user-facing setting: RealtimeSTT overwrites it dynamically (`_realtime_stt_adjust_silence`) within ~200ms of any speech, so it was cosmetic as a slider.
- **RealtimeSTT comes from our fork, and that is load-bearing — not a convenience.** `requirements.txt` points at the `faster-whisper-engine-options` branch of `TopGradeTech/RealtimeSTT`. `realtime_stt_mixin.py` passes `transcription_engine_options={"model": {"local_files_only": True}}` to keep startup fully offline; stock upstream *accepts* that argument but its faster_whisper adapter silently ignores it, so on upstream the app quietly reaches for Hugging Face on every launch and reports no error explaining why. If offline startup ever regresses, check which RealtimeSTT is actually installed first (`pip show realtimestt`). The fork is otherwise stock 1.0.2.

### Translation
- Local NLLB-200 (`translation_mixin.py`) is the only translation engine, running locally via `transformers`. NLLB is text-only — it does not perform speech recognition and does not restore punctuation.
- Translated finals are direct-committed to the display like untranslated finals (not routed through the word-by-word reveal queue), so translated and untranslated output feel equally instant.

### Settings Persistence
- Settings are stored in `settings.json`, written when Apply is clicked.
- Monitor selection is persisted by device and screen origin, not just index.
- The Advanced settings panel has been deliberately pruned over time — several former settings (noise gate, text-manipulation chunk/delay tuning, post-speech silence) were confirmed dead or non-load-bearing and hardcoded/removed rather than left as unused knobs. Don't reintroduce a setting without checking `TODO.md` history for why it may have been removed.

### UI Notes
- The output window is fullscreen and moves to the selected monitor.
- The controller window (settings) uses a separate monitor selection.
- The Apply button lights up only when there are pending changes.

## Common Tasks
- Add a new setting: define the Tk variable in `settings_ui_mixin.py`, apply it in the relevant `_apply_*_vars` method, and persist it in `settings_mixin.py` (`save_settings`/`load_settings`).
- Update output rendering: look for `render_text`, `_update_line_items`, and font sizing helpers in `display_mixin.py`.

## Releases / Versioning
The in-app "Check for Updates" (`update_mixin.py`) compares `version.py`'s `APP_VERSION` against the latest release tag on this repo, `TopGradeTech/rhema`. Bump the version **only when actually shipping a rebuilt installer**, not on every commit/push — ordinary commits (docs, in-progress code, TODO updates) should leave it alone. When you do ship:
1. Bump both `version.py`'s `APP_VERSION` and `installer.iss`'s `MyAppVersion` to the same value — they aren't linked by tooling, so keep them in sync manually. CI enforces this (`.github/workflows/smoke.yml`), so a mismatch fails the build rather than shipping.
2. Rebuild: `python -m PyInstaller --noconfirm --clean main.spec`, then `ISCC.exe installer.iss`.
3. Publish a GitHub release tagged `vX.Y.Z` with `dist/Rhema-Setup.exe` attached (`gh release create vX.Y.Z dist/Rhema-Setup.exe --repo TopGradeTech/rhema`) — installed copies won't see the update until this step happens, regardless of what's bumped locally.

**This repo must stay public.** The update check is unauthenticated by design (a token inside a distributed exe would be extractable), and GitHub's API answers 404 rather than 403 for anonymous reads of a private repo — so making it private makes every install silently believe it is up to date, with no error surfaced.

Releases up to and including v1.1.4 were published to a separate `TopGradeTech/rhema-releases` repo, which existed only because this one was private. It is retained read-only for those historical installers; nothing new goes there. Builds from v1.1.5 onward publish here. Note that installs of v1.1.4 and earlier poll the old repo, so they will not see releases published here — those were reinstalled manually.

## Conventions
- Prefer small, safe changes that preserve user settings.
- Use ASCII-only edits unless the file already uses Unicode.
- Keep changes scoped to the mixin that owns the concern; avoid large refactors unless requested.
- Do not commit or share API keys. If one appears in files, notify the user.
