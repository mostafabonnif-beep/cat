# -*- coding: utf-8 -*-
"""v7.41 — exact clip-window factual title validation (spec item A).

Covers ``scripts/title_factual.py`` v7.41 additions:

* deterministic claim / scope / entity / number gates against the EXACT clip
  window (never the full source transcript),
* the ``validate_title_vs_clip`` schema + status policy,
* ``validate_all_title_candidates`` (recommended + alt titles + fallback),
* ``build_title_data`` dual-schema output and review flag.

All tests are deterministic and stdlib-only; no network, no LLM is required.

اختبارات التحقق الفعلي للعناوين مقابل نص نافذة المقطع بالضبط — حتمية وبلا شبكة.
"""

from scripts import title_factual as tf

WINDOW = "الخطوة الأولى هي اختيار مهارة واحدة وإتقانها بعمق"


def _analysis(text=WINDOW):
    """Minimal window_analysis dict for the conservative fallback path."""
    return {"text": text, "first_sentence": text, "last_sentence": text}


# ---------------------------------------------------------------------------
# Constants / claim detection
# ---------------------------------------------------------------------------

def test_schema_constants_are_pinned():
    assert tf.TITLE_VALIDATION_SCHEMA_VERSION == "1.0"
    assert tf.MIN_VERIFY_FACTUAL == 60.0
    assert tf.MIN_REVIEW_FACTUAL == 45.0


def test_unsupported_claims_are_orthography_and_digit_blind():
    # Diacritic/hamza variants compare equal to the clip's own spelling.
    assert tf.unsupported_claims("الشحن مجانا", "الشحن مجانًا اليوم") == []
    # A claim the clip never makes is reported (original marker string).
    assert tf.unsupported_claims("الشحن مجانًا", "المنتج مدفوع بالكامل") == ["مجانًا"]
    # Arabic-Indic digits in the clip satisfy an ASCII-digit claim.
    assert tf.unsupported_claims("وفّر 3 أيام", "وفّر ٣ أيام من وقتك") == []


def test_unsupported_claims_covers_arabic_and_english_markers():
    window = "خطوات النجاح في العمل الحر"
    assert "أفضل" in tf.unsupported_claims("أفضل خطوات النجاح", window)
    assert "best" in tf.unsupported_claims("best steps to success", window)
    assert tf.unsupported_claims("خطوات النجاح", window) == []


# ---------------------------------------------------------------------------
# Whole-video scope
# ---------------------------------------------------------------------------

def test_scope_marker_alone_is_not_enough_without_clip_ratio():
    assert tf.detect_full_video_scope("ملخص كل الفيديو", "جزء صغير") is False


def test_scope_detected_only_for_a_small_clip_ratio():
    assert tf.detect_full_video_scope("ملخص كل الفيديو", "جزء صغير", clip_ratio=0.1) is True
    # The window itself frames the video → no false positive.
    assert tf.detect_full_video_scope(
        "ملخص كل الفيديو", "في هذا الفيديو نتحدث عن نقطة واحدة", clip_ratio=0.1) is False
    # The clip covers most of the source → not a whole-video claim.
    assert tf.detect_full_video_scope("ملخص كل الفيديو", "جزء صغير", clip_ratio=0.9) is False


# ---------------------------------------------------------------------------
# validate_title_vs_clip status policy
# ---------------------------------------------------------------------------

def test_unsupported_number_is_rejected():
    validation = tf.validate_title_vs_clip("خطوات النجاح 99", WINDOW, "ar")
    assert validation["status"] == "rejected"
    assert validation["contains_unsupported_number"] is True
    assert "99" in validation["checks"]["unsupported_numbers"]
    assert any("number" in reason for reason in validation["reasons"])


def test_unsupported_entity_is_rejected():
    window = "spoke about freelancing and daily marketing"
    validation = tf.validate_title_vs_clip(
        "How Elon Musk freelances", window, "en")
    assert validation["status"] == "rejected"
    assert validation["contains_unsupported_entity"] is True
    assert "elon" in validation["checks"]["unsupported_entities"]


def test_full_video_scope_title_is_rejected():
    validation = tf.validate_title_vs_clip(
        "ملخص كل الفيديو كامل", "جزء صغير عن المهارة الأولى", "ar", clip_ratio=0.1)
    assert validation["status"] == "rejected"
    assert validation["checks"]["full_video_scope"] is True
    assert "full_video_scope" in validation["reasons"]


def test_false_claim_marker_is_rejected():
    window = "خطوات النجاح في العمل الحر"
    validation = tf.validate_title_vs_clip(
        "أفضل خطوات النجاح في العمل الحر", window, "ar")
    assert validation["status"] == "rejected"
    assert validation["contains_unsupported_claim"] is True
    assert "أفضل" in validation["checks"]["claims"]
    assert any("claim" in reason for reason in validation["reasons"])


def test_arabic_digits_and_hamza_variants_validate_as_grounded():
    window = "سافر أحمد ٣ أيام إلى الرياض"
    title = "سافر احمد 3 ايام الي الرياض"
    validation = tf.validate_title_vs_clip(title, window, "ar")
    assert validation["status"] == "verified"
    assert validation["contains_unsupported_number"] is False
    assert validation["contains_unsupported_claim"] is False
    assert validation["matches_clip_transcript"] is True


def test_mixed_language_title_is_tolerated_but_ar_title_vs_en_clip_is_not():
    mixed_window = "نتحدث اليوم عن العمل freelancing وأسرار النجاح"
    mixed = tf.validate_title_vs_clip(
        "العمل freelancing وأسرار النجاح", mixed_window, "auto")
    assert mixed["language_matches_clip"] is True
    assert mixed["status"] == "verified"

    english_window = "today we discuss freelancing tips and daily success"
    mismatch = tf.validate_title_vs_clip(
        "العمل الحر والنجاح اليومي", english_window, "en")
    assert mismatch["language_matches_clip"] is False
    assert mismatch["status"] == "rejected"
    assert any("language" in reason for reason in mismatch["reasons"])


def test_verified_title_schema_and_status():
    validation = tf.validate_title_vs_clip(
        "الخطوة الأولى اختيار مهارة واحدة", WINDOW, "ar")
    assert validation["status"] == "verified"
    assert validation["matches_clip_transcript"] is True
    assert validation["factual_consistency"] >= 0.6
    assert validation["reasons"] == []
    assert validation["checks"]["schema_version"] == tf.TITLE_VALIDATION_SCHEMA_VERSION
    required = {
        "matches_clip_transcript", "factual_consistency",
        "contains_unsupported_entity", "contains_unsupported_number",
        "contains_unsupported_claim", "language_matches_clip",
        "clickbait_penalty", "status", "reasons", "checks",
    }
    assert required <= set(validation)


def test_empty_and_generic_titles_go_to_review():
    assert tf.validate_title_vs_clip("", WINDOW, "ar")["status"] == "review"
    # Stopword-only title: nothing verifiable, but nothing false either.
    generic = tf.validate_title_vs_clip("هذا من في على", WINDOW, "ar")
    assert generic["status"] == "review"


def test_low_relevance_with_foreign_content_words_is_rejected():
    validation = tf.validate_title_vs_clip(
        "وصفة طبخ إيطالية سهلة", WINDOW, "ar")
    assert validation["status"] == "rejected"
    assert any("relevance" in reason for reason in validation["reasons"])


# ---------------------------------------------------------------------------
# validate_all_title_candidates
# ---------------------------------------------------------------------------

def test_hallucinated_alternative_is_excluded_and_rejected():
    result = tf.validate_all_title_candidates(
        "الخطوة الأولى اختيار مهارة واحدة",
        ["عنوان وهمي 77", "إتقان مهارة واحدة بعمق"],
        WINDOW, "ar")

    alternative_texts = [item["text"] for item in result["alternatives"]]
    rejected_texts = [item["text"] for item in result["rejected"]]

    assert "عنوان وهمي 77" not in alternative_texts
    assert "عنوان وهمي 77" in rejected_texts
    assert "إتقان مهارة واحدة بعمق" in alternative_texts
    assert result["verified_available"] is True
    assert result["review_required"] is False
    for item in result["alternatives"]:
        assert isinstance(item["score"], float)
        assert item["validation"]["status"] != "rejected"


def test_validate_all_reports_review_required_when_nothing_verifies():
    # Empty window → no verifiable transcript to derive a fallback from, so
    # review is forced instead of silently shipping an unverified title.
    result = tf.validate_all_title_candidates(
        "عنوان لا علاقة له بالمحتوى إطلاقا", [], "", "ar")
    assert result["verified_available"] is False
    assert result["review_required"] is True
    assert result["primary"]["validation"]["status"] != "verified"


def test_validate_all_uses_transcript_fallback_before_giving_up():
    window = "الخطوة الأولى هي اختيار مهارة واحدة وإتقانها بعمق"
    result = tf.validate_all_title_candidates("عنوان وهمي 77", [], window, "ar")
    # The conservative window-derived candidate verifies, so review is not forced.
    assert result["verified_available"] is True
    assert result["review_required"] is False
    assert result["alternatives"]
    assert any(item["validation"]["status"] == "verified"
               for item in result["alternatives"])


# ---------------------------------------------------------------------------
# build_title_data dual schema + selection
# ---------------------------------------------------------------------------

def test_build_title_data_replaces_unsupported_recommended_title():
    data = tf.build_title_data("خطوات النجاح 99", [], WINDOW, _analysis(), "ar")
    assert "99" not in data["primary_title"]
    assert data["fallback_used"] is True
    rejected = {item["text"]: item["validation"] for item in data["rejected_titles"]}
    assert "خطوات النجاح 99" in rejected
    assert rejected["خطوات النجاح 99"]["status"] == "rejected"
    assert any("number" in reason
               for reason in rejected["خطوات النجاح 99"]["reasons"])


def test_build_title_data_prefers_verified_alternative():
    data = tf.build_title_data(
        "عنوان وهمي 77", ["إتقان مهارة واحدة بعمق"], WINDOW, _analysis(), "ar")
    assert data["primary_title"] == "إتقان مهارة واحدة بعمق"
    assert data["fallback_used"] is False
    assert data["title_validation"]["status"] == "verified"
    assert data["title_review_required"] is False
    assert [item["text"] for item in data["rejected_titles"]] == ["عنوان وهمي 77"]
    assert [item["text"] for item in data["alternative_titles"]] == []


def test_build_title_data_only_non_rejected_candidates_in_alternatives():
    data = tf.build_title_data(
        "الخطوة الأولى اختيار مهارة واحدة",
        ["عنوان وهمي 77", "إتقان مهارة واحدة بعمق"],
        WINDOW, _analysis(), "ar")
    alternative_texts = [item["text"] for item in data["alternative_titles"]]
    assert alternative_texts == ["إتقان مهارة واحدة بعمق"]
    assert "عنوان وهمي 77" not in alternative_texts


def test_build_title_data_returns_legacy_and_new_validation_blocks():
    data = tf.build_title_data(
        "الخطوة الأولى اختيار مهارة واحدة", [], WINDOW, _analysis(), "ar")

    legacy = data["validation"]
    assert {
        "matches_transcript", "contains_hallucinated_fact", "hallucinated_facts",
        "language_matches_content", "within_length_limit", "excessive_clickbait",
    } <= set(legacy)

    new = data["title_validation"]
    assert new["status"] == "verified"
    assert new["checks"]["schema_version"] == tf.TITLE_VALIDATION_SCHEMA_VERSION

    assert data["schema_version"] == tf.TITLE_VALIDATION_SCHEMA_VERSION
    assert data["title_review_required"] is False
    # Legacy keys that existing callers depend on stay present.
    assert {
        "primary_title", "title_language", "title_confidence", "title_is_factual",
        "fallback_used", "title_scores", "alternative_titles",
    } <= set(data)


def test_build_title_data_marks_review_required_when_nothing_verifies():
    data = tf.build_title_data("", [], "", {}, "auto")
    assert data["title_review_required"] is True
    assert data["title_validation"]["status"] != "verified"
    assert data["primary_title"]  # never empty, but flagged for review


# ---------------------------------------------------------------------------
# Optional LLM entailment hook
# ---------------------------------------------------------------------------

def test_llm_entailment_false_forces_rejection():
    validation = tf.validate_title_vs_clip(
        "الخطوة الأولى اختيار مهارة واحدة", WINDOW, "ar",
        llm_entailment=lambda *args: False)
    assert validation["status"] == "rejected"
    assert "llm_entailment_failed" in validation["reasons"]


def test_llm_entailment_none_and_raising_fall_back_to_deterministic():
    none_verdict = tf.validate_title_vs_clip(
        "الخطوة الأولى اختيار مهارة واحدة", WINDOW, "ar",
        llm_entailment=lambda *args: None)
    assert none_verdict["status"] == "verified"

    def boom(*args, **kwargs):
        raise RuntimeError("entailment backend down")

    raising = tf.validate_title_vs_clip(
        "الخطوة الأولى اختيار مهارة واحدة", WINDOW, "ar", llm_entailment=boom)
    assert raising["status"] == "verified"

    # The same failure isolation must hold through the aggregate helper.
    result = tf.validate_all_title_candidates(
        "الخطوة الأولى اختيار مهارة واحدة", [], WINDOW, "ar",
        llm_entailment=boom)
    assert result["primary"]["validation"]["status"] == "verified"
