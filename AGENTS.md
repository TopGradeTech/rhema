# AGENTS.md

This file documents how to work in this repo for humans and automation (Codex).

## Project Summary
Python desktop app that captures live audio, transcribes speech, optionally translates, and renders large text in a fullscreen output window. The UI is a Tkinter controller window with settings and a preview.

## Key Files
- `main.py`: All core logic and UI (single-file app).
- `settings.json`: Persisted user settings (auto-written on Apply).
- `requirements.txt`: Python dependencies.

## Quick Start
1. Create and activate a virtual environment.
2. Install dependencies: `pip install -r requirements.txt`
3. Run: `python main.py`

## Speech Engines
- `OpenAI (gpt-4o-mini-transcribe)` uses the OpenAI API and requires `openai_api_key`.
- `Local (faster-whisper)` runs locally on GPU/CPU and downloads models as needed. No OpenAI calls for STT.

## Translation
- Translation can be enabled/disabled in settings.
- If enabled and an OpenAI key is present, OpenAI is used first with Google as fallback.

## Settings Persistence
- Settings are stored in `settings.json` and written when Apply is clicked.
- Monitor selection is persisted by device and screen origin, not just index.

## UI Notes
- The output window is fullscreen and moves to the selected monitor.
- The controller window (settings) is a separate monitor selection.
- The Apply button lights up only when there are pending changes.

## Common Tasks
- Add a new setting: define Tk variable in `open_settings`, apply in `_apply_*_vars`, and persist in `save_settings`/`load_settings`.
- Update output rendering: look for `render_text`, `_update_line_items`, and font sizing helpers.
- Add a new engine: add UI in `_build_api_section`, apply in `_apply_api_vars`, and implement in `_recognize_audio`.

## Testing
- Minimal: `python -m py_compile main.py`
- Full: run the app and verify UI/monitor behavior and live transcription.

## Conventions
- Keep changes in `main.py` focused; avoid large refactors unless requested.
- Use ASCII-only edits unless the file already uses Unicode.
- Prefer small, safe changes that preserve user settings.

## Safety
- Do not commit or share API keys. If one appears in files, notify the user.
- Avoid destructive git commands unless explicitly requested.
