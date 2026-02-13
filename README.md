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
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
4. Set your OpenAI key as an environment variable:
   - PowerShell:
     ```
     $env:OPENAI_API_KEY="sk-..."
     ```
   - bash/zsh:
     ```
     export OPENAI_API_KEY="sk-..."
     ```
   - Optional local file (not committed): copy `.env.example` to `.env.local` and load it in your shell before running.
5. Run the app:
   ```
   python main.py
   ```

## Usage

- Speak into your microphone
- The app will transcribe your speech and translate it to English
- Use the slider to adjust text size
- Click "Toggle Fullscreen" for fullscreen mode

## Dependencies

- speechrecognition
- googletrans
- pyaudio
- tkinter (system package on Ubuntu)

Note: You may need to install PyAudio manually on some systems. See https://people.csail.mit.edu/hubert/pyaudio/
