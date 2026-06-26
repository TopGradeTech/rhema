import speech_recognition as sr
import tkinter as tk
from tkinter import messagebox
from tkinter import colorchooser
from tkinter import filedialog
from tkinter import font as tkfont
from threading import Thread, Lock, Event
import queue
import time
import re
import requests
import struct
import json
import pyaudio
from collections import deque, Counter
import os
import sys
import traceback
import io
import math
import statistics
import tempfile
import gc
import wave
import importlib.util
import ttkbootstrap as ttkb
from ttkbootstrap.constants import PRIMARY


class TranslationMixin:
    def _translate_with_openai(self, text):
        if not self.openai_api_key:
            raise ValueError(self.OPENAI_API_KEY_EMPTY_MESSAGE)
        url = "https://api.openai.com/v1/responses"
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.openai_translate_model,
            "input": self._build_openai_translation_prompt(text),
            "temperature": 0,
        }
        request_started = time.time()
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        request_ms = int((time.time() - request_started) * 1000)
        if response.status_code != 200:
            raise sr.RequestError(
                f"OpenAI API error {response.status_code}: {response.text}"
            )
        output_text = self._extract_openai_output_text(response.json())
        if not output_text:
            raise sr.RequestError("OpenAI API returned empty translation")
        return output_text, request_ms

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
                kwargs["torch_dtype"] = torch_module.float16
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
        provider = self._active_text_translation_provider()
        if provider == "none":
            return text, 0
        if provider == "local_nllb":
            return self._translate_with_local_nllb(text)
        return self._translate_with_openai(text)

    def _translate_and_display(self, text, started_at=None, latency_meta=None):
        self._update_translation_status()
        self._trace_pipeline(
            "translation_input",
            text,
            translation_enabled=self.translation_enabled,
        )
        display_meta = self._build_translation_display_meta(started_at, latency_meta)
        if self._should_drop_pretranslated_translation(
            text,
            started_at=started_at,
            display_meta=display_meta,
        ):
            return
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
                stt_openai_ms=display_meta.get("stt_openai_ms"),
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
        display_meta.setdefault("stt_openai_ms", None)
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

    def _should_drop_pretranslated_translation(
        self,
        text,
        started_at=None,
        display_meta=None,
    ):
        pretranslated = bool((display_meta or {}).get("pretranslated"))
        if self.translation_enabled or not pretranslated:
            return False
        # This payload came from Whisper direct-translation before the toggle changed.
        # Drop it to enforce "translation off" strictly.
        self._trace_pipeline(
            "translation_disabled_drop_pretranslated",
            text,
            translation_enabled=self.translation_enabled,
        )
        self._record_chunk_latency(
            started_at,
            latency_meta=display_meta,
            rendered_at=time.time(),
        )
        self.update_status(self.STATUS_LISTENING)
        return True

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
            cleanup_source = self._translation_cleanup_source_text(text, display_meta)
            cleaned = self._apply_translation_cleanup_steps(cleanup_source, translated)
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
        fallback = (text or "").strip()
        if not fallback:
            return fallback
        if not self._is_raw_faster_whisper_spanish_to_english():
            return fallback
        if bool((display_meta or {}).get("pretranslated")) and self._looks_like_english_text(
            fallback
        ):
            return fallback
        if self._looks_like_spanish_text(fallback):
            self._trace_pipeline(
                "translation_error_target_lang_suppressed",
                fallback,
                target_lang=(self._effective_target_lang() or "").strip().lower(),
            )
            return ""
        return fallback

    def _translation_cleanup_source_text(self, text, display_meta=None):
        meta_source = (display_meta or {}).get("translation_source_text")
        if isinstance(meta_source, str) and meta_source.strip():
            return meta_source.strip()
        return text

    def _translation_log_source_text(self, text, display_meta=None):
        for key in ("translation_source_text", "source_text"):
            value = (display_meta or {}).get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return (text or "").strip()

    def _guard_translation_output_language(self, original_text, translated_text, display_meta=None):
        out = (translated_text or "").strip()
        if not out:
            return out
        if not self._is_raw_faster_whisper_spanish_to_english():
            return out
        if not self._looks_like_spanish_text(out):
            return out
        fallback = ""
        if bool((display_meta or {}).get("pretranslated")):
            candidate = (original_text or "").strip()
            if candidate and self._looks_like_english_text(candidate):
                fallback = candidate
        if fallback:
            self._trace_pipeline(
                "translation_target_lang_guard_fallback",
                out,
                fallback_chars=len(fallback),
                target_lang=(self._effective_target_lang() or "").strip().lower(),
            )
            return fallback
        self._trace_pipeline(
            "translation_target_lang_guard_suppressed",
            out,
            target_lang=(self._effective_target_lang() or "").strip().lower(),
        )
        return ""

    def _translate_for_display(self, text, display_meta=None):
        pretranslated = bool((display_meta or {}).get("pretranslated"))
        if not self.translation_enabled:
            return text
        translation_source = text
        if pretranslated:
            should_retranslate, reason = self._should_force_retranslation_pretranslated(
                text,
                display_meta=display_meta,
            )
            if should_retranslate:
                translation_source = self._pretranslated_retranslation_source(
                    text,
                    display_meta=display_meta,
                )
                if not translation_source:
                    if display_meta is not None:
                        display_meta["translate_openai_ms"] = 0
                        display_meta["translate_provider_ms"] = 0
                    self._trace_pipeline(
                        "translation_retranslate_pretranslated_skipped",
                        text,
                        reason="missing_source_text",
                        translation_enabled=self.translation_enabled,
                    )
                    return text
                if display_meta is not None:
                    display_meta["translation_source_text"] = translation_source
                self._trace_pipeline(
                    "translation_retranslate_pretranslated",
                    text,
                    reason=reason,
                    source_chars=len(translation_source),
                    translation_enabled=self.translation_enabled,
                )
            else:
                if display_meta is not None:
                    display_meta["translate_openai_ms"] = 0
                    display_meta["translate_provider_ms"] = 0
                self._trace_pipeline(
                    "translation_skipped_pretranslated",
                    text,
                    translation_enabled=self.translation_enabled,
                )
                return text
        else:
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

    def _pretranslated_retranslation_source(self, text, display_meta=None):
        source_text = (display_meta or {}).get("source_text")
        if isinstance(source_text, str) and source_text.strip():
            return source_text.strip()
        # If the only available text already looks English, avoid sending it
        # through an es->en translator, which can flip it back to Spanish.
        if self._looks_like_english_text(text):
            return ""
        return (text or "").strip()

    def _should_force_retranslation_pretranslated(self, text, display_meta=None):
        if not bool((display_meta or {}).get("pretranslated")):
            return False, "not_pretranslated"
        if not self.translation_enabled:
            return False, "translation_disabled"
        if not bool((self.openai_api_key or "").strip()):
            return False, "no_api_key"
        if not self._effective_target_lang().startswith("en"):
            return False, "non_english_target"
        candidate = (text or "").strip()
        if not candidate:
            return True, "empty_text"
        if self._pretranslated_text_looks_unstable(candidate):
            return True, "unstable_text"
        confidence = (display_meta or {}).get("stt_confidence")
        try:
            conf_value = float(confidence)
        except Exception:
            conf_value = None
        if conf_value is not None and conf_value < 0.75:
            return True, "low_confidence"
        return False, "confidence_ok"

    def _pretranslated_text_looks_unstable(self, text):
        raw = (text or "").strip()
        if not raw:
            return True
        lowered = raw.lower()
        if "..." in raw or "\u2026" in raw:
            return True
        words = re.findall(self.UNICODE_WORD_PATTERN, lowered, flags=re.UNICODE)
        if not words:
            return True
        token_set = set(words)
        english_hits = len(token_set & self.english_common_words)
        spanish_hits = len(token_set & self.spanish_common_words)
        if spanish_hits >= 2 and english_hits == 0:
            return True
        if spanish_hits >= 2 and spanish_hits >= english_hits:
            return True
        return False

    def _is_raw_faster_whisper_spanish_to_english(self):
        if not self.translation_enabled:
            return False
        if (self.speech_engine or "").strip().lower() != "faster-whisper":
            return False
        source = (self._effective_source_lang() or "").strip().lower()
        target = (self._effective_target_lang() or "").strip().lower()
        return source.startswith("es") and target.startswith("en")

    def _apply_translation_cleanup_steps(self, source_text, translated_text):
        if (
            self._is_raw_faster_whisper_spanish_to_english()
            and self._looks_like_passthrough_translation(source_text, translated_text)
        ):
            raw_out = self._coerce_text(translated_text).strip()
            cleaned_out = self._sanitize_model_text(raw_out)
            if not cleaned_out:
                self._trace_pipeline(
                    "translation_raw_passthrough_suppressed",
                    raw_out,
                    source_lang=(self._effective_source_lang() or "").strip().lower(),
                    target_lang=(self._effective_target_lang() or "").strip().lower(),
                    speech_engine=(self.speech_engine or "").strip().lower(),
                )
                return ""
            self._trace_pipeline(
                "translation_raw_passthrough",
                cleaned_out,
                source_lang=(self._effective_source_lang() or "").strip().lower(),
                target_lang=(self._effective_target_lang() or "").strip().lower(),
                speech_engine=(self.speech_engine or "").strip().lower(),
                sanitized=(cleaned_out != raw_out),
            )
            return cleaned_out
        translated = self._strip_translation_wrappers(translated_text)
        if self._looks_like_nontranslation_response(source_text, translated):
            self._trace_pipeline(
                "translation_guard_fallback",
                translated,
                reason="non_translation_response",
            )
            translated = source_text
        translated = self.apply_custom_vocabulary(translated)
        if self.translation_enabled and self._effective_target_lang().startswith("en"):
            translated = self.apply_spanish_bible_name_map(translated)
        translated = self.format_scripture_refs(translated)
        if self._is_spanish_output_mode():
            translated = self._normalize_spanish_text(translated)
        translated = self.clean_text_spacing(translated)
        return self._sanitize_model_text(translated, suppress_repeated_noise=False)

    def _looks_like_passthrough_translation(self, source_text, translated_text):
        src = self._coerce_text(source_text).strip()
        out = self._coerce_text(translated_text).strip()
        if not src or not out:
            return False
        if src == out:
            return True
        src_norm = re.sub(r"\s+", " ", src.lower(), flags=re.UNICODE).strip()
        out_norm = re.sub(r"\s+", " ", out.lower(), flags=re.UNICODE).strip()
        return src_norm == out_norm

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
        text = self._add_missing_opening_mark(text, "?", "Â¿")
        text = self._add_missing_opening_mark(text, "!", "Â¡")

        # Spanish punctuation spacing.
        text = re.sub(self.PUNCTUATION_SPACING_PATTERN, r"\1", text)
        text = re.sub(r"([Â¿Â¡])\s+", r"\1", text)
        text = re.sub(r"([,.;:!?])(?![\s\"')\]Â»â€]|$)", r"\1 ", text)
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

