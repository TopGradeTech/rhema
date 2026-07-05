import speech_recognition as sr
import time
import re


class TranslationMixin:
    def _active_text_translation_provider(self):
        return self._normalize_text_translation_provider(
            getattr(self, "text_translation_provider", "none")
        )

    def _translate_with_local_nllb(
        self,
        text,
        model_name=None,
        device=None,
        source_lang=None,
        target_lang=None,
        max_chars=None,
        cache_dir=None,
    ):
        source_text = (text or "").strip()
        if not source_text:
            return "", 0
        model_name = (
            str(model_name or self.local_nllb_model_name or "").strip()
            or self.LOCAL_NLLB_DEFAULT_MODEL_NAME
        )
        device_setting = self._normalize_local_nllb_device(
            device if device is not None else self.local_nllb_device
        )
        cache_dir = self._normalize_optional_directory(
            self.local_nllb_cache_dir if cache_dir is None else cache_dir
        )
        max_chars = self._coerce_int_range(
            self.local_nllb_max_chars if max_chars is None else max_chars,
            self.LOCAL_NLLB_DEFAULT_MAX_CHARS,
            250,
            20000,
        )
        src_lang = self._resolve_local_nllb_source_lang(source_lang)
        tgt_lang = self._resolve_local_nllb_target_lang(target_lang)
        chunks = self._split_local_nllb_chunks(source_text, max_chars)
        if not chunks:
            return "", 0
        started = time.time()
        try:
            tokenizer, model, torch_module, resolved_device = (
                self._get_local_nllb_components(
                    model_name=model_name,
                    device=device_setting,
                    cache_dir=cache_dir,
                    src_lang=src_lang,
                )
            )
            forced_bos_token_id = self._local_nllb_forced_bos_token_id(
                tokenizer,
                tgt_lang,
            )
            translated_chunks = []
            for chunk in chunks:
                self._set_local_nllb_tokenizer_source(tokenizer, src_lang)
                inputs = tokenizer(
                    chunk,
                    return_tensors="pt",
                    truncation=True,
                )
                inputs = {
                    key: value.to(resolved_device)
                    for key, value in inputs.items()
                }
                max_new_tokens = self._local_nllb_max_new_tokens(chunk)
                with torch_module.no_grad():
                    generated = model.generate(
                        **inputs,
                        forced_bos_token_id=forced_bos_token_id,
                        max_new_tokens=max_new_tokens,
                        num_beams=1,
                        do_sample=False,
                    )
                decoded = tokenizer.batch_decode(
                    generated,
                    skip_special_tokens=True,
                )
                translated_chunks.append((decoded[0] if decoded else "").strip())
            translated = self._join_local_nllb_outputs(translated_chunks)
        except sr.RequestError:
            raise
        except TimeoutError as exc:
            raise sr.RequestError(self.LOCAL_NLLB_TIMEOUT_MESSAGE) from exc
        except RuntimeError as exc:
            if self._is_local_nllb_cuda_oom_exception(exc):
                raise sr.RequestError(self.LOCAL_NLLB_CUDA_OOM_MESSAGE) from exc
            raise sr.RequestError(self.LOCAL_NLLB_FAILED_MESSAGE) from exc
        except Exception as exc:
            if str(exc) == self.LOCAL_NLLB_UNSUPPORTED_LANGUAGE_MESSAGE:
                raise
            raise sr.RequestError(self.LOCAL_NLLB_FAILED_MESSAGE) from exc
        elapsed_ms = int((time.time() - started) * 1000)
        meta = {
            "provider": "local_nllb",
            "model": model_name,
            "device": resolved_device,
            "source_lang": src_lang,
            "target_lang": tgt_lang,
            "input_chars": len(source_text),
            "output_chars": len(translated),
            "chunk_count": len(chunks),
            "duration_ms": elapsed_ms,
        }
        self._log_status(
            "Local NLLB translation: "
            f"model={model_name}, device={resolved_device}, source={src_lang}, "
            f"target={tgt_lang}, input_chars={len(source_text)}, "
            f"output_chars={len(translated)}, chunks={len(chunks)}, "
            f"duration_ms={elapsed_ms}"
        )
        self._trace_pipeline(
            "local_nllb_translation",
            source_text,
            **meta,
        )
        return translated, elapsed_ms

    def _get_local_nllb_components(self, model_name, device, cache_dir, src_lang):
        resolved_device = None
        try:
            torch_module, AutoModelForSeq2SeqLM, AutoTokenizer = (
                self._import_local_nllb_dependencies()
            )
        except sr.RequestError:
            raise
        except Exception as exc:
            raise sr.RequestError(self.LOCAL_NLLB_MISSING_DEPENDENCIES_MESSAGE) from exc
        resolved_device = self._resolve_local_nllb_device(torch_module, device)
        config = (model_name, resolved_device, cache_dir)
        with self.local_nllb_lock:
            if (
                self.local_nllb_tokenizer is not None
                and self.local_nllb_model is not None
                and self.local_nllb_model_config == config
            ):
                self._set_local_nllb_tokenizer_source(
                    self.local_nllb_tokenizer,
                    src_lang,
                )
                self.local_nllb_resolved_device = resolved_device
                return (
                    self.local_nllb_tokenizer,
                    self.local_nllb_model,
                    torch_module,
                    resolved_device,
                )
            kwargs = self._local_nllb_model_kwargs(
                cache_dir,
                local_files_only=True,
            )
            if resolved_device != "cpu":
                kwargs["dtype"] = torch_module.float16
            try:
                tokenizer = AutoTokenizer.from_pretrained(
                    model_name,
                    src_lang=src_lang,
                    **kwargs,
                )
                model = AutoModelForSeq2SeqLM.from_pretrained(model_name, **kwargs)
                model.to(resolved_device)
                model.eval()
                model.generation_config.max_length = None
                self._set_local_nllb_tokenizer_source(tokenizer, src_lang)
            except RuntimeError as exc:
                if self._is_local_nllb_cuda_oom_exception(exc):
                    raise sr.RequestError(self.LOCAL_NLLB_CUDA_OOM_MESSAGE) from exc
                raise sr.RequestError(self.LOCAL_NLLB_MODEL_UNAVAILABLE_MESSAGE) from exc
            except Exception as exc:
                raise sr.RequestError(self.LOCAL_NLLB_MODEL_UNAVAILABLE_MESSAGE) from exc
            self.local_nllb_tokenizer = tokenizer
            self.local_nllb_model = model
            self.local_nllb_model_config = config
            self.local_nllb_resolved_device = resolved_device
            return tokenizer, model, torch_module, resolved_device

    def _resolve_local_nllb_device(self, torch_module, device):
        device = self._normalize_local_nllb_device(device)
        cuda_available = False
        try:
            cuda_available = bool(torch_module.cuda.is_available())
        except Exception:
            cuda_available = False
        if device == "cpu":
            return "cpu"
        if cuda_available:
            return "cuda"
        return "cpu"

    def _set_local_nllb_tokenizer_source(self, tokenizer, src_lang):
        try:
            tokenizer.src_lang = src_lang
        except Exception:
            pass

    def _local_nllb_forced_bos_token_id(self, tokenizer, target_lang):
        token_id = tokenizer.convert_tokens_to_ids(target_lang)
        unknown_id = getattr(tokenizer, "unk_token_id", None)
        if token_id is None or token_id == unknown_id:
            raise ValueError(self.LOCAL_NLLB_UNSUPPORTED_LANGUAGE_MESSAGE)
        return token_id

    def _local_nllb_max_new_tokens(self, text):
        word_count = len(re.findall(r"\S+", text or ""))
        return max(64, min(1024, word_count * 3 + 32))

    def _split_local_nllb_chunks(self, text, max_chars):
        clean = str(text or "").strip()
        if not clean:
            return []
        max_chars = max(250, int(max_chars or self.LOCAL_NLLB_DEFAULT_MAX_CHARS))
        chunks = []
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", clean) if p.strip()]
        for paragraph in paragraphs:
            chunks.extend(self._split_local_nllb_paragraph(paragraph, max_chars))
        return [chunk for chunk in chunks if chunk.strip()]

    def _split_local_nllb_paragraph(self, paragraph, max_chars):
        if len(paragraph) <= max_chars:
            return [paragraph]
        sentence_candidates = re.split(r"(?<=[.!?])\s+", paragraph)
        chunks = []
        current = ""
        for sentence in sentence_candidates:
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) > max_chars:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(self._split_local_nllb_by_length(sentence, max_chars))
                continue
            candidate = f"{current} {sentence}".strip() if current else sentence
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = sentence
        if current:
            chunks.append(current)
        return chunks

    def _split_local_nllb_by_length(self, text, max_chars):
        remaining = str(text or "").strip()
        chunks = []
        while len(remaining) > max_chars:
            split_at = remaining.rfind(" ", 0, max_chars + 1)
            if split_at < max_chars // 2:
                split_at = max_chars
            chunk = remaining[:split_at].strip()
            if chunk:
                chunks.append(chunk)
            remaining = remaining[split_at:].strip()
        if remaining:
            chunks.append(remaining)
        return chunks

    def _join_local_nllb_outputs(self, chunks):
        return " ".join(chunk.strip() for chunk in chunks if chunk and chunk.strip()).strip()

    def _is_local_nllb_cuda_oom_exception(self, exc):
        message = str(exc or "").lower()
        return "out of memory" in message and ("cuda" in message or "gpu" in message)

    def _translate_text(self, text):
        return self._translate_with_local_nllb(text)

    def _translate_and_display(self, text, started_at=None, latency_meta=None):
        self._update_translation_status()
        self._trace_pipeline(
            "translation_input",
            text,
            translation_enabled=self.translation_enabled,
        )
        display_meta = self._build_translation_display_meta(started_at, latency_meta)
        translated = self._translate_for_display_safe(
            text,
            display_meta=display_meta,
        )
        if not translated:
            self._trace_pipeline(
                "translation_output_empty",
                "",
                translation_enabled=self.translation_enabled,
            )
            self._record_chunk_latency(
                started_at,
                latency_meta=display_meta,
                rendered_at=time.time(),
            )
            self.update_status(self.STATUS_LISTENING)
            return
        self._trace_pipeline(
            "translation_output",
            translated,
            translation_enabled=self.translation_enabled,
            text_translation_provider=display_meta.get("text_translation_provider"),
            translate_openai_ms=display_meta.get("translate_openai_ms"),
            translate_provider_ms=display_meta.get("translate_provider_ms"),
        )
        if self.translation_enabled:
            self._log_translated_text(
                self._translation_log_source_text(text, display_meta),
                translated,
                source_lang=(self._effective_source_lang() or "").strip().lower(),
                target_lang=(self._effective_target_lang() or "").strip().lower(),
                speech_engine=(self.speech_engine or "").strip().lower(),
                text_translation_provider=display_meta.get("text_translation_provider"),
                pretranslated=bool(display_meta.get("pretranslated")),
                translate_openai_ms=display_meta.get("translate_openai_ms"),
                translate_provider_ms=display_meta.get("translate_provider_ms"),
                stt_confidence=display_meta.get("stt_confidence"),
            )
        self._enqueue_finalized_output(translated, latency_meta=display_meta)
        self.update_status(self.STATUS_LISTENING)

    def _update_translation_status(self):
        if not self.translation_enabled:
            self.update_status("Transcribing...")
            return
        if (
            self._active_text_translation_provider() == "local_nllb"
            and not self._local_nllb_ready_for_translation()
        ):
            self._maybe_report_local_nllb_not_ready()
            return
        self.update_status("Translating...")

    def _build_translation_display_meta(self, started_at=None, latency_meta=None):
        display_meta = dict(latency_meta or {})
        display_meta["queued_at"] = started_at
        display_meta.setdefault("translate_openai_ms", None)
        display_meta.setdefault("translate_provider_ms", None)
        display_meta.setdefault(
            "text_translation_provider",
            self._active_text_translation_provider(),
        )
        display_meta.setdefault("stt_confidence", None)
        display_meta.setdefault("chunk_seconds", None)
        display_meta.setdefault("overlap_words", 0)
        return display_meta

    def _translate_for_display_safe(
        self,
        text,
        display_meta=None,
    ):
        try:
            translated = self._translate_for_display(
                text,
                display_meta=display_meta,
            )
            if bool((display_meta or {}).get("local_nllb_unavailable_passthrough")):
                return (translated or "").strip()
            cleaned = self._apply_translation_cleanup_steps(translated)
            return self._guard_translation_output_language(
                text,
                cleaned,
                display_meta=display_meta,
            )
        except Exception as exc:
            self.update_status(f"Translation error: {exc}")
            self._trace_pipeline("translation_error", text, error=str(exc))
            return self._translation_error_fallback_text(text, display_meta=display_meta)

    def _translation_error_fallback_text(self, text, display_meta=None):
        return (text or "").strip()

    def _translation_log_source_text(self, text, display_meta=None):
        for key in ("translation_source_text", "source_text"):
            value = (display_meta or {}).get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return (text or "").strip()

    def _guard_translation_output_language(self, original_text, translated_text, display_meta=None):
        return (translated_text or "").strip()

    def _translate_for_display(self, text, display_meta=None):
        if not self.translation_enabled:
            return text
        translation_source = text
        if display_meta is not None:
            display_meta["translation_source_text"] = translation_source
        if (
            self._active_text_translation_provider() == "local_nllb"
            and not self._local_nllb_ready_for_translation()
        ):
            if display_meta is not None:
                display_meta["translate_openai_ms"] = 0
                display_meta["translate_provider_ms"] = 0
                display_meta["local_nllb_unavailable_passthrough"] = True
                display_meta["text_translation_provider"] = "local_nllb"
            self._maybe_report_local_nllb_not_ready()
            self._trace_pipeline(
                "local_nllb_translation_skipped",
                translation_source,
                reason="not_ready",
                nllb_status=self.nllb_status,
            )
            return translation_source
        translated_candidate, translate_ms = self._translate_text(translation_source)
        if not self.translation_enabled:
            # Toggle changed while request was in-flight.
            self._trace_pipeline(
                "translation_result_discarded_toggle_off",
                translated_candidate,
            )
            if display_meta is not None:
                display_meta["translate_openai_ms"] = 0
                display_meta["translate_provider_ms"] = 0
            return text
        if display_meta is not None:
            display_meta["translate_openai_ms"] = translate_ms
            display_meta["translate_provider_ms"] = translate_ms
            display_meta["text_translation_provider"] = (
                self._active_text_translation_provider()
            )
        return translated_candidate

    def _apply_translation_cleanup_steps(self, translated_text):
        translated = (translated_text or "").strip()
        translated = self.apply_custom_vocabulary(translated)
        if self.translation_enabled and self._effective_target_lang().startswith("en"):
            translated = self.apply_spanish_bible_name_map(translated)
        translated = self.format_scripture_refs(translated)
        if self._is_spanish_output_mode():
            translated = self._normalize_spanish_text(translated)
        translated = self.clean_text_spacing(translated)
        return self._sanitize_model_text(translated, suppress_repeated_noise=False)

    def _is_spanish_output_mode(self):
        if self.translation_enabled:
            return self._effective_target_lang().startswith("es")
        return self._effective_source_lang().startswith("es")

    def _normalize_spanish_text(self, text):
        text = (text or "").strip()
        if not text:
            return text

        # Common contractions in Spanish.
        text = re.sub(
            r"\b([Dd])e\s+([Ee])l\b",
            lambda m: "Del" if m.group(1).isupper() else "del",
            text,
        )
        text = re.sub(
            r"\b([Aa])\s+([Ee])l\b",
            lambda m: "Al" if m.group(1).isupper() else "al",
            text,
        )

        # Correct a common literal translation artifact: "No un/una..." -> "No es un/una..."
        text = re.sub(
            r"(^|[.!?]\s+)([Nn])o\s+(un(?:a|os|as)?)\b",
            lambda m: f"{m.group(1)}{'No' if m.group(2).isupper() else 'no'} es {m.group(3)}",
            text,
        )

        # Add opening punctuation when a sentence has only closing punctuation.
        text = self._add_missing_opening_mark(text, "?", "¿")
        text = self._add_missing_opening_mark(text, "!", "¡")

        # Spanish punctuation spacing.
        text = re.sub(self.PUNCTUATION_SPACING_PATTERN, r"\1", text)
        text = re.sub(r"([¿¡])\s+", r"\1", text)
        text = re.sub(r"([,.;:!?])(?![\s\"')\]»”]|$)", r"\1 ", text)
        text = re.sub(r"\s{2,}", " ", text)
        return text.strip()

    def _add_missing_opening_mark(self, value, closing_mark, opening_mark):
        if closing_mark not in value:
            return value
        chunks = value.split(closing_mark)
        if len(chunks) == 1:
            return value
        rebuilt = []
        for chunk in chunks[:-1]:
            rebuilt.append(
                self._normalize_spanish_chunk_opening_mark(
                    chunk,
                    closing_mark,
                    opening_mark,
                )
            )
        rebuilt.append(chunks[-1])
        return "".join(rebuilt)

    def _normalize_spanish_chunk_opening_mark(self, chunk, closing_mark, opening_mark):
        if not chunk:
            return closing_mark
        tail_has_opening = opening_mark in chunk[chunk.rfind(".") + 1 :]
        if tail_has_opening:
            return chunk + closing_mark
        boundary = max(chunk.rfind(". "), chunk.rfind("! "), chunk.rfind("? "))
        start = boundary + 2 if boundary >= 0 else 0
        lead = re.match(r'[\s"\'(\[{]*', chunk[start:])
        lead_len = len(lead.group(0)) if lead else 0
        insert_at = start + lead_len
        if re.search(r"[^\W\d_]", chunk[insert_at:], flags=re.UNICODE):
            chunk = chunk[:insert_at] + opening_mark + chunk[insert_at:]
        return chunk + closing_mark

