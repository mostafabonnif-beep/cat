"""Tests for the i18n module (language loading + fallback)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from i18n.i18n import I18nAuto, load_language_list


def test_load_language_list_en():
    data = load_language_list("en_US")
    assert isinstance(data, dict)
    assert len(data) > 0


@pytest.mark.parametrize("lang", ["en_US", "pt_BR", "ar_SA", "tr_TR"])
def test_locale_files_exist(lang):
    """Every declared locale must be loadable."""
    data = load_language_list(lang)
    assert len(data) > 0


def test_known_language_loads():
    i18n = I18nAuto("en_US")
    assert i18n.language == "en_US"
    assert callable(i18n)


def test_missing_language_falls_back_to_english():
    i18n = I18nAuto("xx_XX")
    assert i18n.language == "en_US"


def test_missing_key_returns_key():
    i18n = I18nAuto("en_US")
    assert i18n("this_key_does_not_exist_12345") == "this_key_does_not_exist_12345"


def test_locale_value_wins_over_fallback():
    """A translated key must use the selected locale, not the en_US fallback."""
    i18n = I18nAuto("ar_SA")
    ar_value = load_language_list("ar_SA")["Start Processing"]
    assert ar_value != "Start Processing"  # actually translated
    assert i18n("Start Processing") == ar_value


def test_missing_key_falls_back_to_english_then_key(monkeypatch):
    """Locale files that lag behind en_US degrade to English, then to the key."""
    import i18n.i18n as i18n_mod

    en = {"Start Processing": "Start Processing", "English Only": "English Only"}
    pt = {"Start Processing": "Iniciar Processamento"}  # missing "English Only"

    def fake_load(language):
        return {"pt_BR": pt, "en_US": en}.get(language, {})

    monkeypatch.setattr(i18n_mod, "load_language_list", fake_load)
    i18n = i18n_mod.I18nAuto("pt_BR")
    assert i18n("Start Processing") == "Iniciar Processamento"  # pt wins
    assert i18n("English Only") == "English Only"  # falls back to en_US value
    assert i18n("Missing Key") == "Missing Key"  # fallback exhausted -> key


def test_path_independent_of_cwd(tmp_path, monkeypatch):
    """Loading must work regardless of the current working directory."""
    monkeypatch.chdir(tmp_path)
    data = load_language_list("en_US")
    assert len(data) > 0
