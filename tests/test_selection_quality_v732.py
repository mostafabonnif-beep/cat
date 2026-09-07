# -*- coding: utf-8 -*-
"""Selection-quality regression tests (v7.32).

Covers four robustness/quality improvements in create_viral_segments.py:

1. Arabic orthography normalization for matching — hamza carriers (أ إ آ ٱ),
   hamza seats (ؤ ئ), ta marbuta (ة), tatweel and tashkeel are unified so
   spelling variants match, while alif maqsura (ى) stays distinct from ي
   (على vs علي must not collide). English output stays byte-identical.
2. Near-duplicate tightening — two KEPT clips never share more than ~60% of
   the shorter window (previously ~75%), with the small-shift rule tightened
   from 0.50 to 0.45.
3. Dict-shaped ``segments`` recovery — models returning
   ``{"segments": {"0": {...}, "1": {...}}}`` (numeric-key object) used to
   yield zero segments; the object is now decoded back into a list.
4. Window-content title relevance — titles are scored against the words
   ACTUALLY inside the final cut window (transcript ground truth), so a
   hallucinated LLM caption can no longer inflate relevance, while the
   legacy LLM-text-only formula is preserved when no window text is given.
"""

import json

import pytest

from scripts.create_viral_segments import (
    _choose_recommended_title,
    _extract_segments_json,
    _normalize_arabic_orthography,
    _normalized_match_text,
    _text_similarity,
    _title_content_relevance,
    _window_text_from_transcript,
    _windows_are_near_duplicates,
    clean_json_response,
    deduplicate_segments,
    process_segments,
)

# ---------------------------------------------------------------------------
# 1. Arabic orthography normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "variant,plain",
    [
        ("الأمور", "الامور"),   # أ (hamza above) vs ا
        ("أمل", "امل"),
        ("إيمان", "ايمان"),     # إ (hamza below)
        ("آخر", "اخر"),         # آ (madda)
        ("\u0671لله", "الله"),  # ٱ (wasla, U+0671)
    ],
)
def test_arabic_hamza_carriers_normalize_to_alef(variant, plain):
    assert _normalized_match_text(variant) == _normalized_match_text(plain)
    assert _text_similarity(
        _normalized_match_text(variant),
        _normalized_match_text(plain)) >= 0.999


def test_arabic_hamza_seats_and_ta_marbuta_normalize():
    # ؤ -> و
    assert _normalized_match_text("مؤمن") == _normalized_match_text("مومن")
    # ئ -> ي
    assert _normalized_match_text("بئر") == _normalized_match_text("بير")
    # ة -> ه
    assert _normalized_match_text("مدرسة") == _normalized_match_text("مدرسه")
    assert _normalized_match_text("فتاة") == _normalized_match_text("فتاه")


def test_arabic_tatweel_and_diacritics_variants_match():
    # Tatweel ـ (U+0640) is a scribal stretch mark kept by the legacy \w
    # filter; tashkeel marks (fatha/damma/kasra/sukun U+064B..U+065F) and the
    # superscript alef U+0670 are optional decorations. All must be removed.
    plain = "مشروعي مستقبلي"
    decorated = "مُشْرُوعِيـْ مُسْتَقْبَلِي"   # fatha/damma/kasra/sukun + tatweel
    assert decorated != plain
    assert _normalized_match_text(decorated) == _normalized_match_text(plain)
    assert _text_similarity(
        _normalized_match_text(decorated),
        _normalized_match_text(plain)) == 1.0
    # Superscript alef U+0670.
    assert _normalized_match_text("ه\u0670ذا") == _normalized_match_text("هذا")


def test_arabic_alif_maqsura_stays_distinct_from_yaa():
    # Alif maqsura ى must NOT fold into ي: على (on) vs علي (name) are
    # different words and a matching fix must not merge them.
    assert _normalized_match_text("على") != _normalized_match_text("علي")
    assert _text_similarity(
        _normalized_match_text("على"),
        _normalized_match_text("علي")) < 1.0
    assert _normalized_match_text("مشى") != _normalized_match_text("مشي")
    assert _normalized_match_text("علي") == "علي"  # ي itself is untouched


def test_normalized_match_text_keeps_english_byte_identical():
    # The Arabic table only touches Arabic code points: English output must
    # match the legacy lowercase + strip behaviour exactly (digits are \w and
    # are kept; non-word punctuation is dropped without inserting spaces).
    assert _normalized_match_text("Hello, World!") == "hello world"
    assert _normalized_match_text("Hello, World!") == _normalized_match_text("Hello, World!  ")
    assert _normalized_match_text("Don't stop! 12.5% done") == "dont stop 125 done"


def test_normalize_arabic_orthography_is_pure_and_safe():
    assert _normalize_arabic_orthography("") == ""
    assert _normalize_arabic_orthography(None) == ""
    assert _normalize_arabic_orthography(123) == "123"
    assert _normalize_arabic_orthography("hello") == "hello"


# ---------------------------------------------------------------------------
# 2. Near-duplicate tightening
# ---------------------------------------------------------------------------

def _win(start_time, end_time, title="clip", score=50):
    return {"title": title, "start_time": start_time, "end_time": end_time,
            "score": score}


def test_near_duplicates_60_percent_overlap_is_duplicate():
    # 0-20 vs 8-28: intersection 12s over a 20s shorter window = 0.60.
    assert _windows_are_near_duplicates(_win(0, 20), _win(8, 28)) is True


def test_near_duplicates_55_percent_overlap_with_shift_is_distinct():
    # 0-20 vs 9-29: intersection 11s = 0.55, start shift 9s > 1s.
    assert _windows_are_near_duplicates(_win(0, 20), _win(9, 29)) is False


def test_near_duplicates_large_shift_partial_overlap_is_distinct():
    # 10-40 vs 25-55: intersection 15s over a 30s shorter window = 0.50,
    # start shift 15s — genuinely different footage.
    assert _windows_are_near_duplicates(_win(10, 40), _win(25, 55)) is False


def test_near_duplicates_identical_windows_are_duplicate():
    assert _windows_are_near_duplicates(_win(0, 20), _win(0, 20)) is True


def test_near_duplicates_15s_windows_shifted_10s_stay_distinct():
    # 0-15 vs 10-25: intersection 5s = 0.33, shift 10s. Regression guard for
    # test_cli_main.py::test_process_segments_keeps_distinct_partial_overlap.
    assert _windows_are_near_duplicates(_win(0, 15), _win(10, 25)) is False


def test_near_duplicates_tolerates_disjoint_and_malformed():
    assert _windows_are_near_duplicates(_win(0, 10), _win(20, 30)) is False
    assert _windows_are_near_duplicates({"title": "no times"}, _win(0, 10)) is False
    assert _windows_are_near_duplicates(
        {"start_time": "abc", "end_time": "xyz"}, _win(0, 10)) is False


def test_deduplicate_segments_drops_60_percent_overlap_keeps_disjoint():
    segments = [
        _win(8, 28, title="high-60", score=95),
        _win(0, 20, title="low-60", score=90),
        _win(60, 90, title="disjoint", score=80),
    ]
    result = deduplicate_segments(segments)
    assert {item["title"] for item in result} == {"high-60", "disjoint"}


def test_deduplicate_segments_keeps_55_percent_overlap_when_shifted():
    segments = [
        _win(0, 20, title="first", score=95),
        _win(9, 29, title="second", score=90),
    ]
    result = deduplicate_segments(segments)
    assert {item["title"] for item in result} == {"first", "second"}


# ---------------------------------------------------------------------------
# 3. Dict-shaped "segments" recovery
# ---------------------------------------------------------------------------

def test_extract_segments_recovers_dict_shaped_segments():
    payload = (
        "Sure! Here are the viral moments:\n"
        '{"segments": {"0": {"title": "zero", "start_time": 0, "end_time": 5},'
        ' "1": {"title": "one", "start_time": 10, "end_time": 20}}}'
    )
    parsed = _extract_segments_json(payload)
    assert [seg["title"] for seg in parsed["segments"]] == ["zero", "one"]


def test_extract_segments_dict_recovery_orders_numeric_keys():
    payload = (
        '"segments": {"2": {"title": "two", "start_time": 20},'
        ' "0": {"title": "zero", "start_time": 0},'
        ' "1": {"title": "one", "start_time": 10}}'
    )
    parsed = _extract_segments_json(payload)
    assert [seg["title"] for seg in parsed["segments"]] == ["zero", "one", "two"]


def test_extract_segments_list_path_unchanged_by_recovery():
    expected = [{"title": "only", "start_time": 1, "end_time": 5}]
    payload = 'prefix {"segments": %s} suffix' % json.dumps(expected)
    parsed = _extract_segments_json(payload)
    assert parsed["segments"] == expected


def test_extract_segments_dict_with_non_dict_values_falls_through():
    # A dict whose values are not all segment-shaped must NOT be treated as a
    # segment list (no false positives).
    assert _extract_segments_json('{"segments": {"meta": "nope"}}') == {"segments": []}
    assert _extract_segments_json('{"segments": {}}') == {"segments": []}


def test_extract_segments_garbage_returns_empty():
    assert _extract_segments_json("this is not json segments at all") == {"segments": []}
    assert _extract_segments_json("") == {"segments": []}


def test_clean_json_response_recovers_dict_shaped_segments():
    payload = (
        "Here is the result:\n"
        '```json\n{"segments": {"0": {"title": "أول", "start_time": 1, "end_time": 6},'
        ' "1": {"title": "ثاني", "start_time": 9, "end_time": 14}}}\n```'
    )
    parsed = clean_json_response(payload)
    assert [seg["title"] for seg in parsed["segments"]] == ["أول", "ثاني"]


# ---------------------------------------------------------------------------
# 4. Window-content title relevance
# ---------------------------------------------------------------------------

def _three_line_transcript():
    return [
        {"start": 0.0, "end": 5.0, "text": "First line."},
        {"start": 5.0, "end": 10.0, "text": "Second line."},
        {"start": 10.0, "end": 15.0, "text": "Third line!"},
    ]


def test_window_text_from_transcript_joins_overlapping_lines():
    transcript = _three_line_transcript()
    # [4, 11) overlaps all three lines (10 < 11 and 15 > 4 for the third).
    assert _window_text_from_transcript(transcript, 4.0, 11.0) == (
        "first line second line third line")


def test_window_text_from_transcript_respects_half_open_bounds():
    transcript = _three_line_transcript()
    # Line ending exactly at start_time (0-5 vs [5,10)) is excluded; line
    # starting exactly at end_time (10-15 vs [0,10)) is excluded.
    assert _window_text_from_transcript(transcript, 0.0, 5.0) == "first line"
    assert _window_text_from_transcript(transcript, 5.0, 10.0) == "second line"
    assert _window_text_from_transcript(transcript, 15.0, 20.0) == ""


def test_window_text_from_transcript_empty_and_unparseable():
    assert _window_text_from_transcript([], 0.0, 5.0) == ""
    assert _window_text_from_transcript(_three_line_transcript(), None, 5.0) == ""
    assert _window_text_from_transcript(_three_line_transcript(), 0.0, "soon") == ""


def _hallucinated_segment():
    # The LLM claims the clip is about a "shortcut" (start/end/caption), but
    # the actual transcript inside the window never says it — the window only
    # contains "patience" & friends.
    return {
        "title": "shortcut",
        "alt_titles": ["patience"],
        "start_text": "shortcut pays",
        "end_text": "shortcut",
        "caption": "use the shortcut now",
        "score": 90,
    }


_HALLUCINATION_TRANSCRIPT = [
    {"start": 0.0, "end": 5.0, "text": "the market opened quietly"},
    {"start": 5.0, "end": 10.0, "text": "then patience became the secret"},
    {"start": 10.0, "end": 15.0, "text": "nobody expected the crash"},
]


def test_title_content_relevance_prefers_real_window_words():
    segment = _hallucinated_segment()
    window = _window_text_from_transcript(_HALLUCINATION_TRANSCRIPT, 2.0, 12.0)
    assert "shortcut" not in window and "patience" in window

    # Legacy (no window): the hallucinated caption alone gives "shortcut"
    # full marks and the real-word title zero.
    assert _title_content_relevance("shortcut", segment) == 1.0
    assert _title_content_relevance("patience", segment) == 0.0

    # With the actual window text, "patience" (really spoken) outranks
    # "shortcut" (only in the hallucinated caption).
    assert _title_content_relevance("patience", segment, window) >= \
        _title_content_relevance("shortcut", segment, window)
    assert _title_content_relevance("shortcut", segment, window) == 0.5
    assert _title_content_relevance("patience", segment, window) == 1.0


def test_title_content_relevance_window_llm_only_words_get_half_weight():
    segment = _hallucinated_segment()
    window = _window_text_from_transcript(_HALLUCINATION_TRANSCRIPT, 2.0, 12.0)
    # "shortcut pays": neither word is in the window, both are LLM-only →
    # (0 + 2 * 0.5) / 2 = 0.5, never the legacy 1.0.
    assert _title_content_relevance("shortcut pays", segment, window) == 0.5


def test_title_content_relevance_window_is_capped_at_one():
    segment = _hallucinated_segment()
    window = _window_text_from_transcript(_HALLUCINATION_TRANSCRIPT, 2.0, 12.0)
    score = _title_content_relevance("patience crash", segment, window)
    assert score == 1.0
    mixed = _title_content_relevance("patience shortcut", segment, window)
    assert 0.0 <= mixed <= 1.0
    assert mixed == 0.75
    assert _title_content_relevance("notspoken", segment, window) == 0.0


def test_title_content_relevance_legacy_path_unchanged_without_window():
    segment = _hallucinated_segment()
    # Explicit None behaves exactly like omitting the argument (legacy).
    assert _title_content_relevance("shortcut", segment, None) == \
        _title_content_relevance("shortcut", segment)
    assert _title_content_relevance("shortcut", segment, None) == 1.0
    # Empty window_text also falls back to the legacy LLM-text formula.
    assert _title_content_relevance("shortcut", segment, "") == 1.0


def test_choose_recommended_title_uses_window_text_when_given():
    segment = _hallucinated_segment()
    window = _window_text_from_transcript(_HALLUCINATION_TRANSCRIPT, 2.0, 12.0)
    # Legacy ranking trusts the hallucinated caption → "shortcut" wins.
    assert _choose_recommended_title(segment) == "shortcut"
    # With window ground truth the really-spoken word wins.
    assert _choose_recommended_title(segment, window_text=window) == "patience"


def test_process_segments_recommended_title_prefers_actual_window_words():
    raw = [dict(_hallucinated_segment(), start_time=2, end_time=12)]
    result = process_segments(raw, _HALLUCINATION_TRANSCRIPT, 5, 30)
    assert result["segments"][0]["recommended_title"] == "patience"


def test_process_segments_keeps_explicit_recommended_title_verbatim():
    raw = [dict(_hallucinated_segment(), start_time=2, end_time=12,
                recommended_title="editorial pick")]
    result = process_segments(raw, _HALLUCINATION_TRANSCRIPT, 5, 30)
    assert result["segments"][0]["recommended_title"] == "editorial pick"
