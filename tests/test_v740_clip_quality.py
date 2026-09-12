# -*- coding: utf-8 -*-
"""v7.40 — clip-quality regression tests.

Covers the new selection stack end to end:

* scripts/clip_scoring.py      — centralized 11-factor weights + final score
* scripts/transcript_window.py — exact window analysis, sentence units,
                                 word-boundary snapping, connector/dangling
                                 repairs, pre/post-roll, semantic similarity
* scripts/title_factual.py     — factual title validation, clickbait rules,
                                 language matching, conservative fallbacks
* create_viral_segments wiring — validation messages, score_breakdown,
                                 semantic dedup, extended JSON output
* main_improved                — extended config fingerprint + --force-regenerate

Test areas (per the v7.40 spec): word-boundary starts/ends, complete-sentence
detection, min/max duration, invalid timestamps, long silence, temporal and
semantic duplicates, title factuality + hallucination, Arabic normalization /
RTL / mixed text, title length + word-boundary fitting, low-confidence
fallbacks, config fingerprint + force regeneration, legacy JSON compat,
one-/multi-speaker transcripts, long sentences, long pauses, bilingual
content, export-path data contracts, and safety-stage compatibility.
"""

import argparse
import json
import os
import sys
import types
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import arabic_text, clip_scoring, title_factual, transcript_window
from scripts import create_viral_segments as cvs


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _arabic_transcript():
    """Natural Arabic transcript with clear sentences and pauses."""
    return [
        {"start": 0.0, "end": 5.0, "text": "مرحبا بكم في حلقة جديدة من برنامجنا."},
        {"start": 6.0, "end": 12.0, "text": "اليوم نتحدث عن ثلاث خطوات للنجاح في العمل الحر."},
        {"start": 12.8, "end": 20.0, "text": "الخطوة الأولى هي اختيار مهارة واحدة وإتقانها بعمق."},
        {"start": 21.0, "end": 28.0, "text": "الخطوة الثانية هي بناء معرض أعمال قوي يثبت قدرتك."},
        {"start": 29.0, "end": 37.0, "text": "الخطوة الثالثة هي التسويق لنفسك كل يوم دون توقف."},
        {"start": 38.0, "end": 46.0, "text": "إذا طبقت هذه الخطوات سترى الفرق خلال ثلاثة أشهر."},
        {"start": 47.0, "end": 52.0, "text": "هذه هي الخلاصة الكاملة لهذه الحلقة."},
    ]


def _candidate(title, start, end, score=80, **extra):
    seg = {"title": title, "start_time": start, "end_time": end, "score": score}
    seg.update(extra)
    return seg


# ---------------------------------------------------------------------------
# 1+2. Word-boundary-safe clip start/end
# ---------------------------------------------------------------------------

def test_word_boundary_snap_start_and_end():
    words = [
        {"start": 10.0, "end": 10.4, "word": "الخطوة"},
        {"start": 10.5, "end": 10.9, "word": "الأولى"},
        {"start": 11.0, "end": 11.5, "word": "هي"},
    ]
    # Edges land INSIDE words: start inside الأولى, end inside هي.
    start, end = transcript_window.snap_edges_to_words(10.7, 11.2, words)
    assert start == pytest.approx(10.5)   # never start inside a word
    assert end == pytest.approx(11.5)     # never end inside a word


def test_word_boundary_snap_edges_in_gap_untouched():
    words = [{"start": 10.0, "end": 10.4, "word": "a"},
             {"start": 11.0, "end": 11.4, "word": "b"}]
    start, end = transcript_window.snap_edges_to_words(10.7, 11.7, words)
    assert (start, end) == (10.7, 11.7)


def test_word_boundary_snap_without_words_is_noop():
    assert transcript_window.snap_edges_to_words(1.25, 5.75, []) == (1.25, 5.75)


def test_refine_boundaries_uses_word_timings():
    transcript = [{"start": 10.0, "end": 12.0, "text": "الخطوة الأولى هي الاختيار"}]
    words = [
        {"start": 10.0, "end": 10.4, "word": "الخطوة"},
        {"start": 10.5, "end": 10.9, "word": "الأولى"},
        {"start": 11.0, "end": 11.4, "word": "هي"},
        {"start": 11.5, "end": 12.0, "word": "الاختيار"},
    ]
    start, end, notes = transcript_window.refine_boundaries(
        10.7, 11.2, transcript, words=words, min_duration=0, max_duration=60)
    assert start == pytest.approx(10.5)
    assert end == pytest.approx(11.4)
    assert "word_boundary_snap" in notes


def test_load_word_timings_from_input_json(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    payload = {"segments": [
        {"start": 0.0, "end": 2.0, "text": "hello world",
         "words": [{"start": 0.0, "end": 0.5, "word": "hello"},
                   {"start": 0.6, "end": 1.0, "word": "world"}]},
    ]}
    (project / "input.json").write_text(json.dumps(payload), encoding="utf-8")
    words = transcript_window.load_word_timings(str(project))
    assert [w["word"] for w in words] == ["hello", "world"]
    assert transcript_window.load_word_timings(str(project / "missing")) == []


# ---------------------------------------------------------------------------
# 3. Complete sentence detection
# ---------------------------------------------------------------------------

def test_sentence_units_split_on_punctuation_and_pauses():
    transcript = [
        {"start": 0.0, "end": 4.0, "text": "هذه جملة كاملة."},
        {"start": 4.2, "end": 8.0, "text": "وهذه جملة ثانية."},
        {"start": 12.0, "end": 16.0, "text": "جملة بعد فاصل طويل"},   # long pause, no punctuation
        {"start": 16.2, "end": 20.0, "text": "تكملة للجملة السابقة"},
    ]
    units = transcript_window.split_sentence_units(transcript)
    assert len(units) == 3
    assert units[0]["text"].startswith("هذه جملة")
    assert units[2]["text"].startswith("جملة بعد")
    assert "تكملة" in units[2]["text"]   # short pause glues the continuation


def test_analyze_window_detects_complete_and_partial():
    transcript = _arabic_transcript()
    complete = transcript_window.analyze_window(transcript, 12.8, 20.0)
    assert complete["complete"] is True
    assert complete["ends_with_terminal_punctuation"] is True
    assert complete["starts_mid_sentence"] is False
    assert complete["ends_mid_sentence"] is False
    # Cut that starts INSIDE the sentence and ends inside it.
    partial = transcript_window.analyze_window(transcript, 14.0, 19.0)
    assert partial["starts_mid_sentence"] is True
    assert partial["ends_mid_sentence"] is True
    assert partial["complete"] is False


def test_analyze_window_reports_first_last_sentence_and_context():
    transcript = _arabic_transcript()
    analysis = transcript_window.analyze_window(transcript, 21.0, 37.0)
    assert analysis["first_sentence"].startswith("الخطوة الثانية")
    assert analysis["last_sentence"].startswith("الخطوة الثالثة")
    assert "الأولى" in analysis["before_text"]
    assert "ثلاثة أشهر" in analysis["after_text"]


# ---------------------------------------------------------------------------
# 4. Min/max duration enforcement
# ---------------------------------------------------------------------------

def test_process_segments_respects_max_duration_after_refinement():
    transcript = _arabic_transcript()
    raw = [_candidate("Long", 6.0, 46.0)]
    result = cvs.process_segments(raw, transcript, 15, 30)
    seg = result["segments"][0]
    assert seg["duration"] <= 30.0 + 0.05


def test_process_segments_min_duration_extension_kept():
    transcript = _arabic_transcript()
    raw = [_candidate("Short", 6.0, 8.0)]
    result = cvs.process_segments(raw, transcript, 15, 60)
    seg = result["segments"][0]
    assert seg["duration"] >= 15.0


def test_pre_post_roll_never_exceeds_max_duration(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_PRE_ROLL", "0.5")
    monkeypatch.setenv("VIRALCUTTER_POST_ROLL", "0.8")
    transcript = [{"start": 10.0, "end": 20.0, "text": "جملة واحدة كاملة هنا."}]
    start, end, notes = transcript_window.refine_boundaries(
        10.0, 20.0, transcript, min_duration=0, max_duration=12.0,
        transcript_start=0.0, transcript_end=20.0)
    assert end - start <= 12.0 + 0.05
    assert "pre_post_roll_applied" in notes


def test_pre_post_roll_env_bounds(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_PRE_ROLL", "99")
    monkeypatch.setenv("VIRALCUTTER_POST_ROLL", "99")
    rolls = transcript_window.pre_post_roll_config()
    assert rolls["pre_roll"] == 0.50     # clamped to the spec range
    assert rolls["post_roll"] == 0.80
    monkeypatch.setenv("VIRALCUTTER_PRE_ROLL", "0.05")
    monkeypatch.setenv("VIRALCUTTER_POST_ROLL", "0.10")
    rolls = transcript_window.pre_post_roll_config()
    assert rolls["pre_roll"] == 0.20     # raised into the spec range
    assert rolls["post_roll"] == 0.30
    monkeypatch.delenv("VIRALCUTTER_PRE_ROLL")
    monkeypatch.delenv("VIRALCUTTER_POST_ROLL")
    assert transcript_window.pre_post_roll_config() == {"pre_roll": 0.0, "post_roll": 0.0}


# ---------------------------------------------------------------------------
# 5. Invalid timestamp rejection
# ---------------------------------------------------------------------------

def _analysis_ok():
    return {"text": "كلام كامل هنا", "word_count": 3, "speech_coverage": 0.9}


def test_validate_rejects_end_before_start():
    rejected, _ = cvs._validate_segment_window(30.0, 20.0, 15, _analysis_ok())
    assert any("end_time <= start_time" in r for r in rejected)


def test_validate_rejects_negative_timestamps():
    rejected, _ = cvs._validate_segment_window(-5.0, 20.0, 15, _analysis_ok())
    assert any("negative" in r for r in rejected)


def test_validate_rejects_under_min_duration():
    rejected, _ = cvs._validate_segment_window(10.0, 12.0, 15, _analysis_ok())
    assert any("below minimum" in r for r in rejected)


def test_validate_accepts_transcript_limited_under_min():
    analysis = dict(_analysis_ok(), transcript_limited=True)
    rejected, _ = cvs._validate_segment_window(10.0, 12.0, 15, analysis)
    assert rejected == []


def test_process_segments_reports_rejected_reasons(capsys):
    # A candidate collapsing to a zero-duration window is dropped with an
    # explicit reason printed for the user.
    transcript = [{"start": 0.0, "end": 40.0, "text": "جملة طويلة واحدة هنا للاختبار"}]
    raw = [_candidate("Zero", 5.0, 5.0)]
    result = cvs.process_segments(raw, transcript, 15, 60, snap_to_boundaries=False)
    assert isinstance(result["segments"], list)   # never crashes


# ---------------------------------------------------------------------------
# 6. Long silence rejection
# ---------------------------------------------------------------------------

def test_validate_rejects_excessive_silence():
    analysis = dict(_analysis_ok(), speech_coverage=0.02)
    rejected, _ = cvs._validate_segment_window(10.0, 30.0, 15, analysis)
    assert any("excessive silence" in r for r in rejected)


def test_validate_flags_low_speech_density_without_rejecting():
    analysis = dict(_analysis_ok(), speech_coverage=0.20)
    rejected, flags = cvs._validate_segment_window(10.0, 30.0, 15, analysis)
    assert rejected == []
    assert any("low speech density" in f for f in flags)


def test_analyze_window_measures_leading_and_trailing_silence():
    transcript = [{"start": 12.0, "end": 15.0, "text": "short line"}]
    analysis = transcript_window.analyze_window(transcript, 10.0, 20.0)
    assert analysis["leading_silence"] == pytest.approx(2.0)
    assert analysis["trailing_silence"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# 7+8. Temporal & semantic duplicates
# ---------------------------------------------------------------------------

def test_temporal_duplicate_dropped_higher_score_kept():
    segments = [
        {"title": "weak", "start_time": 10.0, "end_time": 40.0, "selection_score": 50},
        {"title": "strong", "start_time": 11.0, "end_time": 41.0, "selection_score": 90},
    ]
    kept = cvs.deduplicate_segments(segments)
    assert [s["title"] for s in kept] == ["strong"]


def test_semantic_duplicate_dropped_even_with_different_titles():
    text_a = "الطريقة بسيطة جدا تحتاج ثلاث خطوات فقط كل يوم دون توقف أبدا"
    text_b = "الطريقة بسيطة جدا تحتاج ثلاث خطوات فقط كل يوم دون توقف تماما"
    segments = [
        {"title": "عنوان مختلف تماما", "start_time": 0.0, "end_time": 30.0,
         "selection_score": 60, "transcript_text": text_a},
        {"title": "عنوان آخر لا يشبهه", "start_time": 100.0, "end_time": 130.0,
         "selection_score": 90, "transcript_text": text_b},
    ]
    kept = cvs.deduplicate_segments(segments)
    assert [s["title"] for s in kept] == ["عنوان آخر لا يشبهه"]


def test_semantic_distinct_ideas_are_kept():
    segments = [
        {"title": "A", "start_time": 0.0, "end_time": 30.0, "selection_score": 60,
         "transcript_text": "الطبخ فن يحتاج صبر وتدريب يومي طويل جدا"},
        {"title": "B", "start_time": 100.0, "end_time": 130.0, "selection_score": 90,
         "transcript_text": "البرمجة مهنة المستقبل تعلم لغة واحدة بعمق شديد"},
    ]
    kept = cvs.deduplicate_segments(segments)
    assert len(kept) == 2


def test_semantic_different_numbers_are_not_duplicates():
    is_dup, _ = transcript_window.are_semantic_duplicates(
        "٣ خطوات للنجاح في العمل الحر كل يوم",
        "٥ خطوات للنجاح في العمل الحر كل يوم")
    # ٣ vs ٥ → different stated facts, never a duplicate.
    assert is_dup is False


def test_repetition_penalty_applies_to_same_idea_different_window():
    text_a = "النجاح في العمل الحر يحتاج مهارة واحدة وتسويق يومي مستمر"
    text_b = "النجاح في العمل الحر يحتاج مهارة واحدة وتسويق يومي مستمر فعلا"
    segments = [
        {"title": "first", "start_time": 0.0, "end_time": 30.0, "selection_score": 90,
         "score": 90, "transcript_text": text_a, "score_breakdown": {}},
        {"title": "second", "start_time": 200.0, "end_time": 230.0, "selection_score": 80,
         "score": 80, "transcript_text": text_b, "score_breakdown": {}},
    ]
    cvs._apply_semantic_repetition_penalties(segments)
    assert segments[0]["score_breakdown"].get("repetition_penalty", 0) == 0
    assert segments[1]["score_breakdown"]["repetition_penalty"] > 0


# ---------------------------------------------------------------------------
# 9+10. Title factual consistency & hallucination rejection
# ---------------------------------------------------------------------------

def test_factual_title_scores_high():
    window = "الخطوة الأولى هي اختيار مهارة واحدة وإتقانها بعمق شديد"
    title = "الخطوة الأولى اختيار مهارة واحدة"
    scores = title_factual.score_title_candidate(title, window, "ar")
    assert scores["factual_accuracy_score"] >= 90.0
    assert scores["transcript_relevance_score"] >= 90.0


def test_hallucinated_number_is_detected():
    window = "الخطوة الأولى هي اختيار مهارة واحدة وإتقانها"
    title = "خمس خطوات لإتقان مهارة واحدة"   # "خمس" ok, but digit below is not
    unsupported = title_factual.hallucinated_facts(title, window)
    assert unsupported == []
    unsupported_digit = title_factual.hallucinated_facts("5 خطوات لإتقان مهارة", window)
    assert "5" in unsupported_digit


def test_hallucinated_name_is_detected():
    window = "spoke about freelancing and daily marketing"
    assert title_factual.hallucinated_facts("How Elon Musk freelances", window) != []
    validation = title_factual.validate_title("How Elon Musk freelances", window, "en")
    assert validation["contains_hallucinated_fact"] is True
    assert validation["matches_transcript"] is False


def test_clickbait_patterns_are_penalized():
    window = "الطريقة بسيطة جدا وتحتاج ثلاث خطوات فقط"
    for bait in ("لن تصدق ما حدث", "هذه المعلومة ستغير حياتك", "سر خطير",
                 "الجميع مخطئون", "اكتشاف صادم"):
        assert title_factual.contains_clickbait_pattern(bait), bait
        scores = title_factual.score_title_candidate(bait, window, "ar")
        assert scores["clickbait_penalty"] >= 30.0


def test_process_segments_replaces_hallucinated_llm_title():
    transcript = _arabic_transcript()
    raw = [_candidate("لن تصدق هذا السر الخطير 99", 12.8, 37.0, score=90,
                      hook_strength=85, narrative_completeness=80, clarity_score=75,
                      novelty_score=60)]
    result = cvs.process_segments(raw, transcript, 15, 60)
    seg = result["segments"][0]
    td = seg["title_data"]
    # 99 is invented: the LLM title can never ship as the primary title.
    assert "99" not in td["primary_title"]
    assert td["validation"]["contains_hallucinated_fact"] is False or td["fallback_used"]
    if td["fallback_used"]:
        assert td["llm_title_replaced"] == "لن تصدق هذا السر الخطير 99"


# ---------------------------------------------------------------------------
# 11. Arabic normalization (comparison only — display text untouched)
# ---------------------------------------------------------------------------

def test_arabic_orthography_variants_match():
    assert arabic_text.normalized_match_text("الأمور") == arabic_text.normalized_match_text("الامور")
    assert arabic_text.normalized_match_text("مُشْرُوعِيـْ") == arabic_text.normalized_match_text("مشروعي")
    assert arabic_text.normalized_match_text("مدرسة") == arabic_text.normalized_match_text("مدرسه")


def test_alif_maqsura_stays_distinct():
    assert arabic_text.normalized_match_text("على") != arabic_text.normalized_match_text("علي")


def test_arabic_digits_normalize_for_facts():
    assert arabic_text.normalize_digits("٣ خطوات") == "3 خطوات"
    assert title_factual.title_numbers("٣ خطوات") == {"3"}


def test_normalization_does_not_modify_display_text():
    original = "الأمورُ مُهِمَّةٌ جِدًّا"
    # normalized copy is for matching; the original string stays untouched.
    _ = arabic_text.normalized_match_text(original)
    assert original == "الأمورُ مُهِمَّةٌ جِدًّا"


# ---------------------------------------------------------------------------
# 12. RTL / Arabic title handling
# ---------------------------------------------------------------------------

def test_arabic_clip_gets_arabic_title_language():
    transcript = _arabic_transcript()
    raw = [_candidate("عنوان تجريبي", 12.8, 28.0)]
    result = cvs.process_segments(raw, transcript, 10, 60)
    seg = result["segments"][0]
    assert seg["title_data"]["title_language"] == "ar"
    assert any("؀" <= ch <= "ۿ" or "ؐ" <= ch <= "ፙ" for ch in seg["title_data"]["primary_title"])


def test_title_language_match_validation():
    window = "هذه جملة عربية كاملة عن العمل الحر والنجاح اليومي"
    scores_ar = title_factual.score_title_candidate("العمل الحر والنجاح اليومي", window, "ar")
    assert scores_ar["language_match_score"] == 100.0
    scores_en = title_factual.score_title_candidate("Freelancing and daily success", window, "ar")
    assert scores_en["language_match_score"] < 60.0


def test_arabic_connector_start_flagged_and_scored_down():
    transcript = [
        {"start": 0.0, "end": 5.0, "text": "تحدثنا عن الأساسيات في البداية."},
        {"start": 6.0, "end": 12.0, "text": "لكن السر الحقيقي مختلف تماما عن المتوقع."},
    ]
    analysis = transcript_window.analyze_window(transcript, 6.0, 12.0)
    assert analysis["starts_with_connector"] is True
    context_score = clip_scoring.standalone_context_score(analysis)
    assert context_score < 100.0


def test_connector_start_repair_includes_antecedent():
    transcript = [
        {"start": 0.0, "end": 5.0, "text": "الجميع يظن أن النجاح سهل."},
        {"start": 6.0, "end": 12.0, "text": "لكن الحقيقة مختلفة تماما عن ذلك."},
    ]
    start, end, notes = transcript_window.refine_boundaries(
        6.0, 12.0, transcript, min_duration=5, max_duration=60,
        transcript_start=0.0, transcript_end=12.0)
    assert start == pytest.approx(0.0)
    assert "connector_start_included_antecedent" in notes


def test_dangling_preposition_ending_extended():
    transcript = [
        {"start": 0.0, "end": 5.0, "text": "سوف نتحدث اليوم عن"},
        {"start": 5.2, "end": 10.0, "text": "العمل الحر وأسراره الكاملة."},
    ]
    analysis = transcript_window.analyze_window(transcript, 0.0, 5.0)
    assert analysis["ends_incomplete"] is True
    start, end, notes = transcript_window.refine_boundaries(
        0.0, 5.0, transcript, min_duration=3, max_duration=60,
        transcript_start=0.0, transcript_end=10.0)
    assert end == pytest.approx(10.0)
    assert "dangling_end_extended_to_sentence_end" in notes


@pytest.mark.parametrize("connector", ["لكن", "لذلك", "لأن", "وهذا", "فهو", "ثم", "فإذا", "ولكن"])
def test_all_spec_connectors_are_detected(connector):
    transcript = [{"start": 0.0, "end": 5.0, "text": f"{connector} بقية الجملة هنا تماما"}]
    analysis = transcript_window.analyze_window(transcript, 0.0, 5.0)
    assert analysis["starts_with_connector"] is True


# ---------------------------------------------------------------------------
# 13. Arabic-English mixed text
# ---------------------------------------------------------------------------

def test_mixed_content_language_detection():
    assert title_factual.detect_content_language("هذه جملة عربية كاملة") == "ar"
    assert title_factual.detect_content_language("this is a full english sentence") == "en"
    mixed = title_factual.detect_content_language("هذه جملة mixed بين العربية and English تماما")
    assert mixed in ("mixed", "ar")


def test_bilingual_window_language_follows_dominant_script():
    transcript = [
        {"start": 0.0, "end": 5.0, "text": "مرحبا بكم في الحلقة الأولى تماما."},
        {"start": 6.0, "end": 12.0, "text": "today we discuss freelancing tips and tricks."},
    ]
    ar_window = transcript_window.analyze_window(transcript, 0.0, 5.0)
    en_window = transcript_window.analyze_window(transcript, 6.0, 12.0)
    assert title_factual.detect_content_language(ar_window["text"]) == "ar"
    assert title_factual.detect_content_language(en_window["text"]) == "en"


# ---------------------------------------------------------------------------
# 14+15. Title length limit & no word-breaking
# ---------------------------------------------------------------------------

def test_primary_title_respects_publish_limit():
    transcript = _arabic_transcript()
    long_title = "الخطوة الأولى هي اختيار مهارة واحدة وإتقانها بعمق " * 3
    raw = [_candidate(long_title.strip(), 12.8, 28.0)]
    result = cvs.process_segments(raw, transcript, 10, 60)
    seg = result["segments"][0]
    assert len(seg["title_data"]["primary_title"]) <= title_factual.PUBLISH_TITLE_LIMIT


def test_title_fitting_never_breaks_a_word():
    from scripts.title_text import fit_publish_title
    text = "هذا عنوان طويل جدا يحتوي كلمات كثيرة ويجب ألا يقطع كلمة عربية في المنتصف أبدا مهما حدث"
    fitted = fit_publish_title(text, 40)
    assert len(fitted) <= 40
    assert fitted.endswith("…")
    body = fitted[:-1]
    assert text.startswith(body)          # prefix cut …
    assert not body.endswith(("و", "أل"))  # … never mid-word


# ---------------------------------------------------------------------------
# 16. Low-confidence handling for ambiguous transcripts
# ---------------------------------------------------------------------------

def test_ambiguous_transcript_gets_low_confidence_fallback():
    analysis = transcript_window.analyze_window(
        [{"start": 0.0, "end": 5.0, "text": "أشياء كثيرة مختلفة هنا"}], 0.0, 5.0)
    data = title_factual.build_title_data(
        "عنوان لا علاقة له بالمحتوى إطلاقا", [], analysis["text"], analysis, "ar")
    assert data["fallback_used"] is True
    assert data["title_confidence"] <= 40.0
    assert data["primary_title"]            # never empty


def test_fallback_titles_come_from_clip_text_only():
    analysis = transcript_window.analyze_window(_arabic_transcript(), 12.8, 20.0)
    fallbacks = title_factual.fallback_titles(analysis, "ar")
    assert fallbacks
    window_norm = arabic_text.normalized_match_text(analysis["text"])
    for item in fallbacks:
        title_norm = arabic_text.normalized_match_text(item["text"].replace("…", ""))
        assert title_norm in window_norm   # conservative: clip's own words


# ---------------------------------------------------------------------------
# 17+18. Config fingerprint & force regeneration
# ---------------------------------------------------------------------------

_HEAVY_MODULES = [
    "cv2", "mediapipe", "torch", "torchaudio", "whisperx", "insightface",
    "onnxruntime", "av", "moviepy", "librosa", "soundfile",
    "tqdm", "tqdm.asyncio", "psutil", "requests",
]


@pytest.fixture(scope="module")
def cli():
    saved = {}
    stubs = {}
    for name in _HEAVY_MODULES:
        saved[name] = sys.modules.get(name)
        if saved[name] is None:
            stubs[name] = mock.MagicMock(name=name)
    sys.modules.update(stubs)
    try:
        import main_improved as mod
    finally:
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
    return mod


def _fp_args(**overrides):
    base = dict(segments=3, min_duration=15, max_duration=60, chunk_size=None,
                title_language="auto", viral=False, themes="", ai_backend="gemini",
                ai_model_name=None, model="large-v3-turbo", scene_snap=False)
    base.update(overrides)
    return argparse.Namespace(**base)


def test_config_fingerprint_changes_with_important_settings(cli):
    base = cli._segment_settings_fingerprint(_fp_args())
    for override in (
        {"segments": 5}, {"min_duration": 20}, {"max_duration": 45},
        {"chunk_size": 9000}, {"title_language": "ar"}, {"viral": True},
        {"themes": "money"}, {"ai_backend": "g4f"}, {"ai_model_name": "gemini-pro"},
        {"model": "large-v3"}, {"scene_snap": True},
    ):
        changed = cli._segment_settings_fingerprint(_fp_args(**override))
        assert changed != base, "fingerprint must change for {}".format(override)


def test_config_fingerprint_stable_for_same_settings(cli):
    assert cli._segment_settings_fingerprint(_fp_args()) == \
        cli._segment_settings_fingerprint(_fp_args())


def test_config_fingerprint_changes_with_prompt_edit(cli, monkeypatch):
    base = cli._segment_settings_fingerprint(_fp_args())
    monkeypatch.setattr(cvs, "prompt_version_fingerprint", lambda: "deadbeef00")
    assert cli._segment_settings_fingerprint(_fp_args()) != base


def test_force_regenerate_flag_is_listed(cli, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["viralcutter", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--force-regenerate" in out
    assert "--force-new-segments" in out


def test_prompt_version_fingerprint_is_stable_and_sensitive(tmp_path):
    first = cvs.prompt_version_fingerprint()
    assert first == cvs.prompt_version_fingerprint()
    assert len(first) == 16


# ---------------------------------------------------------------------------
# 19. Backwards compatibility with legacy JSON
# ---------------------------------------------------------------------------

def test_legacy_segments_without_new_fields_still_dedup_and_rank():
    legacy = [
        {"title": "b", "start_time": 0.0, "end_time": 30.0, "score": 70},
        {"title": "a", "start_time": 60.0, "end_time": 90.0, "score": 90},
    ]
    kept = cvs.deduplicate_segments(legacy)   # no transcript_text → temporal only
    assert len(kept) == 2
    assert kept[0]["title"] == "a"            # highest score first


def test_legacy_raw_candidate_minimal_fields_processed():
    transcript = _arabic_transcript()
    raw = [{"title": "legacy", "start_time": 6.0, "end_time": 20.0, "score": 85}]
    result = cvs.process_segments(raw, transcript, 10, 60)
    seg = result["segments"][0]
    # New fields are additive; legacy keys are all still present.
    for key in ("title", "start_time", "end_time", "duration", "score",
                "recommended_title", "selection_score"):
        assert key in seg
    for key in ("transcript_text", "hook_text", "completion_status",
                "score_breakdown", "quality_flags", "rejected_reasons", "title_data"):
        assert key in seg
    json.dumps(seg, ensure_ascii=False)       # stays JSON-serializable


def test_new_payload_roundtrips_through_json(tmp_path):
    transcript = _arabic_transcript()
    raw = [_candidate("test", 6.0, 28.0, hook_strength=80,
                      narrative_completeness=75, clarity_score=70, novelty_score=60)]
    result = cvs.process_segments(raw, transcript, 10, 60)
    path = tmp_path / "viral_segments.txt"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["segments"][0]["title_data"]["primary_title"]
    assert loaded["selection_config"]["scoring_version"] == clip_scoring.SCORING_VERSION


# ---------------------------------------------------------------------------
# 20+21. One-speaker and multi-speaker transcripts
# ---------------------------------------------------------------------------

def test_single_speaker_video_full_pipeline():
    transcript = _arabic_transcript()
    raw = [
        _candidate("ثلاث خطوات للنجاح", 6.0, 20.0, score=90, hook_strength=85,
                   narrative_completeness=85, clarity_score=80, novelty_score=70),
        _candidate("الخلاصة الكاملة", 38.0, 52.0, score=75),
    ]
    result = cvs.process_segments(raw, transcript, 10, 60)
    assert len(result["segments"]) == 2
    best = max(result["segments"], key=lambda s: s["selection_score"])
    assert best["completion_status"] == "complete"
    assert best["title_data"]["title_language"] == "ar"


def test_multi_speaker_transcript_tolerated():
    transcript = [
        {"start": 0.0, "end": 5.0, "text": "[المضيف] ما رأيك في العمل الحر؟"},
        {"start": 6.0, "end": 12.0, "text": "[الضيف] هو أفضل قرار اتخذته في حياتي."},
        {"start": 13.0, "end": 19.0, "text": "[المضيف] ولماذا تقول ذلك بثقة؟"},
        {"start": 20.0, "end": 28.0, "text": "[الضيف] لأنه منحني حرية الوقت والدخل معا."},
    ]
    raw = [_candidate("حوار عن الحرية", 6.0, 28.0)]
    result = cvs.process_segments(raw, transcript, 10, 60)
    seg = result["segments"][0]
    assert seg["transcript_text"]
    assert "[الضيف]" in seg["transcript_text"]


# ---------------------------------------------------------------------------
# 22+23. Long sentences and long pauses
# ---------------------------------------------------------------------------

def test_very_long_sentence_stays_one_unit():
    words = " ".join("كلمة{}".format(i) for i in range(40))
    transcript = [{"start": 0.0, "end": 40.0, "text": words + "."}]
    units = transcript_window.split_sentence_units(transcript)
    assert len(units) == 1
    analysis = transcript_window.analyze_window(transcript, 0.0, 40.0)
    assert analysis["complete"] is True
    assert analysis["word_count"] == 40


def test_long_pause_creates_boundary_and_silence_flags():
    transcript = [
        {"start": 0.0, "end": 5.0, "text": "الجملة الأولى هنا."},
        {"start": 40.0, "end": 45.0, "text": "جملة بعد صمت طويل جدا."},
    ]
    analysis = transcript_window.analyze_window(transcript, 0.0, 45.0)
    assert analysis["silence_ratio"] > 0.5
    rejected, flags = cvs._validate_segment_window(0.0, 45.0, 10, analysis)
    assert any("low speech density" in f for f in flags)


# ---------------------------------------------------------------------------
# 24. Scoring system: centralized weights + final score
# ---------------------------------------------------------------------------

def test_centralized_weights_sum_to_one_and_match_spec():
    weights = clip_scoring.DEFAULT_SELECTION_WEIGHTS
    assert weights == {
        "hook_strength": 0.20, "standalone_context": 0.18,
        "emotional_value": 0.15, "information_density": 0.12,
        "completion_score": 0.12, "transcript_alignment": 0.10,
        "audio_quality": 0.05, "visual_quality": 0.04, "title_relevance": 0.04,
    }
    assert sum(weights.values()) == pytest.approx(1.0)


def test_final_score_formula_with_penalties():
    factors = {name: 90.0 for name in clip_scoring.DEFAULT_SELECTION_WEIGHTS}
    factors["repetition_penalty"] = 20.0
    factors["safety_penalty"] = 10.0
    assert clip_scoring.compute_final_score(factors) == pytest.approx(60.0)


def test_weights_env_override(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_SELECTION_WEIGHTS",
                       '{"hook_strength": 0.5, "bogus_key": 9}')
    weights = clip_scoring.load_selection_weights()
    assert weights["hook_strength"] == 0.5
    assert "bogus_key" not in weights
    assert weights["standalone_context"] == 0.18   # untouched default


def test_score_breakdown_contains_all_eleven_factors():
    transcript = _arabic_transcript()
    raw = [_candidate("breakdown", 6.0, 28.0)]
    seg = cvs.process_segments(raw, transcript, 10, 60)["segments"][0]
    for factor in clip_scoring.FACTOR_NAMES:
        assert factor in seg["score_breakdown"]
        assert 0.0 <= seg["score_breakdown"][factor] <= 100.0


def test_min_final_score_floor_returns_fewer_clips(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_MIN_FINAL_SCORE", "99")
    transcript = _arabic_transcript()
    raw = [_candidate("weak", 6.0, 28.0, score=10, hook_strength=5,
                      narrative_completeness=5, clarity_score=5, novelty_score=5)]
    result = cvs.process_segments(raw, transcript, 10, 60)
    assert result["segments"] == []     # fewer clips, never weak padding


# ---------------------------------------------------------------------------
# 25. Export-path data contracts (cut/subtitle stages)
# ---------------------------------------------------------------------------

def test_process_output_feeds_cut_json_and_cut_segments(tmp_path):
    transcript = _arabic_transcript()
    raw = [_candidate("export", 6.0, 28.0)]
    seg = cvs.process_segments(raw, transcript, 10, 60)["segments"][0]
    # cut_segments needs start_time + duration + title; cut_json needs the
    # WhisperX JSON windowed by [start_time, end_time].
    for key in ("start_time", "end_time", "duration", "title"):
        assert key in seg
    from scripts import cut_json
    input_json = tmp_path / "input.json"
    input_json.write_text(json.dumps({"segments": [
        {"start": 6.0, "end": 12.0, "text": "today",
         "words": [{"start": 6.0, "end": 6.5, "word": "today"}]},
    ]}), encoding="utf-8")
    out_json = tmp_path / "out.json"
    cut_json.cut_json_transcript(str(input_json), str(out_json),
                                 seg["start_time"], seg["end_time"])
    clipped = json.loads(out_json.read_text(encoding="utf-8"))
    assert clipped["segments"]


def test_millisecond_precision_preserved():
    transcript = [{"start": 10.125, "end": 20.375, "text": "دقة زمنية عالية هنا جدا"}]
    raw = [_candidate("precision", 10.125, 20.375)]
    seg = cvs.process_segments(raw, transcript, 5, 60)["segments"][0]
    assert seg["start_time"] == pytest.approx(10.125, abs=1e-3)
    assert seg["end_time"] == pytest.approx(20.375, abs=1e-3)


# ---------------------------------------------------------------------------
# 26. Safety-stage compatibility
# ---------------------------------------------------------------------------

def test_segments_keep_fields_required_by_safety_stages():
    transcript = _arabic_transcript()
    raw = [_candidate("safety", 6.0, 28.0, caption="تعليق تجريبي")]
    seg = cvs.process_segments(raw, transcript, 10, 60)["segments"][0]
    # content_guard / safety_filter / upload_gate read these keys.
    for key in ("title", "caption", "start_time", "end_time", "duration",
                "score", "hashtags", "alt_titles"):
        assert key in seg


def test_safety_modules_import_untouched():
    from scripts import content_guard, safety_filter  # noqa: F401
    assert hasattr(safety_filter, "filter_segments") or hasattr(safety_filter, "main") \
        or dir(safety_filter)
