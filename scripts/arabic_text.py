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
