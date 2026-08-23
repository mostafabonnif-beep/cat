"""Guard rails for locale files: coverage, cleanliness, placeholder parity."""

import json
import os
import re

import pytest

LOCALE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "i18n", "locale"
)
ARABIC_RE = re.compile(r"[\u0600-\u06FF]")


def _load(lang):
    with open(os.path.join(LOCALE_DIR, f"{lang}.json"), encoding="utf-8") as f:
        return json.load(f)


EN = _load("en_US")
AR = _load("ar_SA")


@pytest.mark.parametrize("lang", ["ar_SA", "pt_BR", "tr_TR"])
def test_locale_covers_all_english_keys(lang):
    data = _load(lang)
    missing = [k for k in EN if k not in data]
    assert missing == [], f"{lang} is missing {len(missing)} keys: {missing[:5]}"


@pytest.mark.parametrize("lang", ["ar_SA", "pt_BR", "tr_TR"])
def test_locale_has_no_extra_keys_beyond_english(lang):
    """All locales must share EXACTLY the en_US key set.

    Guard added after v6.16: ar_SA carried 103 orphan keys (Arabic UI
    literals hardcoded in the WebUI that are never routed through i18n()),
    making the locale files asymmetric. en_US is the canonical key set.
    """
    data = _load(lang)
    extra = [k for k in data if k not in EN]
    assert extra == [], f"{lang} has {len(extra)} keys not in en_US: {extra[:5]}"


def test_english_has_no_arabic_values():
    polluted = [k for k, v in EN.items() if ARABIC_RE.search(v)]
    assert polluted == [], f"en_US has Arabic values: {polluted[:5]}"


def test_arabic_is_actually_arabic():
    """ar_SA values should be Arabic (or intentional proper nouns)."""
    allowed_untranslated = {"main", "Gemini", "G4F", "Unknown_Video"}
    untranslated = [
        k for k in EN
        if AR.get(k) == k and k not in allowed_untranslated and not ARABIC_RE.search(k)
    ]
    assert untranslated == [], f"ar_SA untranslated keys: {untranslated[:10]}"


def test_placeholders_preserved_in_arabic():
    """Keys with {} placeholders must keep them in the Arabic translation."""
    broken = []
    for k in EN:
        if "{}" in k:
            v = AR.get(k, "")
            if k.count("{}") != v.count("{}"):
                broken.append(k)
    assert broken == [], f"placeholder mismatch: {broken[:5]}"
