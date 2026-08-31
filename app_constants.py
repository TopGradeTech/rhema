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

from languages import WHISPER_LANGUAGES


class AppConstants:
    """Pure data shared by both the Tk app (TranslationApp, main.py) and the
    pywebview app (WebTranslationApp, main_webview.py) - line-count/inactivity
    defaults, NLLB message strings, regex patterns, the Windows-startup
    registry path/name, STT noise-filtering markers. Extracted verbatim from
    main.py's TranslationApp class body during the pywebview port so neither
    app has its own copy to drift out of sync with the other."""

    SCROLL_EVENTS = ("<MouseWheel>", "<Button-4>", "<Button-5>")
    CONFIGURE_EVENT = "<Configure>"
    STATUS_LISTENING = "Listening..."
    SHOW_LIST_LABEL = "Show list"
    HIDE_LIST_LABEL = "Hide list"
    NON_WORD_PATTERN = r"[^\w]+"
    UNICODE_WORD_PATTERN = r"[^\W_]+"
    UNICODE_WORD_CHAR_PATTERN = r"[^\W_]"
    UNICODE_LETTER_PATTERN = r"[^\W\d_]"
    SPANISH_WORD_PATTERN = r"[a-z\u00e1\u00e9\u00ed\u00f3\u00fa\u00fc\u00f1]+"
    SPANISH_DIACRITIC_PATTERN = r"[\u00e1\u00e9\u00ed\u00f3\u00fa\u00fc\u00f1\u00bf\u00a1]"
    TERMINAL_PUNCTUATION_PATTERN = r"[.!?][\"')\]]*$"
    TRAILING_EDGE_PUNCTUATION_PATTERN = r"(?:^(?:\.{2,}|[\s\-:;,])+|(?:\.{2,}|[\s\-:;,])+$)"
    PUNCTUATION_SPACING_PATTERN = r"\s+([,.;:!?])"
    URL_SCHEME_PATTERN = r"(?:https?://|www\.)"
    BARE_DOMAIN_PATTERN = r"\b(?:[a-z0-9-]+\.)+[a-z]{2,24}\b"
    COMMON_DOMAIN_SUFFIXES = frozenset(
        {
            "ai",
            "app",
            "biz",
            "ca",
            "co",
            "com",
            "de",
            "dev",
            "edu",
            "es",
            "fr",
            "gov",
            "info",
            "io",
            "me",
            "mx",
            "net",
            "org",
            "tv",
            "uk",
            "us",
        }
    )
    # "Number of lines to show" is two separate ranges depending on whether
    # the video overlay is on: fewer lines leave more of the video visible,
    # so its default/range are both lower than the plain-text default.
    LINES_NO_VIDEO_MIN = 4
    LINES_NO_VIDEO_MAX = 10
    LINES_NO_VIDEO_DEFAULT = 8
    LINES_VIDEO_MIN = 1
    LINES_VIDEO_MAX = 3
    LINES_VIDEO_DEFAULT = 2
    CLEAR_DISPLAY_INACTIVITY_MIN = 1
    CLEAR_DISPLAY_INACTIVITY_MAX = 60
    CLEAR_DISPLAY_INACTIVITY_DEFAULT = 3
    LOCAL_NLLB_DEFAULT_MODEL_NAME = "facebook/nllb-200-distilled-600M"
    LOCAL_NLLB_DEFAULT_TARGET_LANG = "eng_Latn"
    LOCAL_NLLB_DEFAULT_MAX_CHARS = 4000
    LOCAL_NLLB_UNSUPPORTED_LANGUAGE_MESSAGE = (
        "Local NLLB does not yet have a language-code mapping for this language."
    )
    LOCAL_NLLB_MODEL_UNAVAILABLE_MESSAGE = (
        "Local NLLB is not ready. Download and test the model before using "
        "Local NLLB translation."
    )
    LOCAL_NLLB_MISSING_DEPENDENCIES_MESSAGE = (
        "Local NLLB requires transformers, sentencepiece, and torch. Install "
        "dependencies, then select Local NLLB again."
    )
    LOCAL_NLLB_NOT_READY_MESSAGE = (
        "Local NLLB is not ready. Download and test the model before using "
        "Local NLLB translation."
    )
    LOCAL_NLLB_DOWNLOAD_CANCELED_MESSAGE = (
        "Local NLLB download canceled. Select another translation provider or "
        "download the model later."
    )
    LOCAL_NLLB_DOWNLOAD_FAILED_MESSAGE = (
        "Could not download Local NLLB model. Check your internet connection "
        "and click Download Local NLLB model to try again."
    )
    LOCAL_NLLB_CACHE_ERROR_MESSAGE = (
        "Could not write Local NLLB model cache. Check disk space and permissions."
    )
    LOCAL_NLLB_CUDA_OOM_MESSAGE = (
        "Local NLLB translation ran out of GPU memory. Try CPU mode or close other GPU applications."
    )
    LOCAL_NLLB_VERIFICATION_CUDA_OOM_MESSAGE = (
        "Local NLLB downloaded, but GPU loading ran out of memory. Try CPU mode."
    )
    LOCAL_NLLB_TIMEOUT_MESSAGE = (
        "Local NLLB translation timed out. Try a shorter transcript or CPU/GPU setting change."
    )
    LOCAL_NLLB_FAILED_MESSAGE = (
        "Local NLLB translation failed. The source transcript is still available."
    )
    # Auto-derived from languages.WHISPER_LANGUAGES: every 2-letter
    # RealtimeSTT/Whisper code that has a FLORES-200 equivalent (97 of the
    # 100 supported STT languages) maps to its NLLB code, so "NLLB source =
    # follow the selected STT source language" works for any of them, not
    # just the original English/Spanish pair. The dict below adds aliases
    # for full language names and alternate (3-letter/legacy) codes that
    # aren't derivable from the 2-letter code table.
    LOCAL_NLLB_LANG_ALIASES = {
        **{code: flores for code, (_name, flores) in WHISPER_LANGUAGES.items() if flores},
        "ara": "arb_Arab",
        "arabic": "arb_Arab",
        "arb": "arb_Arab",
        "chinese": "zho_Hans",
        "chinese simplified": "zho_Hans",
        "chinese traditional": "zho_Hant",
        "deu": "deu_Latn",
        "dutch": "nld_Latn",
        "eng": "eng_Latn",
        "english": "eng_Latn",
        "fra": "fra_Latn",
        "fre": "fra_Latn",
        "french": "fra_Latn",
        "ger": "deu_Latn",
        "german": "deu_Latn",
        "hin": "hin_Deva",
        "hindi": "hin_Deva",
        "ita": "ita_Latn",
        "italian": "ita_Latn",
        "japanese": "jpn_Jpan",
        "jpn": "jpn_Jpan",
        "kor": "kor_Hang",
        "korean": "kor_Hang",
        "nld": "nld_Latn",
        "por": "por_Latn",
        "portuguese": "por_Latn",
        "rus": "rus_Cyrl",
        "russian": "rus_Cyrl",
        "spa": "spa_Latn",
        "spanish": "spa_Latn",
        "ukr": "ukr_Cyrl",
        "ukrainian": "ukr_Cyrl",
        "zh-cn": "zho_Hans",
        "zh-hans": "zho_Hans",
        "zh-hant": "zho_Hant",
        "zh-tw": "zho_Hant",
        "zho": "zho_Hans",
    }
    LOGGING_MODE_OPTIONS = ("normal", "debug", "evaluation", "full")
    WINDOWS_RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
    WINDOWS_STARTUP_VALUE_NAME = "Rhema"
    STT_EDGE_NOISE_PREFIX_PATTERNS = (
        r"^\s*(?:thanks?|thank\s+you)(?:\s+(?:very|so)\s+much)?\s+for\s+(?:watching|listening)\b[\s.!?,:;\"']*",
        r"^\s*thank\s+you\s+very\s+much\b[\s.!?,:;\"']*",
        r"^\s*gracias(?:\s+muchas)?\b[\s.!?,:;\"']*",
        r"^\s*welcome\s+to\s+another\s+episode\s+of\s+(?:my\s+|the\s+)?channel\b[\s.!?,:;\"']*",
        r"^\s*welcome\s+(?:back\s+)?to\s+(?:my\s+|the\s+)?channel\b[\s.!?,:;\"']*",
        r"^\s*don'?t\s+forget\s+to\s+like\s+and\s+subscribe\b[\s.!?,:;\"']*",
    )
    STT_EDGE_NOISE_SUFFIX_PATTERNS = (
        r"[\s.!?,:;\"']*(?:thanks?|thank\s+you)(?:\s+(?:very|so)\s+much)?\s+for\s+(?:watching|listening)\s*$",
        r"[\s.!?,:;\"']*thank\s+you\s+very\s+much\s*$",
        r"[\s.!?,:;\"']*gracias(?:\s+muchas)?\s*$",
    )
    STT_STRICT_NOISE_MARKERS_NORMALIZED = frozenset(
        {
            "transcribe the audio verbatim",
            "transcribe audio verbatim",
            "context",
            "contexto",
            "there is no speech",
            "there is no speech in the audio",
            "no speech",
            "no speech detected",
            "there isn t any",
            "there is no doubt",
            "i don t know",
            "i do not know",
            "no i don t know",
            "no i do not know",
            "i m not sure",
            "text to translate",
            "texto a traducir",
            "please provide the text you need translated",
            "sure please provide the text you need translated",
            "of course please provide the text you need translated",
            "certainly please provide the text you would like translated",
            "sure please provide the text you want translated",
            "lo siento no puedo ayudar con eso",
            "i can t help with that",
            "i cannot help with that",
            "can t help with that",
            "cannot help with that",
            "i can t assist with that",
            "i cannot assist with that",
            "subtitulos realizados por la comunidad de amara org",
            "subtítulos realizados por la comunidad de amara org",
            "please see the complete disclaimer",
            "thank you for watching",
            "thanks for watching",
            "thank you for listening",
            "thanks for listening",
            "this video is for educational purposes only",
            "the audio is in english",
            "the audio is in spanish",
            "the audio is in english or spanish",
        }
    )
