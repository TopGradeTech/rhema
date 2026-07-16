import re
from collections import Counter


class TextFilterMixin:
    def _refresh_bad_words(self):
        active = set()
        for words in (self.bad_words_by_lang or {}).values():
            active.update(words)
        self.active_bad_words = active

    def _effective_filter_lang(self):
        if self.translation_enabled:
            lang = (self._effective_target_lang() or "").strip().lower()
        else:
            lang = (self._effective_source_lang() or "").strip().lower()
        if "-" in lang:
            lang = lang.split("-", 1)[0]
        if "_" in lang:
            lang = lang.split("_", 1)[0]
        if lang == "auto":
            return ""
        return lang

    def _get_bad_words_for_output(self):
        vocab = self.bad_words_by_lang or {}
        if not vocab:
            return set()
        lang = self._effective_filter_lang()
        if lang and lang in vocab:
            return vocab.get(lang, set())
        if "en" in vocab:
            return vocab.get("en", set())
        merged = set()
        for words in vocab.values():
            merged.update(words)
        return merged

    def filter_bad_words(self, text):
        active_bad_words = self._get_bad_words_for_output()
        if not active_bad_words:
            return text
        filtered = text
        for word in sorted(active_bad_words, key=len, reverse=True):
            # A bare \bword\b only matches the word in exact isolation, so a
            # spoken inflection like "fucking"/"fuckers" (no boundary right
            # after the stem) slipped straight through. Allow a handful of
            # common, regular English suffixes after the stem - deliberately
            # not a bare "y" (would also catch innocuous words like "cocky").
            pattern = r"\b" + re.escape(word) + r"(?:s|es|ing|ed|er|ers)?\b"
            filtered = re.sub(pattern, "", filtered, flags=re.IGNORECASE)
        filtered = re.sub(self.PUNCTUATION_SPACING_PATTERN, r"\1", filtered)
        filtered = re.sub(r"\(\s+", "(", filtered)
        filtered = re.sub(r"\s+\)", ")", filtered)
        filtered = self.clean_text_spacing(filtered)
        # If bad-word removal leaves only punctuation/symbols, omit it entirely.
        if not re.search(self.UNICODE_WORD_CHAR_PATTERN, filtered, flags=re.UNICODE):
            return ""
        return filtered

    def apply_custom_vocabulary(self, text):
        vocabulary = self._get_custom_vocabulary_for_output()
        if not vocabulary:
            return text
        replacements = {v.lower(): v for v in vocabulary}
        def repl(match):
            key = match.group(0).lower()
            return replacements.get(key, match.group(0))
        pattern = r"\b(" + "|".join(re.escape(v) for v in vocabulary) + r")\b"
        return re.sub(pattern, repl, text, flags=re.IGNORECASE)

    def _get_custom_vocabulary_for_output(self):
        vocab_by_lang = self.custom_vocabulary_by_lang or {}
        if not vocab_by_lang:
            return []
        if self.translation_enabled:
            lang = (self._effective_target_lang() or "").lower()
        else:
            lang = (self._effective_source_lang() or "").lower()
        if "-" in lang:
            lang = lang.split("-", 1)[0]
        if lang and lang != "auto":
            if lang in vocab_by_lang:
                return vocab_by_lang.get(lang, [])
            if "en" in vocab_by_lang:
                return vocab_by_lang.get("en", [])
            return []
        if "en" in vocab_by_lang:
            return vocab_by_lang["en"]
        return next(iter(vocab_by_lang.values()), [])

    def clean_text_spacing(self, text):
        text = re.sub(r'([.!?])(?=[^\W\d_])', r'\1 ', text, flags=re.UNICODE)
        text = re.sub(self.PUNCTUATION_SPACING_PATTERN, r"\1", text)
        text = re.sub(r'\s{2,}', ' ', text)
        return text.strip()

    def _sanitize_model_text(self, text, suppress_repeated_noise=True):
        text = (text or "").strip()
        if not text:
            return ""
        if self._is_quoted_empty_text(text):
            return ""
        if self._is_symbol_only_text(text):
            return ""
        if not suppress_repeated_noise:
            return text
        if self._contains_url_like_text(text):
            return ""
        normalized = re.sub(
            self.NON_WORD_PATTERN,
            " ",
            text.lower(),
            flags=re.UNICODE,
        ).strip()
        if self._is_known_non_speech_placeholder(text, normalized):
            return ""
        if self._looks_like_known_stt_hallucination(text, normalized):
            return ""
        if self._looks_like_repeated_noise(normalized):
            return ""
        return text

    def _is_quoted_empty_text(self, text):
        # Some providers return a quoted empty string (e.g. "") for non-speech.
        if re.fullmatch(r'["\'`]+\s*["\'`]+', text):
            return True
        if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'", "`"):
            return not text[1:-1].strip()
        return False

    def _is_symbol_only_text(self, text):
        # Ignore symbol-only outputs so noise does not render as visible text.
        return not re.search(self.UNICODE_WORD_CHAR_PATTERN, text, flags=re.UNICODE)

    def _is_known_non_speech_placeholder(self, text, normalized):
        # OpenAI occasionally returns these exact non-speech placeholders.
        if re.fullmatch(r"(?i)no[.!?]+", text):
            return True
        # Keep STT filtering strict to avoid dropping legitimate user speech.
        return normalized in self.STT_STRICT_NOISE_MARKERS_NORMALIZED

    def _looks_like_known_stt_hallucination(self, text, normalized_text):
        raw = (text or "").strip().lower()
        norm = (normalized_text or "").strip().lower()
        if not raw:
            return False
        if "amara.org" in raw and ("subtit" in norm or "comunidad" in norm):
            return True
        has_link = bool(re.search(self.URL_SCHEME_PATTERN, raw))
        has_domain = self._contains_url_like_text(raw)
        if not (has_link or has_domain):
            return False
        if "please see review no" in norm:
            return True
        if "please see the complete disclaimer" in norm:
            return True
        if "for more information" in norm and has_link:
            return True
        if raw.count("https://") + raw.count("http://") >= 2 and (
            "please see" in norm or "disclaimer" in norm
        ):
            return True
        return False

    def _looks_like_repeated_noise(self, normalized_text):
        tokens = re.findall(
            self.UNICODE_WORD_PATTERN,
            normalized_text,
            flags=re.UNICODE,
        )
        if len(tokens) < 6:
            return False
        unique_ratio = len(set(tokens)) / float(len(tokens))
        if unique_ratio <= 0.35:
            return True
        counts = Counter(tokens)
        if counts and (max(counts.values()) / float(len(tokens))) >= 0.7:
            return True
        for unit_len in (1, 2, 3):
            if len(tokens) < unit_len * 3:
                continue
            if len(tokens) % unit_len != 0:
                continue
            unit = tokens[:unit_len]
            if unit * (len(tokens) // unit_len) == tokens:
                return True
        return False

    def format_scripture_refs(self, text):
        if not self.biblical_books:
            return text
        book_pattern = "|".join(re.escape(b) for b in self.biblical_books)
        pattern = (
            r"\b(" + book_pattern + r")\b"
            r"(?:\s+chapter)?\s+(\d{1,3})"
            r"(?:\s*[:]\s*|\s+verse\s+|\s+)(\d{1,3})\b"
        )
        def repl(match):
            book = match.group(1)
            chapter = match.group(2)
            verse = match.group(3)
            return f"{book} {chapter}:{verse}"
        return re.sub(pattern, repl, text, flags=re.IGNORECASE)

    def default_bad_words_en(self):
        return [
            "ass",
            "asshole",
            "bastard",
            "bitch",
            "boner",
            "cock",
            "crap",
            "cunt",
            "damn",
            "dick",
            "faggot",
            "fuck",
            "piss",
            "pussy",
            "shit",
            "slut",
            "tits",
            "whore",
        ]

    def default_bad_words_es(self):
        return [
            "cabrón",
            "coño",
            "cabron",
            "carajo",
            "chingar",
            "chingada",
            "chingado",
            "chingados",
            "culo",
            "follar",
            "gilipollas",
            "hostia",
            "joder",
            "maldita",
            "maldito",
            "mierda",
            "pendejo",
            "perra",
            "pinche",
            "polla",
            "puta",
            "puto",
            "verga",
        ]

    BIBLICAL_BOOK_NAMES = (
        "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy",
        "Joshua", "Judges", "Ruth", "1 Samuel", "2 Samuel",
        "1 Kings", "2 Kings", "1 Chronicles", "2 Chronicles",
        "Ezra", "Nehemiah", "Esther", "Job", "Psalms", "Proverbs",
        "Ecclesiastes", "Song of Solomon", "Isaiah", "Jeremiah",
        "Lamentations", "Ezekiel", "Daniel", "Hosea", "Joel", "Amos",
        "Obadiah", "Jonah", "Micah", "Nahum", "Habakkuk", "Zephaniah",
        "Haggai", "Zechariah", "Malachi", "Matthew", "Mark", "Luke",
        "John", "Acts", "Romans", "1 Corinthians", "2 Corinthians",
        "Galatians", "Ephesians", "Philippians", "Colossians",
        "1 Thessalonians", "2 Thessalonians", "1 Timothy",
        "2 Timothy", "Titus", "Philemon", "Hebrews", "James", "1 Peter",
        "2 Peter", "1 John", "2 John", "3 John", "Jude", "Revelation",
    )
    BIBLICAL_EXTRA_TERMS = (
        "Moses", "Abraham", "Isaac", "Jacob", "Joseph", "David",
        "Solomon", "Samuel", "Paul", "Peter", "Mary", "Jesus",
        "Jerusalem", "Bethlehem", "Nazareth", "Galilee", "Jericho",
        "Capernaum", "Judea", "Samaria", "Bethany", "Golgotha",
        "Calvary", "Mount Sinai", "Mount Zion", "Jordan",
        "Sea of Galilee", "Dead Sea", "Damascus", "Assyria", "Babylon",
        "Egypt", "Rome", "Antioch", "Corinth", "Ephesus", "Philippi",
        "Thessalonica", "Tarsus", "Patmos",
    )
    def default_biblical_books(self):
        return list(self.BIBLICAL_BOOK_NAMES)

    def default_biblical_terms(self):
        return list(dict.fromkeys(self.BIBLICAL_BOOK_NAMES + self.BIBLICAL_EXTRA_TERMS))

    def default_biblical_terms_es(self):
        return [
            "G\u00e9nesis", "\u00c9xodo", "Lev\u00edtico", "N\u00fameros", "Deuteronomio",
            "Josu\u00e9", "Jueces", "Rut", "1 Samuel", "2 Samuel",
            "1 Reyes", "2 Reyes", "1 Cr\u00f3nicas", "2 Cr\u00f3nicas",
            "Esdras", "Nehem\u00edas", "Ester", "Job", "Salmos", "Salmo",
            "Proverbios", "Eclesiast\u00e9s", "Cantar de los Cantares",
            "Isa\u00edas", "Jerem\u00edas", "Lamentaciones", "Ezequiel", "Daniel",
            "Oseas", "Joel", "Am\u00f3s", "Abd\u00edas", "Jon\u00e1s", "Miqueas",
            "Nah\u00fam", "Habacuc", "Sofon\u00edas", "Hageo", "Zacar\u00edas",
            "Malaqu\u00edas", "Mateo", "Marcos", "Lucas", "Juan", "Hechos",
            "Romanos", "1 Corintios", "2 Corintios", "G\u00e1latas",
            "Efesios", "Filipenses", "Colosenses", "1 Tesalonicenses",
            "2 Tesalonicenses", "1 Timoteo", "2 Timoteo", "Tito",
            "Filem\u00f3n", "Hebreos", "Santiago", "1 Pedro", "2 Pedro",
            "1 Juan", "2 Juan", "3 Juan", "Judas", "Apocalipsis",
            "Jes\u00fas", "Mois\u00e9s", "Abraham", "Isaac", "Jacob", "Jos\u00e9",
            "David", "Salom\u00f3n", "Samuel", "Pablo", "Pedro", "Mar\u00eda",
            "Jerusal\u00e9n", "Bel\u00e9n", "Nazaret", "Galilea", "Jeric\u00f3",
            "Capernaum", "Judea", "Samaria", "Betania", "G\u00f3lgota",
            "Calvario", "Monte Sina\u00ed", "Monte Si\u00f3n", "Jord\u00e1n",
            "Mar de Galilea", "Mar Muerto", "Damasco", "Asiria",
            "Babilonia", "Egipto", "Roma", "Antioqu\u00eda", "Corinto",
            "\u00c9feso", "Filipos", "Tesal\u00f3nica", "Tarso", "Patmos"
        ]

