# TODO

- [x] Make the NLLB-translated output feed as smooth/instant as the untranslated RealtimeSTT feed. Untranslated finals go straight to screen via `_realtime_stt_show` (realtime_stt_mixin.py) — one direct append + render. Translated finals instead go through the word-by-word reveal queue (`enqueue_text`/`reveal_next_word` in display_mixin.py), which feels jerky next to the instant path. Match the NLLB feed's display behavior to the direct-commit style used for non-translated text.
- [x] Create a checkbox to autostart with Windows.
- [x] Remove non-MME audio options.
