# -*- coding: utf-8 -*-
"""
Safety Filter — YouTube Policy Shield for ViralCutter
======================================================

Scans the transcript text of every AI-selected viral segment and blocks (or
flags) segments whose content is likely to violate the YouTube "Hate speech"
policy (الكلام الذي يحضّ على الكراهية) and related policies (harassment,
violent threats), **before** the clips are cut and uploaded.

Why this exists
---------------
YouTube issues strikes per uploaded clip. A single clip containing a slur or
a call to violence can get a channel a strike even when the source video was
fine. ViralCutter now filters those segments out of the cutting list.

Design
------
* 100% local and offline — no API calls, no extra dependencies.
* Multilingual blocklist: Arabic (فصحى + لهجات: مغاربية/جزائرية، خليجية،
  شامية، مصرية), English, Portuguese, French, Spanish, Turkish.
* Robust normalization so common evasions still match:
    - Arabic diacritics / tatweel removal (حَرَام → حرم، حـــرم → حرم)
    - Alef/Hamza/Ya/Taa-marbuta folding (أإآ→ا، ى→ي، ة→ه)
    - Latin accent folding (unicodedata NFKD)
    - Leetspeak / symbol substitution (@→a, 3→ع, $→s, ...)
    - Collapsed repeated letters (كراااهية → كراهية)
* Three severity levels:
    - ``high``   : slurs / dehumanization / calls to violence  → always blocked
    - ``medium`` : strong profanity / harassment               → blocked in ``block`` mode
    - ``low``    : borderline words                            → only reported
* Modes:
    - ``block`` : remove offending segments from the cutting list (default)
    - ``flag``  : keep the segments but annotate them (``safety`` field) and
                  write the report — nothing is removed
    - ``off``   : do nothing
* Users can extend/override the term list with a ``safety_terms.json`` file
  placed in the repo root or in the project folder. See
  ``safety_terms.example.json``.

Outputs (written to the project folder)
---------------------------------------
* ``safety_report.json`` — full report: per-segment verdicts, matched terms,
  categories, severities and approximate timestamps.
"""

import json
import os
import re
import unicodedata
from datetime import datetime

# File downloaded by scripts/safety_updater.py from the repo's canonical
# pack — merged into the index on top of the built-in BLOCKLIST so that
# hate-speech terms pushed to GitHub reach every installation automatically.
REMOTE_CACHE_FILENAME = "safety_blocklist_cache.json"

# ---------------------------------------------------------------------------
# Blocklist
# ---------------------------------------------------------------------------
# Each entry: (phrase, language, severity, category)
#   category: hate_slur | hate_dehumanize | violence_threat | harassment | profanity
#
# NOTE: these lists intentionally target *strong* policy-violating terms.
# They are not meant to censor normal speech — mild words are "low" severity
# and never block a segment by themselves.

BLOCKLIST = [
    # ------------------------------------------------------------------ AR
    # Slurs / dehumanization targeting groups (الكراهية ضد فئات)
    ("روث البشر", "ar", "high", "hate_dehumanize"),
    ("حثالة البشر", "ar", "high", "hate_dehumanize"),
    ("حثالة المجتمع", "ar", "high", "hate_dehumanize"),
    ("ابن الكلب", "ar", "high", "hate_slur"),
    ("يا كلب", "ar", "medium", "harassment"),
    ("يا حيوان", "ar", "medium", "harassment"),
    ("يا بهيم", "ar", "medium", "harassment"),
    ("كفار", "ar", "low", "hate_slur"),
    ("كفرة", "ar", "low", "hate_slur"),
    ("ملاحدة", "ar", "medium", "hate_slur"),
    ("روافض", "ar", "high", "hate_slur"),
    ("رافضي", "ar", "high", "hate_slur"),
    ("نواصب", "ar", "high", "hate_slur"),
    ("صهيوني قذر", "ar", "high", "hate_slur"),
    ("يهودي قذر", "ar", "high", "hate_slur"),
    ("نصارى قذرون", "ar", "high", "hate_slur"),
    ("عبيد", "ar", "low", "hate_slur"),
    ("زنجي", "ar", "high", "hate_slur"),
    ("عرق حقير", "ar", "high", "hate_dehumanize"),
    ("جنس حقير", "ar", "high", "hate_dehumanize"),
    ("اقذر خلق", "ar", "high", "hate_dehumanize"),
    ("قذرين", "ar", "low", "profanity"),
    ("نجس", "ar", "medium", "hate_dehumanize"),
    ("نجسه", "ar", "medium", "hate_dehumanize"),
    ("داعر", "ar", "high", "profanity"),
    ("عاهر", "ar", "high", "profanity"),
    ("عاهرة", "ar", "high", "profanity"),
    ("شرموط", "ar", "high", "profanity"),
    ("شرموطة", "ar", "high", "profanity"),
    ("زانية", "ar", "high", "profanity"),
    ("زاني", "ar", "high", "profanity"),
    ("قحبة", "ar", "high", "profanity"),
    ("منيوك", "ar", "high", "profanity"),
    ("نيك", "ar", "high", "profanity"),
    ("زبي", "ar", "high", "profanity"),
    ("قحاب", "ar", "high", "profanity"),
    ("زامل", "ar", "high", "hate_slur"),
    ("بوسبير", "ar", "high", "hate_slur"),
    ("خنزير", "ar", "medium", "harassment"),
    ("خنازير", "ar", "high", "hate_dehumanize"),
    ("قردة", "ar", "high", "hate_dehumanize"),
    ("يا كلاب", "ar", "medium", "harassment"),
    ("ابن الزنا", "ar", "high", "profanity"),
    ("ابن الحرام", "ar", "high", "profanity"),
    ("ولد الحرام", "ar", "high", "profanity"),
    ("ولد القحبة", "ar", "high", "profanity"),
    ("يا عرص", "ar", "high", "profanity"),
    ("عرص", "ar", "high", "profanity"),
    ("متخلف عقليا", "ar", "high", "hate_slur"),
    ("منغولي", "ar", "high", "hate_slur"),
    ("معاق ذهنيا", "ar", "high", "hate_slur"),

    # Calls to violence (التحريض على العنف)
    ("اذبحهم", "ar", "high", "violence_threat"),
    ("اذبحوهم", "ar", "high", "violence_threat"),
    ("اقتلوهم", "ar", "high", "violence_threat"),
    ("اقتلهم", "ar", "high", "violence_threat"),
    ("اقضي عليهم", "ar", "high", "violence_threat"),
    ("ابادهم", "ar", "high", "violence_threat"),
    ("إبادتهم", "ar", "high", "violence_threat"),
    ("حرقوهم", "ar", "high", "violence_threat"),
    ("يستحق الذبح", "ar", "high", "violence_threat"),
    ("يستحق القتل", "ar", "high", "violence_threat"),
    ("لازم نقتلهم", "ar", "high", "violence_threat"),
    ("يجب قتلهم", "ar", "high", "violence_threat"),
    ("موتوا", "ar", "high", "violence_threat"),
    ("الموت لهم", "ar", "high", "violence_threat"),
    ("سأقتلك", "ar", "high", "violence_threat"),
    ("راح نقتلك", "ar", "high", "violence_threat"),
    ("ندبحك", "ar", "high", "violence_threat"),

    # ------------------------------------------------------------------ EN
    ("nigger", "en", "high", "hate_slur"),
    ("nigga", "en", "high", "hate_slur"),
    ("faggot", "en", "high", "hate_slur"),
    ("fag", "en", "high", "hate_slur"),
    ("kike", "en", "high", "hate_slur"),
    ("chink", "en", "high", "hate_slur"),
    ("spic", "en", "high", "hate_slur"),
    ("wetback", "en", "high", "hate_slur"),
    ("raghead", "en", "high", "hate_slur"),
    ("towelhead", "en", "high", "hate_slur"),
    ("sand nigger", "en", "high", "hate_slur"),
    ("white trash", "en", "high", "hate_slur"),
    ("subhuman", "en", "high", "hate_dehumanize"),
    ("sub-human", "en", "high", "hate_dehumanize"),
    ("vermin", "en", "low", "hate_dehumanize"),
    ("cockroaches", "en", "low", "hate_dehumanize"),
    ("kill them all", "en", "high", "violence_threat"),
    ("kill all", "en", "high", "violence_threat"),
    ("exterminate", "en", "high", "violence_threat"),
    ("gas the", "en", "high", "violence_threat"),
    ("i will kill you", "en", "high", "violence_threat"),
    ("gonna kill you", "en", "high", "violence_threat"),
    ("death to", "en", "high", "violence_threat"),
    ("retard", "en", "high", "hate_slur"),
    ("retarded", "en", "high", "hate_slur"),
    ("tranny", "en", "high", "hate_slur"),
    ("whore", "en", "high", "profanity"),
    ("slut", "en", "high", "profanity"),
    ("cunt", "en", "high", "profanity"),
    ("motherfucker", "en", "high", "profanity"),
    ("son of a bitch", "en", "high", "profanity"),
    ("go to hell", "en", "low", "profanity"),

    # ------------------------------------------------------------------ PT
    ("macaco", "pt", "high", "hate_slur"),
    ("crioulo", "pt", "high", "hate_slur"),
    ("viado", "pt", "high", "hate_slur"),
    ("bicha", "pt", "high", "hate_slur"),
    ("traveco", "pt", "high", "hate_slur"),
    ("vadia", "pt", "high", "profanity"),
    ("puta", "pt", "high", "profanity"),
    ("vagabunda", "pt", "high", "profanity"),
    ("filho da puta", "pt", "high", "profanity"),
    ("retardado", "pt", "high", "hate_slur"),
    ("mata eles", "pt", "high", "violence_threat"),
    ("matar todos", "pt", "high", "violence_threat"),
    ("morte aos", "pt", "high", "violence_threat"),
    ("vou te matar", "pt", "high", "violence_threat"),

    # ------------------------------------------------------------------ FR
    ("nègre", "fr", "high", "hate_slur"),
    ("bougnoule", "fr", "high", "hate_slur"),
    ("pédé", "fr", "high", "hate_slur"),
    ("salope", "fr", "high", "profanity"),
    ("pute", "fr", "high", "profanity"),
    ("fils de pute", "fr", "high", "profanity"),
    ("attardé", "fr", "high", "hate_slur"),
    ("tuez-les", "fr", "high", "violence_threat"),
    ("mort aux", "fr", "high", "violence_threat"),
    ("je vais te tuer", "fr", "high", "violence_threat"),

    # ------------------------------------------------------------------ ES
    ("maricón", "es", "high", "hate_slur"),
    ("puta", "es", "high", "profanity"),
    ("hijo de puta", "es", "high", "profanity"),
    ("retrasado", "es", "high", "hate_slur"),
    ("mátalos", "es", "high", "violence_threat"),
    ("muerte a los", "es", "high", "violence_threat"),
    ("te voy a matar", "es", "high", "violence_threat"),

    # ------------------------------------------------------------------ TR
    ("orospu", "tr", "high", "profanity"),
    ("piç", "tr", "high", "profanity"),
    ("ibne", "tr", "high", "hate_slur"),
    ("öldüreceğim", "tr", "high", "violence_threat"),
    ("seni öldüreceğim", "tr", "high", "violence_threat"),
    ("geber", "tr", "high", "violence_threat"),
    # ------------------------------------------------------------------ v7.18
    # Terms flagged in YouTube policy reviews / community reports (2026).
    # AR — dehumanization & incitement frequently used in hate comments.
    ("أيها النجس", "ar", "high", "hate_dehumanize"),
    ("نجس", "ar", "medium", "harassment"),
    ("يا زبالة", "ar", "medium", "harassment"),
    ("اقتلوهم", "ar", "high", "violence_threat"),
    ("احرقوهم", "ar", "high", "violence_threat"),
    ("اذبحوهم", "ar", "high", "violence_threat"),
    ("حشرات", "ar", "medium", "hate_dehumanize"),
    ("صراصير", "ar", "medium", "hate_dehumanize"),
    ("قرود", "ar", "medium", "hate_slur"),
    ("يا حقير", "ar", "medium", "harassment"),
    ("الله يلعن", "ar", "medium", "profanity"),
    ("سلالة حقيرة", "ar", "high", "hate_dehumanize"),
    ("أوساخ", "ar", "medium", "hate_dehumanize"),
    # EN — dehumanization / threats seen in strikes.
    ("exterminate them", "en", "high", "violence_threat"),
    ("kill them all", "en", "high", "violence_threat"),
    ("burn them alive", "en", "high", "violence_threat"),
    ("subhuman", "en", "high", "hate_dehumanize"),
    ("vermin", "en", "high", "hate_dehumanize"),
    ("cockroaches", "en", "medium", "hate_dehumanize"),
    ("disgusting race", "en", "high", "hate_slur"),
    ("hanging them", "en", "high", "violence_threat"),
    ("shoot them all", "en", "high", "violence_threat"),
    ("آسيوي", "ar", "high", "hate_slur"),
    ("أفعى", "ar", "medium", "hate_dehumanize"),
    ("إماراتي", "ar", "high", "hate_slur"),
    ("اخونجي", "ar", "high", "hate_slur"),
    ("اسود", "ar", "high", "hate_slur"),
    ("اعرابي", "ar", "high", "hate_slur"),
    ("اقباط", "ar", "high", "hate_slur"),
    ("البنا", "ar", "high", "hate_slur"),
    ("انجاس", "ar", "medium", "hate_dehumanize"),
    ("ايراني", "ar", "high", "hate_slur"),
    ("بدو", "ar", "high", "hate_slur"),
    ("بغل", "ar", "medium", "hate_dehumanize"),
    ("بقر", "ar", "medium", "hate_dehumanize"),
    ("بقرة", "ar", "medium", "hate_dehumanize"),
    ("بهايم", "ar", "medium", "hate_dehumanize"),
    ("بهيمة", "ar", "medium", "hate_dehumanize"),
    ("بول", "ar", "medium", "hate_dehumanize"),
    ("بومة", "ar", "medium", "hate_dehumanize"),
    ("تبن", "ar", "medium", "hate_dehumanize"),
    ("جحش", "ar", "medium", "hate_dehumanize"),
    ("جحشة", "ar", "medium", "hate_dehumanize"),
    ("جرابيع", "ar", "medium", "hate_dehumanize"),
    ("جراثيم", "ar", "medium", "hate_dehumanize"),
    ("جربوع", "ar", "medium", "hate_dehumanize"),
    ("جرثومة", "ar", "medium", "hate_dehumanize"),
    ("جزم", "ar", "medium", "hate_dehumanize"),
    ("جزمة", "ar", "medium", "hate_dehumanize"),
    ("حثالة", "ar", "medium", "hate_dehumanize"),
    ("حمار", "ar", "medium", "hate_dehumanize"),
    ("حمارة", "ar", "medium", "hate_dehumanize"),
    ("حمير", "ar", "medium", "hate_dehumanize"),
    ("حيوان", "ar", "medium", "hate_dehumanize"),
    ("حيوانات", "ar", "medium", "hate_dehumanize"),
    ("خرا", "ar", "medium", "hate_dehumanize"),
    ("خرائ", "ar", "medium", "hate_dehumanize"),
    ("خرة", "ar", "medium", "hate_dehumanize"),
    ("خرتيت", "ar", "medium", "hate_dehumanize"),
    ("خليجي", "ar", "high", "hate_slur"),
    ("خنيث", "ar", "high", "hate_slur"),
    ("خول", "ar", "high", "hate_slur"),
    ("خولات", "ar", "high", "hate_slur"),
    ("داعش", "ar", "high", "hate_slur"),
    ("داعشي", "ar", "high", "hate_slur"),
    ("درزي", "ar", "high", "hate_slur"),
    ("ذباب", "ar", "medium", "hate_dehumanize"),
    ("رخمة", "ar", "medium", "hate_dehumanize"),
    ("رمة", "ar", "medium", "hate_dehumanize"),
    ("رمم", "ar", "medium", "hate_dehumanize"),
    ("زبالة", "ar", "medium", "hate_dehumanize"),
    ("زراط", "ar", "medium", "hate_dehumanize"),
    ("زريبة", "ar", "medium", "hate_dehumanize"),
    ("زفت", "ar", "medium", "hate_dehumanize"),
    ("زق", "ar", "medium", "hate_dehumanize"),
    ("زنانة", "ar", "medium", "harassment"),
    ("زنديق", "ar", "high", "hate_slur"),
    ("زنوج", "ar", "high", "hate_slur"),
    ("سحارة", "ar", "medium", "hate_dehumanize"),
    ("سعدانة", "ar", "medium", "hate_dehumanize"),
    ("سعودي", "ar", "high", "hate_slur"),
    ("سلفي", "ar", "high", "hate_slur"),
    ("سلوقي", "ar", "medium", "hate_dehumanize"),
    ("سوداني", "ar", "high", "hate_slur"),
    ("سوري", "ar", "high", "hate_slur"),
    ("سيس", "ar", "high", "hate_slur"),
    ("شاذ", "ar", "high", "hate_slur"),
    ("شارب بول البعير", "ar", "high", "hate_slur"),
    ("شبشب", "ar", "medium", "hate_dehumanize"),
    ("شحات", "ar", "medium", "hate_dehumanize"),
    ("شحاطة", "ar", "medium", "hate_dehumanize"),
    ("شخاخ", "ar", "medium", "hate_dehumanize"),
    ("شخة", "ar", "medium", "hate_dehumanize"),
    ("شواذ", "ar", "high", "hate_slur"),
    ("شيعي", "ar", "high", "hate_slur"),
    ("صباط", "ar", "medium", "hate_dehumanize"),
    ("صرصور", "ar", "medium", "hate_dehumanize"),
    ("صرماية", "ar", "medium", "hate_dehumanize"),
    ("صفوي", "ar", "high", "hate_slur"),
    ("صهاينة", "ar", "high", "hate_slur"),
    ("صهيوني", "ar", "high", "hate_slur"),
    ("صومالي", "ar", "high", "hate_slur"),
    ("صيني", "ar", "high", "hate_slur"),
    ("ضب", "ar", "medium", "hate_dehumanize"),
    ("ضبعة", "ar", "medium", "hate_dehumanize"),
    ("عبد", "ar", "high", "hate_slur"),
    ("عبدة بقر", "ar", "high", "hate_slur"),
    ("عبرية", "ar", "high", "hate_slur"),
    ("عجل", "ar", "medium", "hate_dehumanize"),
    ("عربان", "ar", "high", "hate_slur"),
    ("عفن", "ar", "medium", "hate_dehumanize"),
    ("علق", "ar", "high", "hate_slur"),
    ("علوج", "ar", "high", "hate_slur"),
    ("عنزة", "ar", "medium", "hate_dehumanize"),
    ("عيال المتعة", "ar", "high", "hate_slur"),
    ("فرخة", "ar", "medium", "hate_dehumanize"),
    ("فرس", "ar", "high", "hate_slur"),
    ("فلاح", "ar", "high", "hate_slur"),
    ("فلاحين", "ar", "high", "hate_slur"),
    ("فلس طيني", "ar", "high", "hate_slur"),
    ("قذر", "ar", "medium", "hate_dehumanize"),
    ("قذرة", "ar", "medium", "hate_dehumanize"),
    ("قرامطة", "ar", "high", "hate_slur"),
    ("قرد", "ar", "medium", "hate_dehumanize"),
    ("قطري", "ar", "high", "hate_slur"),
    ("قمامة", "ar", "medium", "hate_dehumanize"),
    ("قوم لوط", "ar", "high", "hate_slur"),
    ("كافر", "ar", "high", "hate_slur"),
    ("كفتس", "ar", "high", "hate_slur"),
    ("كلاب", "ar", "medium", "hate_dehumanize"),
    ("كلب", "ar", "medium", "hate_dehumanize"),
    ("كلبة", "ar", "medium", "hate_dehumanize"),
    ("كويتي", "ar", "high", "hate_slur"),
    ("لاجئ", "ar", "high", "hate_slur"),
    ("لواط", "ar", "high", "hate_slur"),
    ("لوطي", "ar", "high", "hate_slur"),
    ("مبادل", "ar", "high", "hate_slur"),
    ("مثليين", "ar", "high", "hate_slur"),
    ("مجوسي", "ar", "high", "hate_slur"),
    ("مخانيث", "ar", "high", "hate_slur"),
    ("مخنث", "ar", "high", "hate_slur"),
    ("مرتد", "ar", "high", "hate_slur"),
    ("مسيحي", "ar", "high", "hate_slur"),
    ("مشرد", "ar", "medium", "hate_dehumanize"),
    ("معفن", "ar", "medium", "hate_dehumanize"),
    ("معيز", "ar", "medium", "hate_dehumanize"),
    ("مكانك المطبخ", "ar", "medium", "harassment"),
    ("ملحد", "ar", "high", "hate_slur"),
    ("ناقصات عقل ودين", "ar", "medium", "harassment"),
    ("نتن", "ar", "medium", "hate_dehumanize"),
    ("نعال", "ar", "medium", "hate_dehumanize"),
    ("نكدية", "ar", "medium", "harassment"),
    ("هندي", "ar", "high", "hate_slur"),
    ("وافد", "ar", "high", "hate_slur"),
    ("وثني", "ar", "high", "hate_slur"),
    ("وسخ", "ar", "medium", "hate_dehumanize"),
    ("وسخة", "ar", "medium", "hate_dehumanize"),
    ("يهود", "ar", "high", "hate_slur"),
    ("يهودي", "ar", "high", "hate_slur"),
]

SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}

# Identity words are not hate speech by themselves. They need an explicit
# attack, exclusion, dehumanizing comparison, or call to harm around them.
# Keep them visible in reports as low-severity context signals, but never let
# a bare nationality, religion, ethnicity, or demographic label block a clip.
CONTEXT_ONLY_TERMS = {
    "افريقي", "افارقه", "اسيوي", "امازيغي", "امازيغ", "اماراتي", "ايراني",
    "اقباط", "اعرابي", "بدو", "خليجي", "درزي", "سعودي", "سوداني", "سوري",
    "صهيوني", "صهاينه", "صومالي", "صيني", "عربي", "عرب", "قبطي", "قطري",
    "كويتي", "لاجئ", "لاجئين", "مسيحي", "مسيحيين", "مسلم", "مسلمين",
    "ملحد", "ملحدين", "مهاجر", "مهاجرين", "مثليين", "مثليات", "يهودي",
    "يهود", "نصراني", "نصارى", "شيعي", "شيعة", "سني", "سنة", "نساء",
    "رجال", "معاقين", "ذوي اعاقه", "غجر", "حيوان", "حيوانات", "قرد", "قردة",
    "قرود", "خنزير", "خنازير", "حشرات", "صراصير", "جرذان", "كلب", "كلاب",
    "black people", "white people",
    "jews", "muslims", "christians", "immigrants", "refugees", "foreigners",
    "women", "men", "gay people", "disabled people",
}

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_AR_DIACRITICS = re.compile(
    "[ً-ْٰـۖ-ۜ۟-۪ۨ-ۭ]"
)

_AR_MAP = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ئ": "ي", "ؤ": "و",
    "ة": "ه",
})

_LEET_MAP = str.maketrans({
    "@": "a", "4": "a",
    "8": "b",
    "(": "c", "<": "c",
    "3": "ع",  # Arabizi
    "€": "e",
    "6": "g",
    "#": "h",
    "1": "i", "!": "i", "|": "i",
    "0": "o",
    "9": "q",
    "$": "s", "5": "s",
    "7": "ح",  # Arabizi
    "2": "ا",  # Arabizi hamza
    "+": "t",
    "µ": "u",
    "×": "x",
    "¥": "y",
})


def _fold_repeats(text):
    """Collapse 3+ repeated chars into one (كــراهية / craaaazy → كراهية / crazy)."""
    return re.sub(r"(.)\1{2,}", r"\1", text)


def normalize_text(text):
    """Normalize text so that blocklist matching is robust to evasion."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", str(text))
    t = _AR_DIACRITICS.sub("", t)          # Arabic diacritics + tatweel
    t = t.translate(_AR_MAP)               # Alef/Ya/Taa-marbuta folding
    t = t.lower()
    # Latin accent folding
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.translate(_LEET_MAP)
    t = _fold_repeats(t)
    # keep letters/digits/arabic range + spaces
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    # strip the Arabic definite article "ال" (and و/ف/ب/ك/ل + ال) attached to
    # words: القردة → قردة ، والخنازير → خنازير
    t = re.sub(r"(?<!\w)[وفبكل]?ال(?=\w{2,})", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# Pre-normalize the blocklist once (phrase matching happens on token level)
def _build_index(extra_terms=None):
    index = []
    entries = list(BLOCKLIST)
    if extra_terms:
        for e in extra_terms:
            if isinstance(e, dict):
                entries.append((
                    e.get("term", ""), e.get("lang", "custom"),
                    e.get("severity", "high"), e.get("category", "custom"),
                ))
            elif isinstance(e, str):
                entries.append((e, "custom", "high", "custom"))
    context_only_norms = {normalize_text(term) for term in CONTEXT_ONLY_TERMS}
    for phrase, lang, severity, category in entries:
        norm = normalize_text(phrase)
        if not norm:
            continue
        index.append({
            "term": phrase,
            "norm": norm,
            "tokens": norm.split(" "),
            "lang": lang,
            "severity": severity,
            "category": category,
            "context_only": norm in context_only_norms,
        })
    unique = {}
    severity_rank = {"low": 1, "medium": 2, "high": 3}
    for entry in index:
        key = (entry["norm"], entry["lang"], entry["category"])
        previous = unique.get(key)
        if previous is None or severity_rank.get(entry["severity"], 3) > severity_rank.get(previous["severity"], 3):
            unique[key] = entry
    index = list(unique.values())
    index.sort(key=lambda e: len(e["tokens"]), reverse=True)
    return index


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _context_is_harmful(text):
    try:
        from scripts.semantic_safety import analyze_text
        semantic = analyze_text(text)
        if semantic.get("action") in {"block", "review"}:
            return True
        if "dehumanizing_comparison" not in semantic.get("signals", []):
            return False
        normalized = normalize_text(text)
        dehumanizing_terms = {
            normalize_text(term) for term in
            ("قرد", "قردة", "قرود", "خنزير", "خنازير", "حشرات", "صراصير", "جرذان", "كلب", "كلاب")
        }
        return sum(1 for term in dehumanizing_terms if f" {term} " in f" {normalized} ") >= 2
    except Exception:
        return True


def find_matches(text, index=None, min_severity="low", allow_terms=None):
    """Return all blocklist matches found in *text* (already raw text).

    ``allow_terms`` excludes specific blocklist terms (false-positive
    control) — matching is done on the normalized form.
    """
    if index is None:
        index = _build_index()
    allow_norms = {normalize_text(t) for t in (allow_terms or [])}
    norm = normalize_text(text)
    if not norm:
        return []
    tokens = norm.split(" ")
    joined = " " + " ".join(tokens) + " "
    # v7.19: Arabic conjunctive prefixes و/ف ("and"/"then") attach directly to
    # the next word (وخولات = "and khawalat", فقتلوهم = "then kill them").
    # Emit prefix-stripped variants so blocklist terms still match. Only و/ف
    # are stripped — the prepositions ب/ك/ل are kept (too ambiguous, e.g.
    # "بكلب" must not become "كلب" in benign speech).
    variant_tokens = [tok[1:] for tok in tokens
                      if len(tok) > 3 and tok[0] in "وف"]
    if variant_tokens:
        joined += " " + " ".join(variant_tokens) + " "
    matches = []
    min_rank = SEVERITY_ORDER.get(min_severity, 1)
    context_is_harmful = _context_is_harmful(text)
    for entry in index:
        effective_severity = entry["severity"]
        if entry.get("context_only") and not context_is_harmful:
            effective_severity = "low"
        if SEVERITY_ORDER.get(effective_severity, 3) < min_rank:
            continue
        if entry["norm"] in allow_norms:
            continue
        phrase = " " + entry["norm"] + " "
        if phrase in joined:
            # approximate position (token index) for reporting
            pos = joined.find(phrase)
            approx_word = joined[:pos].count(" ")
            matches.append({
                "term": entry["term"],
                "lang": entry["lang"],
                "severity": effective_severity,
                "category": entry["category"] if context_is_harmful else (
                    "context_only_identity" if entry.get("context_only") else entry["category"]
                ),
                "context_only": bool(entry.get("context_only")),
                "word_index": max(0, approx_word - 1),
            })
    return matches


# ---------------------------------------------------------------------------
# Transcript helpers
# ---------------------------------------------------------------------------

def load_transcript(project_folder):
    """Parse input.tsv / input.srt from the project folder.

    Implemented locally (instead of importing create_viral_segments) to keep
    this module side-effect free — importing create_viral_segments re-wraps
    sys.stdout at import time, which breaks pytest capture and notebooks.
    """
    input_tsv = os.path.join(project_folder, 'input.tsv')
    input_srt = os.path.join(project_folder, 'input.srt')

    transcript_segments = []

    if os.path.exists(input_tsv):
        try:
            with open(input_tsv, 'r', encoding='utf-8') as f:
                lines = f.readlines()[1:]
            for line in lines:
                parts = line.strip().split('\t')
                if len(parts) >= 3:
                    transcript_segments.append({
                        'start': float(parts[0]) / 1000.0,
                        'end': float(parts[1]) / 1000.0,
                        'text': parts[2],
                    })
        except Exception as e:
            print(f"[safety] Error parsing TSV: {e}")

    if not transcript_segments and os.path.exists(input_srt):
        try:
            with open(input_srt, 'r', encoding='utf-8') as f:
                srt_content = f.read()
            pattern = re.compile(
                r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n((?:(?!\n\n).)*)',
                re.DOTALL)

            def srt_time_to_seconds(t_str):
                h, m, s = t_str.replace(',', '.').split(':')
                return int(h) * 3600 + int(m) * 60 + float(s)

            for match in pattern.findall(srt_content):
                transcript_segments.append({
                    'start': srt_time_to_seconds(match[1]),
                    'end': srt_time_to_seconds(match[2]),
                    'text': match[3].replace('\n', ' '),
                })
        except Exception as e:
            print(f"[safety] Error parsing SRT: {e}")

    return transcript_segments


def segment_text(segment, transcript_segments):
    """Concatenate transcript text that genuinely overlaps the segment window."""
    start = float(segment.get("start_time", 0) or 0)
    end = float(segment.get("end_time", start) or start)
    parts = []
    for ts in transcript_segments:
        ts_start = ts.get("start", 0)
        ts_end = ts.get("end", ts_start)
        # strict overlap: only chunks truly inside [start, end]
        if ts_end <= start + 0.05:
            continue
        if ts_start >= end - 0.05:
            break
        parts.append(ts.get("text", ""))
    text = " ".join(parts).strip()
    # Fallback: some AI segments carry their own text
    if not text:
        text = " ".join([
            str(segment.get("text", "")),
            str(segment.get("start_text", "")),
            str(segment.get("end_text", "")),
            str(segment.get("title", "")),
            str(segment.get("caption", "")),
            str(segment.get("reasoning", "")),
        ]).strip()
    return text


def _approx_match_times(segment, transcript_segments, matches):
    """Approximate timestamp of each match inside the segment (for the report)."""
    text = segment_text(segment, transcript_segments)
    norm_words = normalize_text(text).split(" ")
    if not norm_words:
        return matches
    seg_start = float(segment.get("start_time", 0) or 0)
    seg_end = float(segment.get("end_time", seg_start) or seg_start)
    duration = max(0.001, seg_end - seg_start)
    n = len(norm_words)
    for m in matches:
        frac = min(0.99, max(0.0, m.get("word_index", 0) / max(1, n)))
        m["approx_time"] = round(seg_start + frac * duration, 2)
        m.pop("word_index", None)
    return matches


# ---------------------------------------------------------------------------
# Custom terms
# ---------------------------------------------------------------------------

def load_remote_terms(base_dir=None):
    """Read the auto-updated blocklist cache (downloaded from GitHub).

    Returns a list of term dicts [{term, lang, severity, category}] — [] if
    the cache is missing/corrupt (the built-in list still protects).
    """
    base_dir = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base_dir, REMOTE_CACHE_FILENAME)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            pack = json.load(f)
        terms = pack.get("terms", [])
        if not isinstance(terms, list):
            return []
        return [t for t in terms if isinstance(t, dict) and t.get("term")]
    except Exception:
        return []


def load_custom_terms(project_folder=None, extra_path=None):
    """Load user-provided terms from safety_terms.json (repo root, project
    folder, or an explicit path). Never raises.

    Returns ``{"extra_terms": [...], "allow_terms": [...]}``:
      * ``extra_terms`` — additional terms to BLOCK
      * ``allow_terms`` — terms to EXCLUDE from the built-in blocklist
        (false-positive control, e.g. a history channel saying "منغولي")
    """
    candidates = []
    if extra_path:
        candidates.append(extra_path)
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates.append(os.path.join(base_dir, "safety_terms.json"))
    if project_folder:
        candidates.append(os.path.join(project_folder, "safety_terms.json"))

    result = {"extra_terms": [], "allow_terms": []}
    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                result["extra_terms"].extend(data)
            elif isinstance(data, dict):
                result["extra_terms"].extend(data.get("extra_terms", []))
                result["allow_terms"].extend(data.get("allow_terms", []))
        except Exception as e:
            print(f"[safety] Could not read custom terms from {path}: {e}")
    try:
        from scripts import policy_lexicon
        result["extra_terms"].extend(policy_lexicon.load_terms(project_folder, sync=True))
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

def analyze_segments(segments, transcript_segments=None, project_folder=None,
                     mode="block", min_severity="medium",
                     extra_terms_path=None):
    """
    Analyze a list of viral segments.

    Returns ``(result_segments, report)``:
      * ``block``  mode → result_segments excludes blocked segments
      * ``flag``   mode → result_segments is the full list, each segment gets a
                          ``safety`` annotation field
      * ``censor`` mode → like ``flag`` (keeps everything, annotated) — the
                          actual muting happens after cutting via
                          scripts/censor_engine.py
      * ``off``    mode → returns input untouched (still produces a report if
                          called directly)
    """
    segments = list(segments or [])
    if transcript_segments is None and project_folder:
        try:
            transcript_segments = load_transcript(project_folder)
        except Exception:
            transcript_segments = []
    transcript_segments = transcript_segments or []

    from scripts import semantic_safety

    custom = load_custom_terms(project_folder, extra_terms_path)
    index = _build_index(custom.get("extra_terms", []) + load_remote_terms())
    allow_terms = custom.get("allow_terms", [])
    block_threshold = SEVERITY_ORDER.get(min_severity, 2)

    report_entries = []
    kept = []
    blocked_count = 0
    manual_review_count = 0

    for segment_index, seg in enumerate(segments):
        text = segment_text(seg, transcript_segments)
        semantic = semantic_safety.analyze_text(text)
        matches = find_matches(text, index=index, min_severity="low",
                               allow_terms=allow_terms)
        blocking = [m for m in matches
                    if SEVERITY_ORDER.get(m["severity"], 3) >= block_threshold
                    and not (m.get("context_only") and m.get("severity") == "low" and semantic.get("action") == "allow")]
        if semantic.get("action") == "block":
            blocking.append({
                "term": "semantic policy pattern",
                "lang": "multi",
                "severity": "high",
                "category": semantic.get("category") or "semantic_policy",
                "word_index": 0,
            })

        entry = {
            "index": segment_index,
            "title": seg.get("title", ""),
            "start_time": seg.get("start_time"),
            "end_time": seg.get("end_time"),
            "status": "safe",
            "matches": _approx_match_times(seg, transcript_segments, matches),
            "semantic": semantic,
        }

        if semantic.get("action") == "review":
            manual_review_count += 1
            entry["status"] = "manual_review"

        semantic_block = semantic.get("action") == "block"
        if blocking and (mode == "block" or semantic_block):
            entry["status"] = "semantic_blocked" if semantic_block and mode == "censor" else "blocked"
            blocked_count += 1
        elif blocking and mode in ("flag", "censor"):
            entry["status"] = "flagged" if mode == "flag" else "censor"
            seg = dict(seg)
            seg["safety"] = {
                "flagged": True,
                "action": mode,
                "reasons": sorted({m["category"] for m in blocking}),
                "terms": sorted({m["term"] for m in blocking}),
            }
            if semantic.get("action") == "review":
                seg["safety"]["manual_review"] = True
                seg["safety"]["semantic_reason"] = semantic.get("explanation", "")
            kept.append(seg)
        else:
            if semantic.get("action") == "review":
                seg = dict(seg)
                safety = dict(seg.get("safety", {}))
                safety.update({
                    "manual_review": True,
                    "semantic_category": semantic.get("category"),
                    "semantic_reason": semantic.get("explanation", ""),
                    "semantic_confidence": semantic.get("confidence", 0.0),
                })
                seg["safety"] = safety
            kept.append(seg)

        report_entries.append(entry)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "min_severity": min_severity,
        "total_segments": len(segments),
        "kept": len(kept),
        "blocked": blocked_count,
        "flagged": sum(1 for e in report_entries if e["status"] == "flagged"),
        "manual_review": manual_review_count,
        "censored": sum(1 for e in report_entries if e["status"] == "censor"),
        "segments": report_entries,
    }
    return kept, report


def apply_safety_filter(viral_segments, project_folder, mode="block",
                        min_severity="medium", extra_terms_path=None,
                        i18n=lambda k: k):
    """
    High-level helper used by main_improved.py:
      * filters the ``segments`` list inside the viral_segments dict
      * writes ``safety_report.json`` into the project folder
      * prints a human summary
    Returns the (possibly filtered) viral_segments dict.
    """
    if mode == "off" or not viral_segments or "segments" not in viral_segments:
        return viral_segments

    segments = viral_segments.get("segments", [])
    if not segments:
        return viral_segments

    kept, report = analyze_segments(
        segments,
        project_folder=project_folder,
        mode=mode,
        min_severity=min_severity,
        extra_terms_path=extra_terms_path,
    )

    # Persist report
    try:
        report_path = os.path.join(project_folder, "safety_report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(i18n("[safety] Report saved to {}").format(report_path))
    except Exception as e:
        print(f"[safety] Could not save report: {e}")

    if mode == "block" and report["blocked"]:
        print(i18n("[safety] {} segment(s) BLOCKED for policy-violating content (hate speech / violence).").format(report["blocked"]))
        for entry in report["segments"]:
            if entry["status"] == "blocked":
                terms = ", ".join(sorted({m["term"] for m in entry["matches"]}))
                print(i18n("[safety]   ✗ '{}' ({}): {}").format(
                    entry["title"], entry["status"], terms))
    elif mode == "flag" and report["flagged"]:
        print(i18n("[safety] {} segment(s) flagged for review (kept).").format(report["flagged"]))
    elif mode == "censor":
        if report["blocked"]:
            print(i18n("[safety] {} segment(s) were removed because contextual policy risk cannot be safely censored.").format(report["blocked"]))
        if report["censored"]:
            print(i18n("[safety] {} segment(s) contain policy-violating words — they will be BLEEPED (muted) after cutting.").format(report["censored"]))
        if not report["blocked"] and not report["censored"]:
            print(i18n("[safety] All {} segments passed the policy check ✔").format(report["total_segments"]))
    else:
        print(i18n("[safety] All {} segments passed the policy check ✔").format(report["total_segments"]))

    result = dict(viral_segments)
    result["segments"] = kept
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Filter viral segments that may violate YouTube hate-speech policy.")
    parser.add_argument("--project", required=True, help="Project folder containing viral_segments.txt")
    parser.add_argument("--mode", choices=["block", "flag", "off"], default="block")
    parser.add_argument("--min-severity", choices=["low", "medium", "high"], default="medium")
    parser.add_argument("--extra-terms", help="Path to safety_terms.json with extra terms")
    parser.add_argument("--in-place", action="store_true",
                        help="Rewrite viral_segments.txt with the filtered list")
    args = parser.parse_args()

    segments_path = os.path.join(args.project, "viral_segments.txt")
    if not os.path.exists(segments_path):
        print(f"[safety] {segments_path} not found.")
        raise SystemExit(1)

    with open(segments_path, "r", encoding="utf-8") as f:
        viral_segments = json.load(f)

    filtered = apply_safety_filter(
        viral_segments,
        project_folder=args.project,
        mode=args.mode,
        min_severity=args.min_severity,
        extra_terms_path=args.extra_terms,
    )

    if args.in_place and args.mode == "block":
        with open(segments_path, "w", encoding="utf-8") as f:
            json.dump(filtered, f, ensure_ascii=False, indent=4)
        print(f"[safety] {segments_path} updated in place.")


if __name__ == "__main__":
    main()
