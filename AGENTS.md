# AGENTS.md

This file documents how to work in this repo for humans and automation (Codex).

## Project Summary
Windows desktop app that captures live audio, transcribes speech locally with RealtimeSTT, translates it locally with NLLB-200, and renders large text in a fullscreen output window. The UI is a Tkinter controller window with settings and a preview.

## Key Files
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
- `requirements.txt`: Python dependencies.

## Quick Start
1. Create and activate a virtual environment (Python 3.11+).
2. If using GPU mode for RealtimeSTT or Local NLLB, install the NVIDIA driver, CUDA Toolkit 12.x, and cuDNN 9.x for CUDA 12 first. CPU mode does not require these GPU libraries.
3. Install dependencies: `pip install -r requirements.txt`
4. Run: `python main.py`

## Speech-to-Text
- RealtimeSTT (`realtime_stt_mixin.py`) is the only speech engine. It runs a small "realtime" faster-whisper model for live preview text and a larger "final" model for accurate finalized output, both locally — no cloud STT calls.
- Device (CPU/GPU) and model sizes are configurable in settings.

## Translation
- Local NLLB-200 (`translation_mixin.py`) is the only translation engine, running locally via `transformers`. No cloud translation calls.

## Settings Persistence
- Settings are stored in `settings.json` and written when Apply is clicked.
- Monitor selection is persisted by device and screen origin, not just index.

## UI Notes
- The output window is fullscreen and moves to the selected monitor.
- The controller window (settings) is a separate monitor selection.
- The Apply button lights up only when there are pending changes.

## Common Tasks
- Add a new setting: define the Tk variable in `settings_ui_mixin.py`, apply it in the relevant `_apply_*_vars` method, and persist it in `settings_mixin.py` (`save_settings`/`load_settings`).
- Update output rendering: look for `render_text`, `_update_line_items`, and font sizing helpers in `display_mixin.py`.

## Testing
- Minimal: `python -m py_compile main.py`
- Full: run the app and verify UI/monitor behavior and live transcription/translation.
- `test_realtime_stt.py` is a standalone script to verify RealtimeSTT works in your environment before integrating changes.

## Conventions
- Prefer small, safe changes that preserve user settings.
- Use ASCII-only edits unless the file already uses Unicode.
- Keep changes scoped to the mixin that owns the concern; avoid large refactors unless requested.

## Safety
- Do not commit or share API keys. If one appears in files, notify the user.
- Avoid destructive git commands unless explicitly requested.
