# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Summary
Windows desktop app that captures live audio, transcribes speech locally with RealtimeSTT, translates it locally with NLLB-200, and renders large text in a fullscreen output window. The UI is a Tkinter (+ttkbootstrap) controller window with settings and a preview, plus a separate fullscreen output window. Built for practical use translating live speech (e.g. sermons, meetings) from Spanish to English, with Bible-specific vocabulary mapping and scripture reference formatting.

All speech recognition and translation runs on-device — no cloud STT/translation calls.

## Commands

```
# Setup (Python 3.11+; repo currently developed on 3.12)
pip install -r requirements.txt

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
- `tooltip.py`: Tooltip widget.
- `settings.json`: Persisted user settings (auto-written on Apply).

### Speech-to-Text
- RealtimeSTT (`realtime_stt_mixin.py`) is the only speech engine. It runs a small "realtime" faster-whisper model for live preview text and a larger "final" model for accurate finalized output, both locally.
- Only finalized RealtimeSTT output is displayed — the live word-by-word interim preview was removed because it caused jerky re-wrap/reflow on every ~100ms partial update.
- Device (CPU/GPU) and model sizes are configurable in settings.
- Post-speech silence duration is not a user-facing setting: RealtimeSTT overwrites it dynamically (`_realtime_stt_adjust_silence`) within ~200ms of any speech, so it was cosmetic as a slider.

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

## Conventions
- Prefer small, safe changes that preserve user settings.
- Use ASCII-only edits unless the file already uses Unicode.
- Keep changes scoped to the mixin that owns the concern; avoid large refactors unless requested.
- Do not commit or share API keys. If one appears in files, notify the user.
