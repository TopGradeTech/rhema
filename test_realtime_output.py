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
Pure RealtimeSTT → output test. No filtering, no preprocessing, no translation.
Exactly what comes out of the model goes on screen.

Committed sentences: alternating yellow / cyan.
Current partial:     bold white (updates at 10 fps).

Run:
    python test_realtime_output.py

Ctrl+C to quit.
"""

import os

SENTENCE_END_MARKS   = frozenset('.!?。')
SILENCE_MID_SENTENCE = 2.0
SILENCE_SENTENCE_END = 0.45
SILENCE_UNKNOWN      = 0.7

if __name__ == "__main__":
    from rich.console import Console
    from rich.live import Live
    from rich.text import Text
    from rich.panel import Panel
    from RealtimeSTT import AudioToTextRecorder

    console = Console()
    console.print("[bold]Initialising models — please wait...[/bold]")

    full_sentences = []
    recorder = None
    prev_partial = ""

    def build_display(partial):
        out = Text()
        for i, s in enumerate(full_sentences):
            out.append(s + " ", style="yellow" if i % 2 == 0 else "cyan")
        if partial:
            out.append(partial, style="bold white")
        return out

    def on_update(text):
        global prev_partial
        prev = prev_partial
        prev_partial = text
        if recorder:
            if text.endswith("..."):
                recorder.post_speech_silence_duration = SILENCE_MID_SENTENCE
            elif text and text[-1] in SENTENCE_END_MARKS and prev and prev[-1] in SENTENCE_END_MARKS:
                recorder.post_speech_silence_duration = SILENCE_SENTENCE_END
            else:
                recorder.post_speech_silence_duration = SILENCE_UNKNOWN
        live.update(Panel(build_display(text), title="RealtimeSTT", border_style="blue"))

    def on_final(text):
        global prev_partial
        if text:
            full_sentences.append(text)
        prev_partial = ""
        live.update(Panel(build_display(""), title="RealtimeSTT", border_style="green"))

    recorder = AudioToTextRecorder(
        model="large-v3",
        realtime_model_type="tiny",
        enable_realtime_transcription=True,
        on_realtime_transcription_update=on_update,
        device="cuda",
        compute_type="int8",
        silero_sensitivity=0.4,
        post_speech_silence_duration=SILENCE_UNKNOWN,
        min_length_of_recording=0.5,
        language="en",
    )

    live = Live(
        Panel(build_display(""), title="RealtimeSTT", border_style="blue"),
        console=console,
        refresh_per_second=10,
        screen=False,
    )

    console.print("[green]Ready — speak into your microphone. Ctrl+C to quit.[/green]")

    try:
        live.start()
        while True:
            recorder.text(on_final)
    except KeyboardInterrupt:
        pass
    finally:
        live.stop()
        try:
            recorder.abort()
            recorder.shutdown()
        except Exception:
            pass
        console.print("\n[bold]Stopped.[/bold]")
        os._exit(0)
