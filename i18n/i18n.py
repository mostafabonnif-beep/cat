import json
import locale
import os

_LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locale")

# Default UI language. Override with the VIRALCUTTER_LANG environment
# variable (e.g. VIRALCUTTER_LANG=en_US) to run in another language.
DEFAULT_LANGUAGE = os.getenv("VIRALCUTTER_LANG", "ar_SA")


def load_language_list(language):
    with open(
        os.path.join(_LOCALE_DIR, f"{language}.json"), "r", encoding="utf-8"
    ) as f:
        language_list = json.load(f)
    return language_list


class I18nAuto:
    def __init__(self, language=None):
        if language in ["Auto", None]:
            # getdefaultlocale() is deprecated in Python 3.11+; getlocale()
            # is sufficient here and the file-existence fallback below keeps
            # minimal containers safe when no system locale is configured.
            language = locale.getlocale()[0] or "en_US"
            language = language.replace("-", "_")
        if not os.path.exists(os.path.join(_LOCALE_DIR, f"{language}.json")):
            language = "en_US"
        self.language = language
        self.language_map = load_language_list(language)
        # Fallback chain: selected locale -> en_US -> the raw key. A locale
        # file that lags behind en_US degrades gracefully instead of showing
        # raw keys (or worse, orphan entries from another language).
        self._fallback = (
            load_language_list("en_US") if language != "en_US" else self.language_map
        )

    def __call__(self, key):
        value = self.language_map.get(key)
        if value is not None:
            return value
        return self._fallback.get(key, key)
