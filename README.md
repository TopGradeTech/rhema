# Rhema

A Windows desktop application for real-time speech transcription and translation, with adjustable text size and a fullscreen output window. Built for practical use translating live speech (e.g. sermons, meetings) from Spanish to English, with Bible-specific vocabulary mapping and scripture reference formatting.

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
5. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
6. Run the app:
   ```
   python main.py
   ```

## Speech-to-Text: RealtimeSTT

RealtimeSTT runs two faster-whisper models locally:

- A small, fast model for live preview text as you speak.
- A larger, accurate model that finalizes each utterance once you pause.

Settings include the final/realtime model size, device (CPU/GPU), Silero VAD sensitivity, and silence/recording-length thresholds.

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

- Speak into your microphone.
- The app transcribes your speech and translates it to English.
- Use the slider to adjust text size.
- Click "Toggle Fullscreen" for fullscreen mode.
- Optionally enable autostart with Windows from the settings window.

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

1. Open the app's Settings window.
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

- speechrecognition
- pyaudio
- ttkbootstrap
- faster-whisper
- transformers
- sentencepiece
- torch
- RealtimeSTT
- tkinter (system package on Ubuntu)

Note: You may need to install PyAudio manually on some systems. See https://people.csail.mit.edu/hubert/pyaudio/
