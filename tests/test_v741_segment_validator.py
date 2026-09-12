# -*- coding: utf-8 -*-
"""v7.41 — tests for the reusable final-segment validator (spec item F).

Covers the full public contract of ``scripts/segment_validator.py``:
timestamps, duration min/max (incl. transcript-limited clips), media bounds,
transcript presence/lexical floor, word- and sentence-level truncation, edge
silence, title status, safety, duplication, immutability of the input, and the
batch aggregation API.
"""

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import segment_validator as sv

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _segment(**overrides):
    """A fully valid final segment; overrides poke holes in it."""
    segment = {
        "title": "Three steps to freelance success",
        "start_time": 10.0,
        "end_time": 40.0,
        "duration": 30.0,
        "transcript_text": "this is a solid transcript with several useful words inside it",
        "quality_flags": [],
        "title_validation": {"status": "verified"},
        "safety_status": "ok",
    }
    segment.update(overrides)
    return segment


def _codes(result):
    return [error["code"] for error in result["errors"]]


def _warning_codes(result):
    return [warning["code"] for warning in result["warnings"]]


# ---------------------------------------------------------------------------
# Baseline / contract
# ---------------------------------------------------------------------------

def test_version_constant():
    assert sv.SEGMENT_VALIDATOR_VERSION == "1.0"


def test_valid_segment_passes():
    result = sv.validate_final_segment(_segment())
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["duration"] == 30.0
    checks = result["checks"]
    assert checks["start_time_numeric"] is True
    assert checks["end_time_numeric"] is True
    assert checks["end_after_start"] is True
    assert checks["positive_duration"] is True
    assert checks["duration_within_min"] is True
    assert checks["duration_within_max"] is True
    assert checks["within_media_bounds"] is None
    assert checks["transcript_present"] is True
    assert checks["no_word_truncation"] is True
    assert checks["no_sentence_truncation"] is True
    assert checks["edge_silence_ok"] is True
    assert checks["title_status"] == "verified"
    assert checks["safety_ok"] is True
    assert checks["duplicate_ok"] is True


def test_checks_dict_has_the_full_contract():
    expected = {
        "start_time_numeric", "end_time_numeric", "end_after_start",
        "positive_duration", "duration_within_min", "duration_within_max",
        "within_media_bounds", "transcript_present", "no_word_truncation",
        "no_sentence_truncation", "edge_silence_ok", "title_status",
        "safety_ok", "duplicate_ok",
    }
    result = sv.validate_final_segment(_segment())
    assert expected.issubset(set(result["checks"]))
    assert set(result) == {"ok", "errors", "warnings", "checks", "duration"}


def test_result_never_raises_for_malformed_input():
    for broken in (None, 42, "not a segment", [], {}, {"start_time": object()}):
        result = sv.validate_final_segment(broken)
        assert result["ok"] is False
        assert isinstance(result["errors"], list)
        assert isinstance(result["warnings"], list)
        assert result["duration"] is None or isinstance(result["duration"], float)


# ---------------------------------------------------------------------------
# Timestamps and duration
# ---------------------------------------------------------------------------

def test_missing_end_time_rejected():
    segment = _segment()
    del segment["end_time"]
    result = sv.validate_final_segment(segment)
    assert result["ok"] is False
    assert "missing_end_time" in _codes(result)
    assert result["checks"]["end_time_numeric"] is False
    assert result["duration"] is None


def test_missing_start_time_rejected():
    segment = _segment()
    del segment["start_time"]
    result = sv.validate_final_segment(segment)
    assert "missing_start_time" in _codes(result)


def test_none_and_blank_timestamps_treated_as_missing_or_invalid():
    assert "missing_end_time" in _codes(sv.validate_final_segment(_segment(end_time=None)))
    assert "invalid_start_time" in _codes(sv.validate_final_segment(_segment(start_time="")))


def test_non_finite_timestamps_rejected():
    assert "invalid_start_time" in _codes(sv.validate_final_segment(_segment(start_time=float("nan"))))
    assert "invalid_end_time" in _codes(sv.validate_final_segment(_segment(end_time=float("inf"))))


def test_numeric_strings_accepted():
    result = sv.validate_final_segment(_segment(start_time="10.5", end_time="40.5"))
    assert result["ok"] is True
    assert result["duration"] == 30.0


def test_end_not_after_start_rejected():
    result = sv.validate_final_segment(_segment(start_time=30.0, end_time=30.0))
    assert result["ok"] is False
    assert "end_not_after_start" in _codes(result)
    assert result["checks"]["end_after_start"] is False


def test_non_positive_duration_rejected():
    result = sv.validate_final_segment(_segment(start_time=30.0, end_time=30.0))
    assert "non_positive_duration" in _codes(result)
    assert result["checks"]["positive_duration"] is False


def test_duration_below_min_rejected():
    result = sv.validate_final_segment(_segment(), min_duration=60.0)
    assert result["ok"] is False
    assert "duration_below_min" in _codes(result)
    assert result["checks"]["duration_within_min"] is False


def test_below_min_transcript_limited_is_warning_and_ok():
    for flag in ("transcript_limited", "under_min"):
        result = sv.validate_final_segment(_segment(**{flag: True}), min_duration=60.0)
        assert result["ok"] is True
        assert "duration_below_min" not in _codes(result)
        assert "duration_below_min_transcript_limited" in _warning_codes(result)
        assert result["checks"]["duration_within_min"] is False


def test_duration_above_max_rejected():
    result = sv.validate_final_segment(_segment(), max_duration=20.0)
    assert result["ok"] is False
    assert "duration_above_max" in _codes(result)
    assert result["checks"]["duration_within_max"] is False


# ---------------------------------------------------------------------------
# Media bounds
# ---------------------------------------------------------------------------

def test_out_of_media_bounds_rejected():
    result = sv.validate_final_segment(_segment(), media_duration=25.0)
    assert result["ok"] is False
    assert "out_of_media_bounds" in _codes(result)
    assert result["checks"]["within_media_bounds"] is False


def test_within_media_bounds_passes():
    result = sv.validate_final_segment(_segment(), media_duration=120.0)
    assert result["ok"] is True
    assert result["checks"]["within_media_bounds"] is True


def test_negative_start_out_of_bounds():
    result = sv.validate_final_segment(_segment(start_time=-1.0, end_time=20.0), media_duration=120.0)
    assert "out_of_media_bounds" in _codes(result)


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------

def test_empty_transcript_rejected():
    result = sv.validate_final_segment(_segment(transcript_text="   "))
    assert result["ok"] is False
    assert "empty_transcript" in _codes(result)
    assert result["checks"]["transcript_present"] is False


def test_transcript_falls_back_to_window_analysis_and_text():
    assert sv.validate_final_segment(_segment(transcript_text=None, window_analysis={"text": "a real window text here"}))["ok"] is True
    assert sv.validate_final_segment(_segment(transcript_text=None, text="a real raw text here"))["ok"] is True


def test_short_transcript_is_warning_not_error():
    result = sv.validate_final_segment(_segment(transcript_text="too short"), min_lexical_words=5)
    assert result["ok"] is True
    assert "short_transcript" in _warning_codes(result)
    assert result["checks"]["transcript_present"] is True


def test_transcript_text_argument_overrides_segment():
    result = sv.validate_final_segment(_segment(transcript_text=""), transcript_text="override text wins")
    assert result["ok"] is True
    assert result["checks"]["transcript_present"] is True


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

def test_mid_sentence_window_flags_rejected():
    for flag in ("starts_mid_sentence", "ends_mid_sentence"):
        result = sv.validate_final_segment(_segment(window_analysis={flag: True}))
        assert result["ok"] is False
        assert "sentence_truncation" in _codes(result)
        assert result["checks"]["no_sentence_truncation"] is False
        assert flag in result["errors"][0]["message"]


def test_quality_flag_mid_sentence_rejected():
    result = sv.validate_final_segment(_segment(quality_flags=["ends mid-sentence after a dangling word"]))
    assert "sentence_truncation" in _codes(result)


def test_quality_flag_word_boundary_rejected():
    result = sv.validate_final_segment(_segment(quality_flags=["starts at a mid-word word_boundary"]))
    assert "word_truncation" in _codes(result)
    assert result["checks"]["no_word_truncation"] is False


def test_ends_incomplete_is_warning():
    result = sv.validate_final_segment(_segment(window_analysis={"ends_incomplete": True}))
    assert result["ok"] is True
    assert "incomplete_ending" in _warning_codes(result)


# ---------------------------------------------------------------------------
# Edge silence
# ---------------------------------------------------------------------------

def test_excessive_edge_silence_rejected():
    result = sv.validate_final_segment(_segment(window_analysis={"leading_silence": 5.0}))
    assert result["ok"] is False
    assert "excessive_edge_silence" in _codes(result)
    assert result["checks"]["edge_silence_ok"] is False


def test_edge_silence_near_limit_is_warning():
    result = sv.validate_final_segment(_segment(window_analysis={"trailing_silence": 1.5}))
    assert result["ok"] is True
    assert "edge_silence_warning" in _warning_codes(result)


def test_custom_edge_silence_limit():
    result = sv.validate_final_segment(_segment(window_analysis={"leading_silence": 3.0}), max_edge_silence=3.5)
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# Title
# ---------------------------------------------------------------------------

def test_title_rejected_is_error_with_reasons():
    segment = _segment(title_validation={"status": "rejected", "reasons": ["unsupported claim about the guest"]})
    result = sv.validate_final_segment(segment)
    assert result["ok"] is False
    assert "title_rejected" in _codes(result)
    assert "unsupported claim about the guest" in result["errors"][0]["message"]
    assert result["checks"]["title_status"] == "rejected"


def test_title_review_is_warning_and_ok():
    result = sv.validate_final_segment(_segment(title_validation={"status": "review"}))
    assert result["ok"] is True
    assert "title_needs_review" in _warning_codes(result)
    assert "title_rejected" not in _codes(result)
    assert result["checks"]["title_status"] == "review"


def test_title_missing_is_error_when_required():
    segment = _segment()
    del segment["title_validation"]
    result = sv.validate_final_segment(segment)
    assert result["ok"] is False
    assert "title_missing" in _codes(result)
    assert result["checks"]["title_status"] == "missing"


def test_title_missing_allowed_when_not_required():
    segment = _segment()
    del segment["title_validation"]
    result = sv.validate_final_segment(segment, require_title=False)
    assert result["ok"] is True
    assert "title_missing" not in _codes(result)


def test_title_validation_read_from_title_data():
    segment = _segment()
    del segment["title_validation"]
    segment["title_data"] = {"title_validation": {"status": "verified"}}
    assert sv.validate_final_segment(segment)["ok"] is True


def test_title_factual_validation_block_derived_status():
    segment = _segment(title_validation={"matches_transcript": True, "contains_hallucinated_fact": False})
    assert sv.validate_final_segment(segment)["checks"]["title_status"] == "verified"
    hallu = _segment(title_validation={"matches_transcript": False, "contains_hallucinated_fact": True})
    assert sv.validate_final_segment(hallu)["checks"]["title_status"] == "rejected"


# ---------------------------------------------------------------------------
# Safety and duplication
# ---------------------------------------------------------------------------

def test_safety_blocked_rejected():
    result = sv.validate_final_segment(_segment(safety_status="blocked"))
    assert result["ok"] is False
    assert "safety_blocked" in _codes(result)
    assert result["checks"]["safety_ok"] is False


def test_safety_unknown_is_none_and_passes():
    segment = _segment()
    del segment["safety_status"]
    result = sv.validate_final_segment(segment)
    assert result["ok"] is True
    assert result["checks"]["safety_ok"] is None


def test_safety_nested_blocked_rejected():
    segment = _segment()
    del segment["safety_status"]
    segment["safety"] = {"status": "unsafe"}
    assert "safety_blocked" in _codes(sv.validate_final_segment(segment))


def test_semantic_duplicate_rejected():
    result = sv.validate_final_segment(_segment(semantic_duplicate=True))
    assert result["ok"] is False
    assert "semantic_duplicate" in _codes(result)
    assert result["checks"]["duplicate_ok"] is False


def test_duplicate_of_marker_rejected():
    result = sv.validate_final_segment(_segment(duplicate_of="clip-3"))
    assert "semantic_duplicate" in _codes(result)


def test_temporal_duplicate_rejected():
    result = sv.validate_final_segment(_segment(temporal_duplicate=True))
    assert "temporal_duplicate" in _codes(result)
    assert result["checks"]["duplicate_ok"] is False


# ---------------------------------------------------------------------------
# Immutability / unknown fields
# ---------------------------------------------------------------------------

def test_unknown_fields_pass():
    result = sv.validate_final_segment(_segment(future_field="whatever", another=[1, 2, 3]))
    assert result["ok"] is True


def test_input_segment_not_mutated():
    segment = _segment(window_analysis={"leading_silence": 5.0, "starts_mid_sentence": True},
                       quality_flags=["ends mid-word"], semantic_duplicate=True)
    snapshot = copy.deepcopy(segment)
    sv.validate_final_segment(segment)
    assert segment == snapshot


def test_is_publishable_segment_helper():
    assert sv.is_publishable_segment(_segment()) is True
    assert sv.is_publishable_segment(_segment(safety_status="blocked")) is False
    assert sv.is_publishable_segment(_segment(), min_duration=60.0) is False


# ---------------------------------------------------------------------------
# Batch aggregation
# ---------------------------------------------------------------------------

def test_validate_final_segments_aggregates():
    segments = [
        _segment(),
        _segment(),
        _segment(end_time=None),
        "not a dict",
    ]
    report = sv.validate_final_segments(segments, min_duration=1.0)
    assert report["ok"] is False
    assert len(report["results"]) == 4
    assert report["invalid_indices"] == [2, 3]
    assert report["error_count"] >= 2
    assert report["results"][3]["errors"][0]["code"] == "invalid_segment"
    assert report["results"][0]["ok"] is True


def test_validate_final_segments_all_valid():
    report = sv.validate_final_segments([_segment(), _segment()])
    assert report["ok"] is True
    assert report["invalid_indices"] == []
    assert report["error_count"] == 0


def test_validate_final_segments_handles_empty_and_none():
    assert sv.validate_final_segments([]) == {
        "ok": True, "results": [], "invalid_indices": [], "error_count": 0,
    }
    assert sv.validate_final_segments(None)["ok"] is True
