# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] - 2026-07-24

### Added
- **Local, offline speech pipeline**: RealtimeSTT (dual-pass faster-whisper) for transcription and Local NLLB-200 for translation, replacing the earlier multi-engine setup (Kroko, OpenAI STT/translation) — everything now runs on-device.
- **Video overlay**: live captions rendered over an OBS Virtual Camera feed, with its own caption bar, opacity control, and line-count limits tuned for screen space.
- **Live interim captions**: near-real-time partial text (translated or raw) on a dedicated row that never disrupts the finalized display.
- **Packaged Windows installer**: `Rhema-Setup.exe` (Inno Setup, per-user install, no admin required) wrapping a PyInstaller build — no Python environment needed to run the app.
- Rebrand to **Rhema**, with a custom logo/icon.
- Hardware autodetect for STT/NLLB model sizing based on available VRAM, including explicit CUDA device selection.
- NLLB "Check for Updates" (re-fetches from Hugging Face without wiping local settings).

### Changed
- Display now does an instant broadcast-style roll-up (previously a word-by-word reveal queue) for consistent pacing between translated and untranslated text.
- Startup now runs fully offline once models are cached (no per-launch Hugging Face re-validation calls).
- NLLB source language now always derives from the Transcription section's source language — it can no longer silently diverge and force Whisper into the wrong transcription language.
- Settings UI reorganized: Hardware Autodetect moved near the top, video/non-video line-count controls no longer swap positions.

### Fixed
- RealtimeSTT child processes no longer survive a hung shutdown (force-killed on close).
- Video feed screen tearing, flicker, and a black Output Snapshot thumbnail (screen-capture now uses window-handle capture instead of a screen-coordinate grab).
- Mouse-wheel scroll no longer bleeds from an open language dropdown into the whole settings page.
- Double-UTF-8-encoded (mojibake) Spanish text.
- A PyInstaller-only fork-bomb from a missing `multiprocessing.freeze_support()` call, plus two other frozen-only packaging defects (editable-install discovery, a missing bundled asset).

### Removed
- Kroko ASR, OpenAI STT/translation, and other now-unused engine/provider code.
- Several Advanced settings confirmed dead in practice (noise gate, NLLB cache directory, redundant Retry Download button).
- Unused vendored `PyAudio` source tarball.
