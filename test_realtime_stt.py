# Rhema - live speech transcription and translation, run locally.
# Copyright (C) 2026 Zachary Price
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""
Standalone RealtimeSTT test — run this script directly to verify the library
works in your environment BEFORE integrating with the main app.

Requirements:
  pip install "RealtimeSTT[faster-whisper]"
  Python 3.11+

Run:
  python test_realtime_stt.py

Speak into your microphone. Each completed utterance will be printed.
Press Ctrl+C to exit.
"""

from RealtimeSTT import AudioToTextRecorder


def on_text(text):
    print(f"[final]  {text}")


def on_realtime(text):
    print(f"[live]   {text}", end="\r", flush=True)


if __name__ == "__main__":
    print("Initialising RealtimeSTT — this may take a moment to load models...")
    print("Speak into your microphone. Ctrl+C to quit.\n")

    recorder = AudioToTextRecorder(
        model="large-v3",
        realtime_model_type="tiny",
        enable_realtime_transcription=True,
        on_realtime_transcription_stabilized=on_realtime,
        language="en",
        device="cuda",
        compute_type="int8",
        silero_sensitivity=0.4,
        post_speech_silence_duration=0.6,
    )

    try:
        while True:
            recorder.text(on_text)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        recorder.stop()
