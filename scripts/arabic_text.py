# -*- coding: utf-8 -*-
"""arabic_text — shared, stdlib-only Arabic/Latin text normalization helpers.

Canonical home of the orthography-unification and match-normalization logic
that ``scripts/create_viral_segments.py`` grew organically. The viral-segment
module re-exports these under its historical private names
(``_normalize_arabic_orthography`` / ``_normalized_match_text``) so existing
callers and tests keep working unchanged.

Rules enforced here (comparison only — display text is never modified):

* Hamza carriers أ إ آ ٱ → ا, hamza seats ؤ → و and ئ → ي, ta marbuta ة → ه.
* Tatweel (U+0640), tashkeel (U+064B..U+065F) and superscript alef (U+0670)
  are removed for matching.
* Alif maqsura ى is NOT folded into ي (على/علي stay distinct), and ي is
  untouched.
* Arabic-Indic (٠-٩) and Persian (۰-۹) digits translate to ASCII, and the
  Arabic decimal marks ٫/٬ become "." — used by both timestamp parsing and
  number-fact checks.
* Latin text passes through byte-identical (only lowercased by
  ``normalized_match_text``).
"""

from __future__ import annotations

import re

# Arabic orthography unification table (ord -> replacement, None = delete).
_ARABIC_ORTHOGRAPHY_TABLE = {
    0x0623: 0x0627,  # أ hamza above    -> ا
    0x0625: 0x0627,  # إ hamza below    -> ا
    0x0622: 0x0627,  # آ madda          -> ا
    0x0671: 0x0627,  # ٱ wasla          -> ا
    0x0624: 0x0648,  # ؤ hamza on waw   -> و
    0x0626: 0x064A,  # ئ hamza on yaa   -> ي
    0x0629: 0x0647,  # ة ta marbuta     -> ه
    0x0640: None,    # tatweel (stretch mark) — deleted
    0x0670: None,    # superscript alef — deleted
}
_ARABIC_ORTHOGRAPHY_TABLE.update({cp: None for cp in range(0x064B, 0x0660)})

# Arabic-Indic / Persian digits + Arabic decimal marks → ASCII.
_ARABIC_NUM_TRANSLATION = str.maketrans({
    **{ord(src): dst for src, dst in zip("٠١٢٣٤٥٦٧٨٩", "0123456789")},
    **{ord(src): dst for src, dst in zip("۰۱۲۳۴۵۶۷۸۹", "0123456789")},
    0x066B: ".",  # ٫ Arabic decimal separator
    0x066C: ".",  # ٬ Arabic thousands separator (used as decimal by LLMs)
})


def normalize_arabic_orthography(text) -> str:
    """Unify common Arabic spelling variants so matching is orthography-blind.

    Pure function: non-Arabic text passes through byte-identical.
    """
    if text is None:
        return ""
    return str(text).translate(_ARABIC_ORTHOGRAPHY_TABLE)


def normalize_digits(text) -> str:
    """Translate Arabic-Indic/Persian digits and Arabic decimal marks to ASCII."""
    if text is None:
        return ""
    return str(text).translate(_ARABIC_NUM_TRANSLATION)


def normalized_match_text(value) -> str:
    """Lowercase alphanumeric-only text for transcript/title alignment.

    Arabic orthography is unified first so spelling variants compare equal,
    while distinct words (على/علي) stay distinct. Latin output is identical
    to the historical pipeline.
    """
    return re.sub(
        r"[^\w\s]", "",
        normalize_arabic_orthography(str(value or "").lower()),
    ).strip()


def collapse_spaces(text) -> str:
    """Collapse repeated whitespace (incl. Arabic ZWSP runs) to single spaces."""
    return re.sub(r"[\s​]+", " ", str(text or "")).strip()


# ---------------------------------------------------------------------------
# v7.41 — three explicit normalization levels
# ---------------------------------------------------------------------------
# The pipeline needs different transformations for different jobs, and mixing
# them silently corrupted Arabic display text (names, numbers, brands) in the
# past. These three helpers name the levels explicitly:
#
#   * DISPLAY    — the visible transcript/title. Identity: never normalized.
#   * COMPARISON — orthography unified + digits ASCII + whitespace collapsed.
#                  Used for factual checks and title/transcript matching.
#   * SEARCH     — COMPARISON + lowercasing + punctuation stripped + tokenized.
#                  Used for indexing/lookup, never for display or storage.
#
# Only COMPARISON/SEARCH ever change digits or letters; DISPLAY is byte-exact.

RTL_MARK = "\u200f"           # RIGHT-TO-LEFT MARK
LTR_MARK = "\u200e"           # LEFT-TO-RIGHT MARK
_FIRST_STRONG_AR_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F]")


def to_display(text) -> str:
    """DISPLAY level: the original text, untouched (identity function).

    Titles and transcripts are shown/stored exactly as produced. Normalization
    at any other level must never be written back over this value.
    """
    return "" if text is None else str(text)


def to_comparison(text) -> str:
    """COMPARISON level: orthography-unified, digit-normalized, space-collapsed.

    Keeps punctuation and letter case so the result is still human-readable;
    use :func:`to_search` when an index key is needed. Intended for factual
    checks against the clip transcript, never for display.
    """
    return collapse_spaces(normalize_digits(normalize_arabic_orthography(text)))


def to_search(text) -> str:
    """SEARCH level: lowercase, punctuation-free, single-spaced comparison text."""
    lowered = str(text or "").lower()
    cleaned = re.sub(r"[^\w\s]", "", normalize_arabic_orthography(lowered))
    return collapse_spaces(normalize_digits(cleaned))


def search_tokens(text) -> list:
    """Tokenize at the SEARCH level (word-boundary-safe)."""
    return [token for token in to_search(text).split() if token]


def language_profile(text) -> dict:
    """Arabic/English letter balance and the dominant spoken language.

    Returns a dict with ``arabic``/``latin`` letter counts, their ratios, the
    strict script classification (``ar``/``en``/``mixed``/``none``) and
    ``dominant`` — the language to use for titles. For genuinely mixed speech
    ``dominant`` resolves to whichever script holds the majority (ties → the
    first strong Arabic letter/RTL context wins), as the spec requires; the
    raw ``mixed`` label stays available as ``script``.
    """
    arabic = latin = 0
    for char in str(text or ""):
        if not char.isalpha():
            continue
        if _FIRST_STRONG_AR_RE.match(char):
            arabic += 1
        elif char.isascii() or "\u00C0" <= char <= "\u024F":
            latin += 1
    total = arabic + latin
    if total == 0:
        script, dominant = "none", "none"
    else:
        ratio = arabic / float(total)
        if ratio >= 0.6:
            script, dominant = "ar", "ar"
        elif ratio <= 0.2:
            script, dominant = "en", "en"
        else:
            script = "mixed"
            dominant = "ar" if ratio >= 0.5 else "en"
    return {
        "arabic": arabic,
        "latin": latin,
        "arabic_ratio": round(arabic / total, 3) if total else 0.0,
        "latin_ratio": round(latin / total, 3) if total else 0.0,
        "script": script,
        "dominant": dominant,
    }


def dominant_language(text) -> str:
    """Title language for a clip: ``ar`` | ``en`` | ``none`` (mixed → majority)."""
    return language_profile(text)["dominant"]


def is_rtl(text) -> bool:
    """True when the first strong character is Arabic (RTL reading order)."""
    for char in str(text or ""):
        if _FIRST_STRONG_AR_RE.match(char):
            return True
        if char.isalpha():
            return False
    return False


def rtl_display(text, enabled=False) -> str:
    """Optionally wrap an RTL string in bidi isolates for display surfaces.

    Default off (``enabled=False``) returns the DISPLAY text byte-exact so
    stored titles/transcripts are never mutated; report generators that need
    correct visual ordering pass ``enabled=True`` and get FSI…PDI isolates
    only when the string is actually RTL.
    """
    value = to_display(text)
    if not enabled or not value or not is_rtl(value):
        return value
    return "\u2068" + value + "\u2069"  # FIRST STRONG ISOLATE … POP DIRECTIONAL ISOLATE
