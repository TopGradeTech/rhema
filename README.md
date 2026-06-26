# Python Translation App

A simple Python application for real-time speech-to-text translation, similar to LocalVocal, with adjustable text size and fullscreen mode.

## Features

- Real-time speech recognition
- Automatic translation to English
- Adjustable text size
- Fullscreen toggle
- Simple GUI interface

## Installation

1. Install Python 3.7 or higher
2. Ubuntu dependencies (for audio + Tk):
   ```
   sudo apt update
   sudo apt install -y python3-tk portaudio19-dev
   ```
3. If you plan to use `Local (faster-whisper)` on an NVIDIA GPU, install the NVIDIA driver, CUDA Toolkit 12.x, and cuDNN 9.x for CUDA 12 first. CPU mode does not require these GPU libraries.
4. If you plan to use `Local (Omnilingual sidecar)`, install Docker Desktop with WSL 2 and run the third-party `syaffers/omniasr-server` Docker sidecar separately.
5. If you plan to use `Local NLLB-200 distilled 600M` translation, the model may download the first time it is used. After it is cached, it can run offline.
6. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
7. Set your OpenAI key as an environment variable:
   - PowerShell:
     ```
     $env:OPENAI_API_KEY="sk-..."
     ```
   - bash/zsh:
     ```
     export OPENAI_API_KEY="sk-..."
     ```
   - Optional local file (not committed): copy `.env.example` to `.env.local` and load it in your shell before running.
8. Run the app:
   ```
   python main.py
   ```

## Speech Engines

- `OpenAI (gpt-4o-transcribe)` uses the OpenAI API and requires `OPENAI_API_KEY`.
- `Local (faster-whisper)` can run on CPU or NVIDIA GPU.
- NVIDIA GPU mode requires the NVIDIA driver, CUDA Toolkit 12.x, and cuDNN 9.x for CUDA 12 installed on the machine.
- `Local (Omnilingual sidecar)` uses the third-party `syaffers/omniasr-server` Docker image through a local HTTP API. The app does not include or run Meta Omnilingual ASR directly. Default URL: `http://127.0.0.1:8765`; transcription endpoint: `/v1/audio/transcriptions`.

## Optional Local Translation with NLLB-200 distilled 600M

The app can translate transcripts locally using Meta's NLLB-200 distilled 600M model. This is useful with `Local (Omnilingual sidecar)`, where ASR produces a source-language transcript and Local NLLB translates that text afterward.

NLLB is a text translation model. It does not perform speech recognition and does not restore punctuation. The app does not bundle model files.

Recommended model: `facebook/nllb-200-distilled-600M`

Recommended Spanish to English settings:

- Text translation provider: `Local NLLB-200 distilled 600M`
- Model: `facebook/nllb-200-distilled-600M`
- Device: `Auto`
- Source language: `spa_Latn`
- Target language: `eng_Latn`

Hardware notes:

- GPU is recommended but not required.
- Practical GPU target: 4-6 GB VRAM.
- CPU mode is possible but slower.
- The model may download the first time Local NLLB is used. After it is cached, it can run offline.

Privacy note: when using Local NLLB, transcript text is processed on your machine and is not sent to a hosted translation API.

## Logging

- `Normal` writes status/error and finalized output logs.
- `Debug` adds pipeline trace logs and Omnilingual debug WAV files.
- `Evaluation` adds raw transcribed/translated comparison logs.
- `Full` enables all logs.

## Usage

- Speak into your microphone
- The app will transcribe your speech and translate it to English
- Use the slider to adjust text size
- Click "Toggle Fullscreen" for fullscreen mode

## Dependencies

- speechrecognition
- pyaudio
- requests
- ttkbootstrap
- faster-whisper
- pydub
- transformers
- sentencepiece
- torch
- tkinter (system package on Ubuntu)

Note: You may need to install PyAudio manually on some systems. See https://people.csail.mit.edu/hubert/pyaudio/
