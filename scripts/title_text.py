# -*- coding: utf-8 -*-
"""
title_text — stdlib-only text helpers for title / publish-metadata policies.

Used by ``scripts/metadata_compliance.py`` (Arabic clickbait rules, title vs
caption script mismatch, emoji budget) and by ``scripts/upload_gate.py``
callers. Pure functions only: no file I/O, no third-party imports, no
network — safe to run inside the upload gate and unit tests.

Public API
----------
* ``is_arabic_script(text)``        — does the string contain any Arabic char
                                      (U+0600-06FF + U+0750-077F)?
* ``detect_text_script(text)``      — 'ar' | 'en' | 'mixed' | 'none'
* ``scripts_match(title, caption)`` — True when the two scripts are compatible
                                      for publishing (mixed tolerated, empty OK)
* ``count_emoji(text)``             — emoji code points (conservative ranges)
* ``strip_excess_emoji(text, max_emoji=1)`` — drop emoji past the budget
* ``fit_publish_title(text, limit=100)`` — longest word-boundary prefix of a
  publish title that fits `limit` chars, with a trailing '…' on truncation
"""

import re

# Arabic script detection ranges: U+0600-06FF (Arabic) + U+0750-077F
# (Arabic Supplement). Presentation forms (U+FB50+, U+FE70+) are excluded on
# purpose: real titles are normalized to the base block, and including them
# would only add false positives for a purely advisory detector.
_ARABIC_RE = re.compile("[\u0600-\u06FF\u0750-\u077F]")

# Latin letters: ASCII + Latin-1 Supplement + Latin Extended-A/B. Digits,
# punctuation and other scripts are ignored by detect_text_script().
_LATIN_RE = re.compile("[A-Za-z\u00C0-\u024F]")

# Conservative emoji ranges (code points, not grapheme clusters):
# U+1F300-U+1FAFF (Misc Symbols & Pictographs … Symbols for Suppl. A) and
# U+2600-U+27BF (Misc Symbols / Dingbats). Regional-indicator flags and ZWJ
# sequences are intentionally NOT counted individually — this is a coarse
# advisory counter, so it errs toward under-counting exotic sequences.
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")

# Variation Selector-16 (forces emoji presentation, e.g. U+2764 U+FE0F "❤️").
VARIATION_SELECTOR_16 = "\uFE0F"


# ---------------------------------------------------------------------------
# Arabic script detection
# ---------------------------------------------------------------------------

def is_arabic_script(text):
    """True when `text` contains at least one Arabic-script character."""
    return bool(text) and _ARABIC_RE.search(text) is not None


def _script_counts(text):
    """(arabic_letters, latin_letters, other_letters) among letter chars."""
    arabic = latin = other = 0
    for char in (text or ""):
        if not char.isalpha():
            continue  # digits, punctuation, spaces, emoji, marks: not letters
        if _ARABIC_RE.match(char):
            arabic += 1
        elif _LATIN_RE.match(char):
            latin += 1
        else:
            other += 1
    return arabic, latin, other


def detect_text_script(text):
    """Classify the dominant script of `text`.

    Ratio = Arabic letters / (Arabic + Latin letters) among *letter* chars:

    * 'ar'    — ratio >= 0.6 (mostly Arabic)
    * 'en'    — ratio <= 0.2 and at least one Latin letter (mostly English)
    * 'mixed' — anything between the two, or both scripts present
    * 'none'  — no Arabic or Latin letters at all (empty, digits, emoji,
                other scripts such as Cyrillic/CJK)
    """
    arabic, latin, _other = _script_counts(text)
    total = arabic + latin
    if total == 0:
        return "none"
    ratio = arabic / float(total)
    if ratio >= 0.6:
        return "ar"
    if ratio <= 0.2 and latin > 0:
        return "en"
    return "mixed"


def scripts_match(title, caption):
    """True when title/caption scripts are compatible for publishing.

    * Either side empty / 'none'  → True (nothing to contradict)
    * 'mixed' on either side      → True (bilingual text is tolerated)
    * 'ar' vs 'en' mismatch       → False (audience mismatch: the shipped
                                    title would be in the wrong language)
    """
    title_script = detect_text_script(title)
    caption_script = detect_text_script(caption)
    if title_script == "none" or caption_script == "none":
        return True
    if title_script == "mixed" or caption_script == "mixed":
        return True
    return title_script == caption_script


# ---------------------------------------------------------------------------
# Emoji budget helpers
# ---------------------------------------------------------------------------

def count_emoji(text):
    """Number of emoji code points in `text` (conservative ranges only)."""
    if not text:
        return 0
    return len(EMOJI_RE.findall(text))


def strip_excess_emoji(text, max_emoji=1):
    """Remove emoji beyond `max_emoji`; also drops U+FE0F variation selectors
    while cleaning. Returns `text` unchanged (same content) when it already
    fits the budget, so emoji-heavy but compliant titles are never touched.
    """
    if not text:
        return text
    if count_emoji(text) <= max_emoji:
        return text
    seen = 0
    kept = []
    for char in text:
        if char == VARIATION_SELECTOR_16:
            # Only meaningful next to an emoji; we are already dropping some,
            # so strip them all for a deterministic, clean result.
            continue
        if EMOJI_RE.match(char):
            if seen >= max_emoji:
                continue
            seen += 1
        kept.append(char)
    return "".join(kept)


# ---------------------------------------------------------------------------
# Publish-title length fitting
# ---------------------------------------------------------------------------

# "…" — single HORIZONTAL ELLIPSIS char (U+2026), one code point. Deliberately
# NOT three ASCII dots: they would cost 3 of the budget and look worse.
ELLIPSIS = "\u2026"

# Word separator for publish titles. Python's re `\s` matches the Unicode
# White_Space property, which does NOT include U+200B (ZERO WIDTH SPACE,
# category Cf) — yet ZWSP is used in the wild (Arabic social titles
# especially) as an invisible word separator. We extend the class explicitly
# so a ZWSP-joined phrase is treated as separate words, never hard-cut in
# half. U+200C/U+200D (ZWNJ/ZWJ) are glue characters, not separators, and are
# deliberately left out.
_TITLE_WS_RE = re.compile(r"[\s\u200B]+")


def fit_publish_title(text, limit=100):
    """Longest prefix of `text` that fits `limit` characters on a word boundary.

    YouTube caps titles at 100 characters and truncates silently; this helper
    is the single place where publish titles are shortened, so every platform
    path truncates the same way: never in the middle of a word.

    Rules
    -----
    * ``None`` → ``""``; whitespace is trimmed from both ends first.
    * ``len(text) <= limit`` → returned unchanged (never appends '…').
    * Otherwise the string is cut at the last whitespace boundary at or before
      ``limit - 1`` (any Unicode whitespace run incl. ZWSP U+200B; the run
      itself is dropped) and '…' is appended, so the total is <= limit.
    * No whitespace inside the budget (a single long token) → hard cut at
      ``limit - 1`` plus '…' (total == limit).
    * ``limit <= 0`` → ``""``.

    Notes
    -----
    * Arabic text works naturally: Python's ``len`` counts code points and
      Arabic letters are single BMP code points, so word boundaries behave
      exactly like Latin text.
    * Emoji are wide glyphs (or ZWJ sequences) but YouTube counts code points
      in its own title UI; we follow the code-point count for simplicity, so
      an emoji-heavy title may *look* short while consuming many code points.
    """
    if text is None or limit <= 0:
        return ""
    text = str(text).strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    # Room for one '…': the kept prefix may hold at most limit - 1 chars.
    last_boundary = -1
    for match in _TITLE_WS_RE.finditer(text):
        if match.start() <= limit - 1:
            last_boundary = match.start()
        else:
            break
    if last_boundary >= 0:
        return text[:last_boundary] + ELLIPSIS
    # Single token longer than the budget: no word boundary to honor.
    return text[: limit - 1] + ELLIPSIS
