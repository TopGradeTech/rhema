# TODO

- [x] Make the NLLB-translated output feed as smooth/instant as the untranslated RealtimeSTT feed. Untranslated finals go straight to screen via `_realtime_stt_show` (realtime_stt_mixin.py) — one direct append + render. Translated finals instead go through the word-by-word reveal queue (`enqueue_text`/`reveal_next_word` in display_mixin.py), which feels jerky next to the instant path. Match the NLLB feed's display behavior to the direct-commit style used for non-translated text.
- [x] Create a checkbox to autostart with Windows.
- [x] Remove non-MME audio options.
- [x] Clean up docs and remove orphaned references to removed engines/providers (Kroko, Omnilingual sidecar, standalone faster-whisper, OpenAI STT, OpenAI text translation) in README.md, AGENTS.md, and `.env.example` now that the app is RealtimeSTT + Local NLLB only.
- [x] Evaluate the Advanced settings panel (settings_ui_mixin.py) one setting at a time to determine which are never actually adjusted in practice and can be removed or hardcoded to their default value, to shrink the settings surface. Removed "Enable noise gate" / "Noise Cancellation (strength)" — confirmed dead, cosmetic-only meter marker that never gated real audio. Remaining Advanced settings (Chunk Size, Chunk Delay, Response Delay, Display Speed, Logging mode, Start-with-Windows, CUDA directory) are all actively consumed downstream.
- [ ] Buffer/paginate the output window so a long uninterrupted stretch of speech doesn't dump one large block of text that fills or overflows the display; needs a scrolling/windowing strategy for the transcript render (display_mixin.py).
- [x] Remove the live word-by-word interim preview entirely (realtime_stt_mixin.py). With translation off it caused visible jerkiness — every ~100ms partial update forced a full re-wrap/reflow of the display. Only finalized RealtimeSTT output is shown now, regardless of translation state.
- [ ] Turn the RealtimeSTT and Local NLLB model-name settings into dropdown menus, with recommended VRAM/system requirements shown next to each option.
- [ ] Rename the "API" settings section to "Speech-to-Text" or "Transcription" now that it only holds RealtimeSTT/NLLB model settings, not API keys.
- [ ] Move the Bad Words filter section into Advanced settings.
- [ ] Re-evaluate whether to keep post-speech silence, Silero sensitivity, Text Manipulation settings, Local GPU Runtime, etc. as user-facing settings, or hardcode/remove them.
- [ ] Add an alternative display option that overlays 1-2 lines of transcription/translation at the bottom of a live OBS/RTSP stream feed (or another researched-and-vetted streaming approach), similar to Facebook Live captions.
