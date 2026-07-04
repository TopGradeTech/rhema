# Python Translation App

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

## Dependencies

- speechrecognition
- pyaudio
- requests
- ttkbootstrap
- faster-whisper
- transformers
- sentencepiece
- torch
- RealtimeSTT
- tkinter (system package on Ubuntu)

Note: You may need to install PyAudio manually on some systems. See https://people.csail.mit.edu/hubert/pyaudio/
