# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.1.5] - 2026-08-13

### Changed
- **Releases and updates moved to this repository.** Earlier versions checked a separate binaries-only repo (`TopGradeTech/rhema-releases`), which existed solely because this repo was private while installers still had to be publicly downloadable. With the source public there is one source of truth for code, issues, discussions, and releases. `rhema-releases` is retained read-only for the historical installers.
- **"Feature Request" now opens GitHub Discussions** (the *Ideas* category) instead of an email link, so requests are public, searchable, and can be upvoted rather than living in one inbox.

### Fixed
- The translation-latency reading in the Controller's status area was carried by two duplicate fields named after translation engines the app no longer uses; consolidated into one correctly-named field. No visible change to the reading itself.

### Notes
- **Installs of v1.1.4 and earlier will not see this or later updates automatically**, because they poll the retired `rhema-releases` repo. Those copies need a one-time manual reinstall from the installer attached to this release; afterwards updates resume normally.

## [1.1.4] - 2026-08-12

### Changed
- Hardware Autodetect now runs automatically only on first launch, or when detected hardware no longer matches saved settings — previously it ran, and showed its result dialog, on every startup.
- The Controller and Options windows are now genuinely responsive: paragraph text reflows to the window width and the Preview thumbnail scales with the window instead of staying a fixed size.

### Fixed
- A permission error where the post-update relaunch could leave the working directory at `C:\Windows\System32`, so RealtimeSTT's log file could not be opened.
- The "Toggle Fullscreen" button could be squeezed to an invisible sliver, and resizing the preview could feed back into a layout loop that progressively shrank the status area.

## [1.1.3] - 2026-08-12

### Fixed
- Tab navigation skipped buttons and dropdowns, which were silently excluded from keyboard focus.

### Added
- Arrow-key value cycling on all dropdown menus.
- The Options dialog remembers its size and maximized state between launches instead of always reopening maximized.

## [1.1.2] - 2026-08-11

### Changed
- Publisher name is now "Top Grade Tech"; internal references updated for the `TopGradeTelecom` → `TopGradeTech` account rename. No functional changes.

## [1.1.1] - 2026-08-11

### Fixed
- A "Windows could not find …" error popup immediately after a silent update finished installing. The installer relaunch is now a small temporary batch file rather than one fragile command-line string.

## [1.1.0] - 2026-08-11

### Added
- **File > Options dialog**: Display, Audio, Transcription, Translation, and Advanced moved out of the main Controller window, which now shows an enlarged Preview and the status area.
- **Light/Dark theme** setting (Options > Display), including dark window title bars.
- Restructured About menu (About Rhema, Check for Updates, Donate, Feature Request) and a rewritten Donate message.

### Changed
- The File/About menu bar stays hidden until startup finishes loading, rather than appearing with its items disabled.

## [1.0.3] - 2026-08-10

### Fixed
- The About menu would not open: its dropdown was a native popup being dismissed by the Controller window's global click-outside handler before it became visible. Replaced with a real top-left File menu.

## [1.0.2] - 2026-08-10

### Fixed
- "Check for Updates" dialogs appeared on the wrong monitor; they now parent to the Controller window rather than the fullscreen Output window.
- The app never reopened after a silent update. Relaunch is now handled deterministically instead of relying on Windows Restart Manager.

## [1.0.1] - 2026-08-10

### Added
- An About menu on the Controller window, with Check for Updates and Donate.

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
