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


def build_title_data(recommended_title, alt_titles, window_text,
                     window_analysis, content_language="auto"):
    """Assemble the full ``title_data`` block for one segment.

    Picks the primary title by FACTUAL ACCURACY first across the LLM's own
    candidates; when every candidate fails validation, falls back to a
    conservative transcript-derived title with low confidence.
    """
    language = str(content_language or "auto").strip().lower()
    if language in ("", "auto"):
        language = detect_content_language(window_text)

    pool = []
    seen = set()
    for candidate in [recommended_title] + list(alt_titles or []):
        text = str(candidate or "").strip()
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            pool.append(text)

    scored = []
    for text in pool:
        scores = score_title_candidate(text, window_text, language)
        scored.append({"text": fit_publish_title(text, PUBLISH_TITLE_LIMIT),
                       "score": scores["final_title_score"], "scores": scores})

    usable = []
    for item in scored:
        needs_replacement, _v = title_needs_replacement(item["text"], window_text, language)
        if not needs_replacement:
            usable.append(item)

    fallback_used = False
    if usable:
        # Factual accuracy first, then the final composite; stable order.
        best = max(enumerate(usable),
                   key=lambda pair: (pair[1]["scores"]["factual_accuracy_score"],
                                     pair[1]["score"], -pair[0]))[1]
        confidence = best["scores"]["factual_accuracy_score"]
    else:
        fallback_used = True
        fallbacks = fallback_titles(window_analysis, language)
        if not fallbacks:
            fallbacks = [{"text": "Viral Segment", "style": "direct"}]
        best = {"text": fallbacks[0]["text"],
                "score": score_title_candidate(fallbacks[0]["text"], window_text, language)["final_title_score"],
                "scores": score_title_candidate(fallbacks[0]["text"], window_text, language)}
        confidence = 35.0  # ambiguous/unsupported transcript → low confidence

    alternatives = []
    styles = ["direct", "curiosity", "benefit", "question", "emotional", "educational"]
    fallback_pool = fallback_titles(window_analysis, language, max_count=4) if fallback_used else []
    style_index = 0
    for item in scored:
        if item["text"] == best["text"]:
            continue
        style = styles[style_index % len(styles)]
        style_index += 1
        alternatives.append({"text": item["text"], "style": style,
                             "score": item["score"]})
    for item in fallback_pool:
        if len(alternatives) >= 5:
            break
        if item["text"] == best["text"] or any(a["text"] == item["text"] for a in alternatives):
            continue
        alternatives.append({"text": item["text"], "style": item["style"],
                             "score": score_title_candidate(item["text"], window_text, language)["final_title_score"]})

    validation = validate_title(best["text"], window_text, language)
    return {
        "primary_title": best["text"],
        "title_language": language,
        "title_confidence": round(float(confidence), 1),
        "title_is_factual": bool(validation["matches_transcript"]),
        "fallback_used": fallback_used,
        "title_scores": best["scores"],
        "alternative_titles": alternatives[:5],
        "validation": validation,
    }
