# Rhema

A Windows desktop application for real-time speech transcription and translation, with adjustable text size and a fullscreen output window. Built for practical use translating live speech (e.g. sermons, meetings), with Bible-specific vocabulary mapping and scripture reference formatting.

Speech recognition and translation both run **entirely on your machine** — no audio or transcript text is sent to any external service. Whisper supports 99 source languages and NLLB-200 supports 200 target languages, so any of those pairs can be configured; the defaults and the Bible-vocabulary features are tuned for Spanish/English use.

## Download

Most people want the installer, not the source:

**[Download the latest release](https://github.com/TopGradeTech/rhema/releases/latest)** — run `Rhema-Setup.exe`.

It installs per-user, so no administrator rights or UAC prompt are needed (useful on managed church or event AV laptops). Python is not required. Once installed, the app updates itself via **About > Check for Updates**.

The rest of this README is for running from source or contributing.

## Architecture

- Speech-to-text: [RealtimeSTT](https://github.com/KoljaB/RealtimeSTT), a local library that owns audio capture, dual VAD (voice activity detection), and a two-pass faster-whisper model pipeline (a fast "realtime" preview model plus an accurate "final" model). Runs entirely on-device — no cloud STT.
- Translation: local NLLB-200 (Meta), via the `transformers` library. Runs on CPU or GPU; no cloud translation.
- UI: Tkinter + ttkbootstrap, with a controller window (settings) and a separate fullscreen output window.

## Installation

1. Install Python 3.11 or higher.
2. Ubuntu dependencies (for audio + Tk), if not on Windows:
   ```
   sudo apt update
   sudo apt install -y python3-tk portaudio19-dev
   ```
3. If you plan to run RealtimeSTT or Local NLLB on an NVIDIA GPU, install the NVIDIA driver, CUDA Toolkit 12.x, and cuDNN 9.x for CUDA 12 first. CPU mode does not require these GPU libraries.
4. The Local NLLB model may download the first time it is used. After it is cached, it can run offline.
5. Install dependencies (this pulls RealtimeSTT from our fork via git, so `git`
   must be on your PATH — see the note in `requirements.txt` for why the fork is
   required rather than optional):
   ```
   pip install -r requirements.txt
   ```
   To instead reproduce the exact dependency set a released build was made from,
   use `pip install -r requirements.lock` and read that file's header first — the
   pinned `torch` is a CUDA build that is not on the default PyPI index.
6. Run the app:
   ```
   python main.py
   ```

## Speech-to-Text: RealtimeSTT

RealtimeSTT runs two faster-whisper models locally:

- A small, fast model for live preview text as you speak.
- A larger, accurate model that finalizes each utterance once you pause.

Settings (**File > Options > Transcription**) include the source language, the final and realtime model sizes, device (CPU/GPU), and Silero VAD sensitivity. Post-speech silence timing is deliberately not exposed: RealtimeSTT recalculates it dynamically within ~200ms of speech starting, so a user-facing slider had no lasting effect.

**Hardware Autodetect** (File > Hardware Autodetect) picks model sizes to match your available VRAM. It also runs by itself on first launch, or whenever your hardware stops matching the saved settings.

## Local Translation with NLLB-200

The app translates transcripts locally using Meta's NLLB-200 distilled 600M model. NLLB is a text translation model — it does not perform speech recognition and does not restore punctuation.

Recommended model: `facebook/nllb-200-distilled-600M`

Recommended Spanish to English settings:

- Model: `facebook/nllb-200-distilled-600M`
- Device: `Auto`
- Source language: `spa_Latn`
- Target language: `eng_Latn`

Hardware notes:

- GPU is recommended but not required.
- Practical GPU target: 4-6 GB VRAM.
- CPU mode is possible but slower.
- The model may download the first time Local NLLB is used. After it is cached, it can run offline.

Privacy note: all speech recognition and translation happens on your machine. No audio or transcript text is sent to an external API.

## Logging

- `Normal` writes status/error and finalized output logs.
- `Debug` adds pipeline trace logs.
- `Evaluation` adds raw transcribed/translated comparison logs.
- `Full` enables all logs.

## Usage

The app opens two windows: **Rhema Controller** (a preview of the output plus status, Pause, and Toggle Fullscreen) and **Rhema** (the fullscreen output shown to your audience, on whichever monitor you select). All settings live in **File > Options**.

- Speak into your microphone.
- The app transcribes your speech and, if translation is enabled, translates it into the target language you selected.
- Adjust text size, colours, and the number of visible lines in Options > Display.
- Click "Toggle Fullscreen" to move the output window in and out of fullscreen.
- Optionally enable autostart with Windows from Options > Advanced.

## Video Overlay (OBS Virtual Camera)

Video Feed mode draws OBS's Virtual Camera behind the captions, so the
output window shows exactly what's live on your stream with translated
text overlaid on top. This requires OBS Studio to be running and its
Virtual Camera started before you enable Video Feed in this app.

### 1. Install OBS Studio

Download and install OBS Studio (free) from https://obsproject.com if you
don't already have it.

### 2. Set up your OBS scene

Build your normal stream scene in OBS (camera, slides, whatever you send
to YouTube/Facebook Live). Video Feed mode mirrors whatever OBS is
compositing as its "Program" output, so make sure the scene looks the way
you want it to appear behind the captions.

### 3. Start the Virtual Camera

In OBS, find the **Start Virtual Camera** button (bottom-right of the main
window, in the Controls dock). Click it. OBS now exposes itself as a
camera device (e.g. "OBS Virtual Camera") that other apps, including this
one, can open like any webcam.

Leave OBS running with the Virtual Camera started for the whole time you
want Video Feed mode active.

### 4. Confirm the Output Type is "Program"

Click the small cogwheel icon to the right of the **Start Virtual Camera**
button and check that **Output Type** is set to **Program** (this is the
default). This makes the Virtual Camera a pixel-accurate mirror of
whatever OBS is actually streaming, not a Preview or a single fixed
Scene/Source. If it's set to anything else, the video feed shown behind
captions won't match what's live.

### 5. Match OBS's output resolution to the output window

In OBS, go to **Settings > Video** and set **Output (Scaled) Resolution**
to exactly the resolution of the monitor the output window will be shown
on (e.g. 1920x1080). This app draws the Virtual Camera frame at native
size with no rescaling whenever it already matches the output window's
size, so matching it here gives you an exact, unscaled passthrough of
OBS's stream. If they don't match, the app still displays the feed
correctly, just resized to fit the window.

### 6. Enable Video Feed mode in this app

1. Open **File > Options** on the Rhema Controller window.
2. In the Display section, check **Show video feed behind captions**.
3. Under **Camera Device**, click **Refresh** to (re)scan for camera
   devices — do this after starting the OBS Virtual Camera, not before,
   or it won't be listed yet.
4. Select the entry named **OBS Virtual Camera** (the dropdown shows each
   device's actual name, not just a bare index).
5. Click **Apply**.

### Troubleshooting

- **Status shows "Camera N not found - start OBS Virtual Camera first"**:
  OBS's Virtual Camera isn't running. Go back to OBS and click **Start
  Virtual Camera**, then click **Refresh** in this app's settings.
- **Status shows "Camera feed lost"**: OBS was closed, the Virtual Camera
  was stopped, or the device was disconnected while the app was reading
  from it. Restart the Virtual Camera in OBS and re-select the device.
- **Video behind captions doesn't match the live stream**: check the
  Output Type via the cogwheel next to **Start Virtual Camera** and make
  sure it's set to **Program** (see step 4).
- **Video looks soft/rescaled**: the status line shows the resolution
  actually being served by OBS — if it doesn't match the output window's
  resolution, set OBS's Output (Scaled) Resolution to match (see step 5)
  for an exact passthrough instead of a resized copy.
- **Dropdown still shows "Camera N" with no name**: the friendly-name
  lookup (via `pygrabber`) couldn't run — reinstall dependencies with
  `pip install -r requirements.txt`, or as a fallback try each entry and
  check the status text for a resolution match to confirm you picked the
  Virtual Camera and not a physical webcam.

## Dependencies

See `requirements.txt` for the authoritative list; the headline ones are:

- `RealtimeSTT` — speech recognition. Installed from [our fork](https://github.com/TopGradeTech/RealtimeSTT/tree/faster-whisper-engine-options) rather than PyPI, which is **required, not optional**: the app relies on that branch's `engine_options` pass-through to load models strictly from the local cache. Stock upstream accepts the option and silently ignores it, so on upstream the app quietly contacts Hugging Face on every launch. `requirements.txt` explains this in full.
- `faster-whisper`, `torch`, `ctranslate2` — the model runtime. `torch` is CPU-only by default; see the GPU note under Installation.
- `transformers`, `sentencepiece` — NLLB-200 translation.
- `silero-vad` — bundles the VAD models locally so no download is needed at runtime.
- `opencv-python`, `pillow` — Video Feed mode and the Controller's preview thumbnail.
- `pygrabber` (Windows only) — real camera names in the Camera Device dropdown instead of bare indices.
- `ttkbootstrap`, `speechrecognition`, `pyaudio` — UI theming and audio device handling.
- `tkinter` — system package on Ubuntu (`python3-tk`); bundled with Python on Windows.

`requirements.lock` records the exact set each release was built from. Read its header before using it — the pinned `torch` is a CUDA build that is not on the default PyPI index.

Note: you may need to install PyAudio manually on some systems. See https://people.csail.mit.edu/hubert/pyaudio/

## Feature requests, questions, and bugs

- **Feature requests and ideas** → [GitHub Discussions, Ideas category](https://github.com/TopGradeTech/rhema/discussions/categories/ideas). The app links here directly from **About > Feature Request**.
- **Questions** → [Discussions, Q&A](https://github.com/TopGradeTech/rhema/discussions/categories/q-a).
- **Bugs** → [open an issue](https://github.com/TopGradeTech/rhema/issues). Please include your Rhema version (About > About Rhema), whether STT/translation were set to CPU or GPU, and the relevant log from the `logs` folder in the install directory.

## License

Rhema is free software licensed under the **[GNU General Public License v3.0](LICENSE)**.

Copyright (C) 2026 Zachary Price

You may use, study, modify, and redistribute it. If you distribute a modified version, you must release your changes under the GPL as well, so the software stays free for the people who receive it.

All bundled dependencies are permissively licensed (MIT, Apache-2.0, BSD) and compatible with the GPL. Note that the NLLB-200 **model weights**, which the app downloads at runtime rather than shipping, carry their own licence from Meta — check its terms before commercial use.

## Development notes

- There is no automated test suite. `python scripts/smoke_check.py` runs the same fast checks CI does (syntax across all files, version file sync, requirements parsing); it is stdlib-only and installs nothing. Real verification means running the app and speaking into it.
- Architecture and conventions are documented in `CLAUDE.md`, which is written as onboarding for both human and AI contributors.
- Rhema was built collaboratively with [Claude Code](https://claude.com/claude-code); commits co-authored by Claude are marked as such in the git history.
