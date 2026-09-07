# -*- coding: utf-8 -*-
"""Subtitle script detection — Arabic vs Latin, stdlib only.

Arabic and Moroccan-Darija subtitles were historically forced through the
English wav2vec2 alignment model, which destroys word-level timing for
Arabic text. These helpers decide, from the subtitle text itself, whether an
external subtitle file should be aligned as Arabic (``ar``) or with the
default/forced alignment language (``en``).

The module deliberately imports nothing outside the standard library so it
stays importable in minimal environments (CI, worker scripts, tests) where
torch/whisperx are not installed.
"""

# Arabic-script blocks: Arabic U+0600..U+06FF (incl. Arabic-Indic digits
# and punctuation) and Arabic Supplement U+0750..U+077F (extra letters used
# by Quranic and some African orthographies).

_ARABIC_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
)


def _is_arabic_char(char):
    code = ord(char)
    return any(start <= code <= end for start, end in _ARABIC_RANGES)


def _is_latin_char(char):
    # ASCII Latin letters only — sufficient to tell English (or Latin-script
    # Darija, which the English model can at least attempt) apart from
    # Arabic-script text.
    return ("a" <= char <= "z") or ("A" <= char <= "Z")


def detect_text_script(text):
    """Classify the dominant script of ``text``.

    Counts Arabic-script characters (U+0600-U+06FF and U+0750-U+077F) and
    Latin letters; ``ratio = arabic / (arabic + latin)``. Returns:

    * ``'ar'``    — ratio >= 0.35 (Arabic-script dominant, incl. Darija);
    * ``'en'``    — ratio <= 0.10 with at least one Latin letter;
    * ``'mixed'`` — both scripts present and neither dominates;
    * ``'none'``  — no Arabic or Latin letters at all (numbers, punctuation,
      emojis, empty text).

    A one-line English subtitle inside a mostly-Arabic file (or vice-versa)
    lands in ``'mixed'`` rather than being silently forced either way.
    """
    if not isinstance(text, str) or not text.strip():
        return "none"
    arabic = 0
    latin = 0
    for char in text:
        if _is_arabic_char(char):
            arabic += 1
        elif _is_latin_char(char):
            latin += 1
    total = arabic + latin
    if total == 0:
        return "none"
    ratio = arabic / total
    if ratio >= 0.35:
        return "ar"
    if ratio <= 0.10:
        return "en"
    return "mixed"


def choose_alignment_language(subtitle_text, forced_default="en"):
    """Pick the WhisperX alignment language for an external subtitle file.

    Arabic-script subtitles (Arabic / Moroccan Darija) return ``'ar'`` so the
    aligner uses the Arabic wav2vec2 model instead of being forced through the
    English one. Every other script returns ``forced_default`` (``'en'``), so
    existing English/Latin-script behavior is unchanged.
    """
    if detect_text_script(subtitle_text) == "ar":
        return "ar"
    return forced_default
