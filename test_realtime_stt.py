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
