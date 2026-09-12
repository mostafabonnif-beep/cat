# -*- coding: utf-8 -*-
"""title_factual — factual title validation, scoring and safe fallbacks.

The pipeline's title contract (v7.40):

* A title is generated ONLY from the exact transcript text of the selected
  clip window — never from the full source-video transcript.
* A title must never introduce a name, number, result, claim or conclusion
  that is not explicitly present in the clip transcript.
* When the transcript is ambiguous, a conservative descriptive title is used
  and confidence is set LOW — the model must not invent a sensational title.
* Title language must match the clip language (Arabic clip → Arabic title).

Everything here is deterministic and stdlib-only so validation is testable
and reproducible without an LLM.
"""

from __future__ import annotations

import re

from scripts import arabic_text
from scripts.title_text import detect_text_script, fit_publish_title

# ---------------------------------------------------------------------------
# Stopwords (excluded from factual-overlap measurement — grammar, not facts)
# ---------------------------------------------------------------------------

AR_STOPWORDS = {
    "في", "على", "إلى", "الى", "عن", "من", "مع", "هذا", "هذه", "ذلك",
    "التي", "الذي", "أن", "ان", "إن", "كان", "كانت", "ما", "لا", "لم",
    "لن", "قد", "هو", "هي", "هم", "أنا", "انا", "نحن", "انت", "أنت",
    "ثم", "أو", "او", "و", "ف", "ب", "ل", "ك", "يا", "أي", "اي", "كل",
    "بعد", "قبل", "عند", "حتى", "إذا", "اذا", "لكن", "ليس", "بين",
    "منه", "منها", "فيه", "فيها", "عليه", "عندما", "الآن", "الان",
    "شيء", "كيف", "ماذا", "لماذا", "هل", "تم", "بشكل", "جدا", "أيضا",
    "the", "a", "an", "and", "or", "but", "so", "to", "of", "in", "on",
    "at", "for", "with", "is", "are", "was", "were", "be", "been", "it",
    "its", "this", "that", "these", "those", "i", "you", "he", "she",
    "we", "they", "my", "your", "his", "her", "our", "their", "not",
    "do", "does", "did", "have", "has", "had", "will", "would", "can",
    "could", "how", "what", "why", "when", "who", "which", "there",
    "here", "just", "really", "very", "much", "many", "some", "any",
}

# Strong clickbait patterns: penalized ALWAYS; treated as excessive when the
# title is also factually unsupported (the spec's "unless the clip clearly
# supports the claim" — deterministic validation cannot prove support, so a
# low-factuality clickbait title is rejected, a factual one is only nudged).
_CLICKBAIT_PATTERNS = [
    r"لن تصدق", r"لن تتوقع", r"ستغير حياتك", r"تغير حياتك", r"سر خطير",
    r"الجميع مخطئ", r"اكتشاف صادم", r"اكتشاف مذهل", r"صدمة", r"صادم",
    r"انصدمت", r"صُدمت", r"لا أحد يخبرك", r"لا يخبرك أحد", r"قبل فوات الأوان",
    r"you won'?t believe", r"won'?t expect", r"will change your life",
    r"shocking", r"insane", r"gone wrong", r"they don'?t want you to know",
]
_CLICKBAIT_RE = re.compile("|".join(_CLICKBAIT_PATTERNS), re.IGNORECASE)

_LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

PUBLISH_TITLE_LIMIT = 100


def _content_tokens(text):
    """Normalized content words (stopwords removed, min length 3)."""
    out = []
    for token in arabic_text.normalized_match_text(text).split():
        if len(token) < 3:
            continue
        if token in AR_STOPWORDS:
            continue
        out.append(token)
    return out


def contains_clickbait_pattern(title):
    return bool(_CLICKBAIT_RE.search(str(title or "")))


def title_numbers(title):
    """All numbers in the title, digits normalized (Arabic-Indic → ASCII)."""
    return set(_NUMBER_RE.findall(arabic_text.normalize_digits(str(title or ""))))


def _text_numbers(text):
    return set(_NUMBER_RE.findall(arabic_text.normalize_digits(str(text or ""))))


def _latin_tokens(text):
    return {tok.casefold() for tok in _LATIN_TOKEN_RE.findall(str(text or ""))}


def hallucinated_facts(title, window_text):
    """Facts in the title with no support in the clip transcript.

    * every NUMBER in the title must appear in the window text;
    * every Latin name/brand token (≥3 chars) must appear in the window text.
    Returns a sorted list of unsupported fact strings ([] = clean).
    """
    window_numbers = _text_numbers(window_text)
    window_latin = _latin_tokens(window_text)
    unsupported = []
    for number in sorted(title_numbers(title)):
        if number not in window_numbers:
            unsupported.append(number)
    for token in sorted(_latin_tokens(title)):
        if token not in window_latin:
            unsupported.append(token)
    return unsupported


def factual_accuracy_score(title, window_text):
    """0-100: fraction of the title's checkable facts + content words grounded
    in the exact window transcript. Numbers/names count double."""
    title = str(title or "").strip()
    if not title:
        return 0.0
    window_tokens = set(_content_tokens(window_text))
    title_tokens = _content_tokens(title)
    unsupported = hallucinated_facts(title, window_text)
    checks = 0.0
    passed = 0.0
    # Hard facts (numbers, Latin names) — weight 2 each.
    for number in title_numbers(title):
        checks += 2.0
        if number in _text_numbers(window_text):
            passed += 2.0
    for token in _latin_tokens(title):
        checks += 2.0
        if token in _latin_tokens(window_text):
            passed += 2.0
    # Content words — weight 1 each.
    for token in title_tokens:
        checks += 1.0
        if token in window_tokens:
            passed += 1.0
    if checks == 0.0:
        # Title is pure stopwords/short words: nothing verifiable — mediocre,
        # not zero (a generic title is not automatically false).
        base = 50.0
    else:
        base = 100.0 * (passed / checks)
    if unsupported:
        base = min(base, 40.0)  # any invented hard fact caps factuality
    return round(max(0.0, min(100.0, base)), 1)


def transcript_relevance_score(title, window_text):
    """0-100 token-overlap between title and window transcript."""
    title_tokens = set(_content_tokens(title))
    if not title_tokens:
        return 0.0
    window_tokens = set(_content_tokens(window_text))
    if not window_tokens:
        return 0.0
    overlap = len(title_tokens & window_tokens) / len(title_tokens)
    return round(100.0 * overlap, 1)


def clarity_score(title):
    """Readability: sane length, no shouty punctuation, no word salad."""
    text = str(title or "").strip()
    if not text:
        return 0.0
    score = 55.0
    if 18 <= len(text) <= 72:
        score += 25.0
    elif len(text) > 110:
        score -= 20.0
    if text.count("!") > 2 or text.count("؟") > 2 or text.count("?") > 2:
        score -= 15.0
    if text.isupper() and any(c.isalpha() for c in text):
        score -= 20.0
    words = [w for w in re.split(r"\W+", text.casefold()) if len(w) > 2]
    if len(words) >= 4:
        counts = {}
        for word in words:
            counts[word] = counts.get(word, 0) + 1
        repeat = max(counts.values())
        if repeat >= 2:
            score -= 10.0 * (repeat - 1)
    return round(max(0.0, min(100.0, score)), 1)


def curiosity_score(title):
    """Information-gap appeal without fabrication: question form, hook words,
    a concrete number — the honest curiosity drivers."""
    text = str(title or "").strip()
    if not text:
        return 0.0
    score = 30.0
    if text.rstrip().endswith(("?", "؟")):
        score += 30.0
    if re.search(r"\d", arabic_text.normalize_digits(text)):
        score += 15.0
    if re.search(r"(?<!\w)(كيف|كيفاش|لماذا|علاش|سر|خطأ|أخطاء|حقيقة|طريقة"
                 r"|how|why|secret|mistake|truth)(?!\w)", text, re.IGNORECASE):
        score += 25.0
    return round(max(0.0, min(100.0, score)), 1)


def seo_score(title):
    """SEO proxy: delegates to the project's existing seo_titles scorer."""
    try:
        from scripts import seo_titles
        return float(seo_titles.score_title(str(title or "")).get("score", 0.0))
    except Exception:
        return 50.0


def language_match_score(title, content_language):
    """Title language vs the clip's spoken language.

    * exact match → 100; 'mixed' on either side → 70 (bilingual tolerated);
    * 'none' (digits/emoji only) → 60 (neutral);
    * 'ar' vs 'en' mismatch → 10 (audience mismatch).
    """
    content_language = str(content_language or "").strip().lower()
    title_script = detect_text_script(str(title or ""))
    if content_language in ("", "auto", "none") or title_script == "none":
        return 60.0
    if content_language == "mixed" or title_script == "mixed":
        return 70.0
    if content_language == title_script:
        return 100.0
    return 10.0


def score_title_candidate(title, window_text, content_language="auto"):
    """The full 8-part score for one title candidate (factual accuracy first)."""
    factual = factual_accuracy_score(title, window_text)
    relevance = transcript_relevance_score(title, window_text)
    clarity = clarity_score(title)
    curiosity = curiosity_score(title)
    seo = seo_score(title)
    language = language_match_score(title, content_language)
    clickbait_penalty = 0.0
    if contains_clickbait_pattern(title):
        clickbait_penalty += 30.0
    text = str(title or "")
    if text.count("!") > 2 or text.count("؟") > 2 or text.count("?") > 2:
        clickbait_penalty += 10.0
    final = (0.30 * factual + 0.20 * relevance + 0.15 * clarity
             + 0.10 * curiosity + 0.10 * seo + 0.15 * language
             - clickbait_penalty)
    return {
        "factual_accuracy_score": factual,
        "transcript_relevance_score": relevance,
        "clarity_score": clarity,
        "curiosity_score": curiosity,
        "SEO_score": round(seo, 1),
        "language_match_score": round(language, 1),
        "clickbait_penalty": round(clickbait_penalty, 1),
        "final_title_score": round(max(0.0, min(100.0, final)), 1),
    }


def validate_title(title, window_text, content_language="auto",
                   max_length=PUBLISH_TITLE_LIMIT):
    """The validation block shipped in title_data.validation."""
    title = str(title or "").strip()
    unsupported = hallucinated_facts(title, window_text)
    factual = factual_accuracy_score(title, window_text)
    language = language_match_score(title, content_language)
    clickbait = contains_clickbait_pattern(title)
    return {
        "matches_transcript": factual >= 60.0 and not unsupported,
        "contains_hallucinated_fact": bool(unsupported),
        "hallucinated_facts": unsupported,
        "language_matches_content": language >= 60.0,
        "within_length_limit": len(title) <= int(max_length),
        "excessive_clickbait": bool(clickbait and factual < 60.0),
    }


# A title is replaced by the deterministic fallback when it is factually
# unsupported, excessive clickbait, or in the wrong language entirely.
REPLACEMENT_FACTUAL_FLOOR = 45.0


def title_needs_replacement(title, window_text, content_language="auto"):
    validation = validate_title(title, window_text, content_language)
    if validation["contains_hallucinated_fact"]:
        return True, validation
    if validation["excessive_clickbait"]:
        return True, validation
    factual = factual_accuracy_score(title, window_text)
    if str(title or "").strip() and factual < REPLACEMENT_FACTUAL_FLOOR:
        # Only when the window actually has checkable content — an empty or
        # stopword-only transcript cannot disprove anything.
        if _content_tokens(window_text):
            return True, validation
    return False, validation


# ---------------------------------------------------------------------------
# Deterministic fallback titles (conservative, low confidence)
# ---------------------------------------------------------------------------

def _trim_words(text, limit=70):
    return fit_publish_title(str(text or "").strip(), limit)


def fallback_titles(window_analysis, content_language="auto", max_count=3):
    """Conservative descriptive titles built ONLY from the window transcript.

    * direct  — the first spoken sentence, word-boundary trimmed;
    * educational — the last spoken sentence (the conclusion), trimmed;
    * curiosity — a genuine question sentence from the clip, if one exists.
    Never invents facts: every word comes from the clip itself.
    """
    analysis = window_analysis if isinstance(window_analysis, dict) else {}
    first = str(analysis.get("first_sentence") or "").strip()
    last = str(analysis.get("last_sentence") or "").strip()
    text = str(analysis.get("text") or "").strip()
    candidates = []
    if first:
        candidates.append({"text": _trim_words(first), "style": "direct"})
    if last and last != first:
        candidates.append({"text": _trim_words(last), "style": "educational"})
    if len(candidates) < max_count:
        for sentence in re.split(r"(?<=[.!?؟…])\s+", text):
            sentence = sentence.strip()
            if sentence.endswith(("?", "؟")) and sentence not in (first, last):
                candidates.append({"text": _trim_words(sentence), "style": "curiosity"})
                break
    seen = set()
    unique = []
    for item in candidates:
        key = item["text"].casefold()
        if item["text"] and key not in seen:
            seen.add(key)
            unique.append(item)
        if len(unique) >= max_count:
            break
    if not unique and text:
        unique.append({"text": _trim_words(text), "style": "direct"})
    return unique


def detect_content_language(window_text):
    """Dominant spoken language of the clip window: 'ar' | 'en' | 'mixed' | 'none'."""
    return detect_text_script(str(window_text or ""))


# ---------------------------------------------------------------------------
# v7.41 — exact clip-window factual validation (spec item A)
# ---------------------------------------------------------------------------
#
# Every candidate title — including the AI's ``recommended_title`` — is
# validated against the EXACT clip-window transcript before it can ship. The
# core is deterministic and stdlib-only (no network, no LLM); ``llm_entailment``
# is an OPTIONAL hook that can only ADD a rejection, never remove one. When it
# is absent, raises, or returns None, the deterministic verdict stands.
#
# كل عنوان — بما فيه ``recommended_title`` القادم من الذكاء الاصطناعي — يُتحقق
# منه فعليًا مقابل نص نافذة المقطع بالضبط، دون أي اعتماد على الشبكة أو نموذج
# لغوي. التحقق حتمي وقابل للاختبار، وخطاف ``llm_entailment`` اختياري: إن أعاد
# False يُرفض العنوان، وإن غاب أو فشل نعود إلى النتيجة الحتمية بلا استثناءات.

TITLE_VALIDATION_SCHEMA_VERSION = "1.0"

# Factual thresholds on the 0-100 ``factual_accuracy_score`` scale.
MIN_VERIFY_FACTUAL = 60.0   # >= this + no unsupported facts → "verified"
MIN_REVIEW_FACTUAL = 45.0   # below this the title/window link is too weak

# Curated claim / superlative / result markers that ASSERT a fact. A marker
# that the title uses but the clip window never says is an unsupported claim.
# علامات الادّعاء/التفضيل/النتيجة: وجودها في العنوان دون وجودها في نص المقطع
# يعني ادّعاءً غير مدعوم.
_CLAIM_MARKERS = (
    # Arabic — superlatives / comparisons
    "الأفضل", "أفضل", "الأسرع", "أسرع", "الأكبر", "أكبر", "الأول", "أول",
    "الأعلى", "أعلى", "الأكثر", "أكثر",
    # Arabic — causation / result
    "بسبب", "نتيجة", "أدى", "أدت", "يؤدي", "يؤدي إلى",
    # Arabic — achievement / change / proof
    "حقق", "حققت", "زاد", "زادت", "ارتفع", "ارتفعت", "انخفض", "انخفضت",
    "وفّر", "وفر", "ربح", "ربحت", "خسر", "خسرت", "أثبت", "أثبتت",
    # Arabic — promotion / period promises
    "مجانًا", "مجانا", "خلال", "في أسبوع", "في يوم", "أضعاف",
    # English — superlatives / comparisons
    "best", "fastest", "biggest", "largest", "first", "most",
    # English — causation / result
    "because", "result", "caused", "cause",
    # English — achievement / change / proof
    "achieved", "increased", "decreased", "saved", "earned", "lost",
    "proved", "proven",
    # English — promotion / period promises
    "free", "in a week", "in a day", "times more",
)

# Amount / currency / period markers (normalized forms compare like words).
# عملات ومبالغ ومدد زمنية — وجودها في العنوان دون نص المقطع ادّعاء غير مدعوم.
_AMOUNT_MARKERS = (
    "ريال", "ريالات", "دولار", "دولارات", "درهم", "دينار", "جنيه", "يورو",
    "سنة", "سنوات", "عام", "أعوام", "شهر", "أشهر", "شهور",
    "أسبوع", "أسابيع", "يوم", "أيام",
    "years", "year", "months", "month", "weeks", "week", "days", "day",
    "$", "€", "%",
)

_ALL_CLAIM_MARKERS = tuple(dict.fromkeys(_CLAIM_MARKERS + _AMOUNT_MARKERS))

# "\d سنة" / "3 years" style date-amount phrases.
_DATE_UNIT_RE = re.compile(
    r"\d+\s*(?:years?|months?|weeks?|days?"
    r"|سنة|سنوات|عام|أعوام|شهر|أشهر|شهور|أسبوع|أسابيع|يوم|أيام)",
    re.IGNORECASE,
)

# Whole-source-video scope framing. Only a problem when the clip is a small
# part of the source (clip_ratio < 0.5) and the window itself does not frame
# the whole video. صياغات تُوهم بأن العنوان يغطي الفيديو كاملًا.
_SCOPE_MARKERS = (
    "في هذا الفيديو", "هذا الفيديو", "الفيديو", "الفيديو كامل",
    "المقطع كامل", "كل الفيديو", "الحلقة",
    "in this video", "this video", "the whole video", "whole video",
    "full video",
)

_ALT_TITLE_STYLES = (
    "direct", "curiosity", "benefit", "question", "emotional", "educational",
)


def _contains_phrase(norm_haystack, norm_needle):
    """Whole-word/phrase containment on already-normalized text.

    Prevents ``الاول`` from matching inside ``الاولي`` while still matching
    multi-word markers such as ``in a week``.
    """
    if not norm_haystack or not norm_needle:
        return False
    pattern = r"(?<!\w)" + re.escape(norm_needle) + r"(?!\w)"
    return re.search(pattern, norm_haystack) is not None


def _normalized_for_match(text):
    """Digits-normalized + orthography-unified comparison text."""
    return arabic_text.normalized_match_text(arabic_text.normalize_digits(text))


def unsupported_claims(title, window_text):
    """Claim / result / amount markers in `title` that the clip never says.

    Comparison is orthography-blind (hamza, diacritics and ta-marbuta variants
    compare equal) and digit-normalized, so ``٣`` == ``3`` and ``الأفضل`` ==
    ``الافضل``. Returns a SORTED list of the original marker strings found in
    the title but not in the window (``[]`` = every claim is grounded).

    العائد قائمة مرتّبة بعلامات الادّعاء الموجودة في العنوان دون نص المقطع.
    """
    title_raw = str(title or "")
    window_raw = str(window_text or "")
    norm_title = _normalized_for_match(title_raw)
    norm_window = _normalized_for_match(window_raw)

    unsupported = []
    seen_norm = set()
    for marker in _ALL_CLAIM_MARKERS:
        norm_marker = _normalized_for_match(marker)
        if not norm_marker:
            # Symbol marker (dollar / euro / percent): punctuation is
            # stripped by the matcher, so fall back to a raw glyph comparison.
            if marker in title_raw and marker not in window_raw:
                unsupported.append(marker)
            continue
        if norm_marker in seen_norm:
            continue  # e.g. "وفّر"/"وفر" and "مجانًا"/"مجانا" collapse
        seen_norm.add(norm_marker)
        if (_contains_phrase(norm_title, norm_marker)
                and not _contains_phrase(norm_window, norm_marker)):
            unsupported.append(marker)

    for match in _DATE_UNIT_RE.finditer(arabic_text.normalize_digits(title_raw)):
        phrase = match.group(0).strip()
        norm_phrase = arabic_text.normalized_match_text(phrase)
        if norm_phrase and not _contains_phrase(norm_window, norm_phrase):
            unsupported.append(phrase)

    return sorted(set(unsupported))


def _scope_markers_in(text):
    """Scope markers present in `text` (normalized, whole-phrase match)."""
    norm = _normalized_for_match(text)
    found = []
    for marker in _SCOPE_MARKERS:
        norm_marker = arabic_text.normalized_match_text(marker)
        if _contains_phrase(norm, norm_marker):
            found.append(marker)
    return found


def detect_full_video_scope(title, window_text, *, clip_ratio=None):
    """True when the title frames the WHOLE source video but covers only a clip.

    ``clip_ratio`` is the fraction of the source transcript covered by the clip
    (0-1). The scope framing must appear in the title and NOT in the clip
    window, and the clip must be a minority of the source (ratio < 0.5).

    With the default ``clip_ratio=None`` a scope marker ALONE is never enough:
    the ratio is genuinely unknown, so returning True would flag every honest
    "in this video" title. ``None`` → ``False``.

    يكشف العنوان الذي يوحي بتغطية الفيديو كاملًا بينما المقطع جزء صغير فقط؛
    وعند غياب ``clip_ratio`` لا يكفي وجود صيغة النطاق وحده.
    """
    if clip_ratio is None:
        return False
    try:
        ratio = float(clip_ratio)
    except (TypeError, ValueError):
        return False
    if ratio >= 0.5:
        return False
    if not _scope_markers_in(title):
        return False
    if _scope_markers_in(window_text):
        return False
    return True


def _resolve_language(content_language, window_text):
    language = str(content_language or "auto").strip().lower()
    if language in ("", "auto"):
        language = detect_content_language(window_text)
    return language


def validate_title_vs_clip(title, window_text, content_language="auto", *,
                           full_transcript_text=None, clip_ratio=None,
                           llm_entailment=None, max_length=PUBLISH_TITLE_LIMIT):
    """Factually validate ONE title against the EXACT clip-window transcript.

    Returns the v7.41 schema (see ``TITLE_VALIDATION_SCHEMA_VERSION``)::

        {"matches_clip_transcript": bool, "factual_consistency": float,
         "contains_unsupported_entity": bool, "contains_unsupported_number": bool,
         "contains_unsupported_claim": bool, "language_matches_clip": bool,
         "clickbait_penalty": float, "status": "verified|review|rejected",
         "reasons": [...], "checks": {...}}

    ``status`` policy:
      * rejected — any unsupported entity/number/claim, language mismatch,
        excessive clickbait (pattern + factual < 60), whole-video scope, or a
        window with checkable content whose relevance is < MIN_REVIEW_FACTUAL
        while the title carries content words the window never says;
      * review — passes the rejected gate but factual < MIN_VERIFY_FACTUAL,
        empty/generic title, or over ``max_length``;
      * verified — otherwise.

    ``llm_entailment`` is an OPTIONAL ``(title, window_text) -> bool|None``
    callable; returning False forces rejection, returning None / raising falls
    back to the deterministic verdict (never raises).

    يتحقّق العنوان فعليًا من نص نافذة المقطع؛ الحكم الحتمي أولًا ثم خطاف
    الاستلزام الاختياري.
    """
    title_text = str(title or "").strip()
    window_text = str(window_text or "")
    language = _resolve_language(content_language, window_text)

    window_tokens = set(_content_tokens(window_text))
    title_tokens = set(_content_tokens(title_text))

    unsupported_entities = sorted(
        token for token in _latin_tokens(title_text)
        if token not in _latin_tokens(window_text))
    unsupported_numbers = sorted(
        number for number in title_numbers(title_text)
        if number not in _text_numbers(window_text))
    claims = unsupported_claims(title_text, window_text)

    factual = factual_accuracy_score(title_text, window_text)
    relevance = transcript_relevance_score(title_text, window_text)
    language_score = language_match_score(title_text, language)
    language_matches = language_score >= 60.0

    clickbait = contains_clickbait_pattern(title_text)
    clickbait_penalty = 30.0 if clickbait else 0.0
    if (title_text.count("!") > 2 or title_text.count("؟") > 2
            or title_text.count("?") > 2):
        clickbait_penalty += 10.0

    full_video_scope = detect_full_video_scope(
        title_text, window_text, clip_ratio=clip_ratio)

    title_only_tokens = title_tokens - window_tokens
    low_relevance = (bool(window_tokens) and relevance < MIN_REVIEW_FACTUAL
                     and bool(title_only_tokens))

    # --- deterministic gate -------------------------------------------------
    reasons = []
    rejected = False
    if unsupported_entities:
        reasons.append("unsupported_entity: " + ", ".join(unsupported_entities))
        rejected = True
    if unsupported_numbers:
        reasons.append("unsupported_number: " + ", ".join(unsupported_numbers))
        rejected = True
    if claims:
        reasons.append("unsupported_claim: " + ", ".join(claims))
        rejected = True
    if not language_matches:
        reasons.append("language_mismatch: title={} clip={}".format(
            detect_text_script(title_text), language))
        rejected = True
    if clickbait and factual < MIN_VERIFY_FACTUAL:
        reasons.append("excessive_clickbait")
        rejected = True
    if full_video_scope:
        reasons.append("full_video_scope")
        rejected = True
    if low_relevance:
        reasons.append("low_transcript_relevance: {:.1f}".format(relevance))
        rejected = True

    # --- optional LLM entailment hook (can only add a rejection) ------------
    entailment = None
    if callable(llm_entailment):
        try:
            entailment = llm_entailment(title_text, window_text)
        except Exception:
            entailment = None
    if entailment is False:
        reasons.append("llm_entailment_failed")
        rejected = True

    has_verifiable_content = bool(
        title_tokens or title_numbers(title_text) or _latin_tokens(title_text)
        or claims)
    if rejected:
        status = "rejected"
    elif factual < MIN_VERIFY_FACTUAL:
        status = "review"
        reasons.append("factual_below_verify_threshold: {:.1f}".format(factual))
    elif not title_text:
        status = "review"
        reasons.append("empty_title")
    elif not has_verifiable_content:
        status = "review"
        reasons.append("title_has_no_verifiable_content")
    elif len(title_text) > int(max_length):
        status = "review"
        reasons.append("title_exceeds_length_limit: {}>{}".format(
            len(title_text), int(max_length)))
    else:
        status = "verified"

    checks = {
        "factual_accuracy_score": factual,
        "relevance": relevance,
        "full_video_scope": bool(full_video_scope),
        "hallucinated_facts": sorted(
            set(unsupported_entities) | set(unsupported_numbers)),
        "unsupported_entities": unsupported_entities,
        "unsupported_numbers": unsupported_numbers,
        "claims": claims,
        "language_score": language_score,
        "clickbait": bool(clickbait),
        "clip_ratio": clip_ratio,
        "llm_entailment": entailment,
        "full_transcript_provided": bool(full_transcript_text),
        "schema_version": TITLE_VALIDATION_SCHEMA_VERSION,
    }
    return {
        "matches_clip_transcript": bool(title_text) and status != "rejected",
        "factual_consistency": float(factual) / 100.0,
        "contains_unsupported_entity": bool(unsupported_entities),
        "contains_unsupported_number": bool(unsupported_numbers),
        "contains_unsupported_claim": bool(claims),
        "language_matches_clip": bool(language_matches),
        "clickbait_penalty": round(clickbait_penalty, 1),
        "status": status,
        "reasons": reasons,
        "checks": checks,
    }


def _candidate_text_and_style(candidate, index):
    """Coerce a title candidate (str or dict) into ``(text, style)``."""
    if isinstance(candidate, dict):
        text = str(candidate.get("text") or candidate.get("title") or "").strip()
        style = str(candidate.get("style") or "").strip()
    else:
        text = str(candidate or "").strip()
        style = ""
    if not style:
        style = _ALT_TITLE_STYLES[index % len(_ALT_TITLE_STYLES)]
    return text, style


def _window_analysis_from_text(window_text):
    """Minimal ``window_analysis`` for fallback titles from raw window text."""
    text = str(window_text or "").strip()
    parts = [part.strip() for part in re.split(r"(?<=[.!?؟…])\s+", text)
             if part.strip()]
    first = parts[0] if parts else text
    last = parts[-1] if parts else text
    return {"text": text, "first_sentence": first, "last_sentence": last}


def _pick_best_title(items):
    """Highest factual accuracy, then composite score, then earliest (stable)."""
    best = items[0]
    for item in items[1:]:
        if (item["scores"]["factual_accuracy_score"], item["score"]) > (
                best["scores"]["factual_accuracy_score"], best["score"]):
            best = item
    return best


def validate_all_title_candidates(recommended_title, alt_titles, window_text,
                                  content_language="auto", **kwargs):
    """Validate ``recommended_title`` AND every de-duplicated alt title.

    Returns::

        {"primary": {"text", "validation"},
         "alternatives": [{"text", "style", "validation", "score"}],  # non-rejected
         "rejected": [{"text", "validation"}],
         "review_required": bool,     # nothing verifies, or primary is "review"
         "verified_available": bool}

    When no candidate is verified a conservative transcript-derived title is
    built from the clip window and validated too; if even that is not verified
    ``review_required`` stays True — an unverified title is never silently
    shipped.

    يتحقّق من العنوان الموصى به وكل العناوين البديلة، ويعرض البديل المحافظ
    المستخلص من نص المقطع عند غياب أي عنوان موثوق.
    """
    language = _resolve_language(content_language, window_text)
    options = dict(kwargs)
    limit = int(options.get("max_length", PUBLISH_TITLE_LIMIT))

    primary_text = fit_publish_title(str(recommended_title or "").strip(), limit)
    primary_validation = validate_title_vs_clip(
        primary_text, window_text, language, **options)

    alternatives = []
    rejected = []
    seen = set()
    if primary_text:
        seen.add(primary_text.casefold())
    if primary_text and primary_validation["status"] == "rejected":
        rejected.append({"text": primary_text, "validation": primary_validation})

    order = 0
    for index, candidate in enumerate(list(alt_titles or [])):
        text, style = _candidate_text_and_style(candidate, index)
        text = fit_publish_title(text, limit)
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        validation = validate_title_vs_clip(text, window_text, language, **options)
        if validation["status"] == "rejected":
            rejected.append({"text": text, "validation": validation})
            continue
        alternatives.append({
            "text": text,
            "style": style,
            "validation": validation,
            "score": score_title_candidate(text, window_text, language)["final_title_score"],
            "_order": order,
        })
        order += 1

    verified_available = primary_validation["status"] == "verified" or any(
        item["validation"]["status"] == "verified" for item in alternatives)

    if not verified_available:
        fallbacks = fallback_titles(
            _window_analysis_from_text(window_text), language, max_count=5)
        for index, item in enumerate(fallbacks):
            text = fit_publish_title(str(item.get("text") or ""), limit)
            if not text or text.casefold() in seen:
                continue
            seen.add(text.casefold())
            validation = validate_title_vs_clip(text, window_text, language, **options)
            if validation["status"] == "rejected":
                continue
            alternatives.append({
                "text": text,
                "style": str(item.get("style") or _ALT_TITLE_STYLES[
                    index % len(_ALT_TITLE_STYLES)]),
                "validation": validation,
                "score": score_title_candidate(
                    text, window_text, language)["final_title_score"],
                "_order": order,
            })
            order += 1
            if validation["status"] == "verified":
                verified_available = True
                break

    alternatives.sort(key=lambda item: (
        -item["validation"]["checks"]["factual_accuracy_score"],
        -item["score"], item["_order"]))
    for item in alternatives:
        item.pop("_order", None)

    review_required = (not verified_available
                       or primary_validation["status"] == "review")
    return {
        "primary": {"text": primary_text, "validation": primary_validation},
        "alternatives": alternatives,
        "rejected": rejected,
        "review_required": bool(review_required),
        "verified_available": bool(verified_available),
    }


def build_title_data(recommended_title, alt_titles, window_text,
                     window_analysis, content_language="auto", *,
                     clip_ratio=None, llm_entailment=None):
    """Assemble the full ``title_data`` block for one segment.

    Every candidate (including ``recommended_title``) is validated against the
    exact clip window with :func:`validate_title_vs_clip`. The primary title is
    chosen from VERIFIED candidates first (factual-accuracy tie-break), then
    ``review`` candidates, then the conservative transcript-derived fallback.
    Only non-rejected titles reach ``alternative_titles``; rejected ones are
    kept in ``rejected_titles`` for audit.

    Both the legacy ``validation`` block (unchanged shape) and the new
    ``title_validation`` schema are returned, plus ``title_review_required`` and
    ``schema_version``. ``title_review_required`` is True whenever the shipped
    primary is not fully verified — an unverified title must be reviewed, not
    silently published.

    يتحقّق من كل عنوان مقابل نص نافذة المقطع، يختار العنوان الأساسي من الموثوق
    أولًا ثم المراجَع ثم البديل المحافظ، ويضع العناوين غير المرفوضة فقط في
    ``alternative_titles`` مع إبقاء المرفوضة في ``rejected_titles``.
    """
    language = _resolve_language(content_language, window_text)
    window_text = str(window_text or "")
    options = {"clip_ratio": clip_ratio, "llm_entailment": llm_entailment}

    pool = []
    seen = set()
    for index, candidate in enumerate([recommended_title] + list(alt_titles or [])):
        text, style = _candidate_text_and_style(candidate, index)
        text = fit_publish_title(text, PUBLISH_TITLE_LIMIT)
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        scores = score_title_candidate(text, window_text, language)
        pool.append({
            "text": text,
            "style": style,
            "score": scores["final_title_score"],
            "scores": scores,
            "validation": validate_title_vs_clip(
                text, window_text, language, **options),
        })

    verified = [item for item in pool
                if item["validation"]["status"] == "verified"]
    review = [item for item in pool
              if item["validation"]["status"] == "review"]

    fallback_used = False
    fallback_pool = []
    if verified:
        best = _pick_best_title(verified)
        confidence = best["scores"]["factual_accuracy_score"]
    elif review:
        best = _pick_best_title(review)
        confidence = best["scores"]["factual_accuracy_score"]
    else:
        fallback_used = True
        fallbacks = fallback_titles(window_analysis, language)
        if not fallbacks:
            fallbacks = [{"text": "Viral Segment", "style": "direct"}]
        for index, item in enumerate(fallbacks):
            text = fit_publish_title(str(item.get("text") or ""), PUBLISH_TITLE_LIMIT)
            if not text:
                continue
            scores = score_title_candidate(text, window_text, language)
            fallback_pool.append({
                "text": text,
                "style": str(item.get("style") or _ALT_TITLE_STYLES[
                    index % len(_ALT_TITLE_STYLES)]),
                "score": scores["final_title_score"],
                "scores": scores,
                "validation": validate_title_vs_clip(
                    text, window_text, language, **options),
            })
        if not fallback_pool:
            scores = score_title_candidate("Viral Segment", window_text, language)
            fallback_pool.append({
                "text": "Viral Segment",
                "style": "direct",
                "score": scores["final_title_score"],
                "scores": scores,
                "validation": validate_title_vs_clip(
                    "Viral Segment", window_text, language, **options),
            })
        verified_fallbacks = [item for item in fallback_pool
                              if item["validation"]["status"] == "verified"]
        best = verified_fallbacks[0] if verified_fallbacks else fallback_pool[0]
        confidence = 35.0  # ambiguous/unsupported transcript → low confidence

    all_items = pool + fallback_pool
    rejected_titles = []
    rejected_seen = set()
    for item in all_items:
        if item["validation"]["status"] != "rejected":
            continue
        if item["text"].casefold() in rejected_seen:
            continue
        rejected_seen.add(item["text"].casefold())
        rejected_titles.append({"text": item["text"],
                                "validation": item["validation"]})

    candidates = [item for item in all_items
                  if item["text"] != best["text"]
                  and item["validation"]["status"] != "rejected"]
    candidates.sort(key=lambda item: (
        -item["validation"]["checks"]["factual_accuracy_score"],
        -item["score"]))
    alternative_titles = [
        {"text": item["text"], "style": item["style"], "score": item["score"]}
        for item in candidates[:5]
    ]
    validated_alternatives = [
        {"text": item["text"], "style": item["style"], "score": item["score"],
         "validation": item["validation"]}
        for item in candidates
    ]

    validation = validate_title(best["text"], window_text, language)
    title_validation = best["validation"]
    return {
        "primary_title": best["text"],
        "title_language": language,
        "title_confidence": round(float(confidence), 1),
        "title_is_factual": bool(validation["matches_transcript"]),
        "fallback_used": bool(fallback_used),
        "title_scores": best["scores"],
        "alternative_titles": alternative_titles,
        "validation": validation,
        "title_validation": title_validation,
        "validated_alternatives": validated_alternatives,
        "rejected_titles": rejected_titles,
        "title_review_required": bool(title_validation["status"] != "verified"),
        "schema_version": TITLE_VALIDATION_SCHEMA_VERSION,
    }
