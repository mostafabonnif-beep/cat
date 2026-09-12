# -*- coding: utf-8 -*-
"""v7.41 — selection-hardening regression tests.

These reproduce the exact failures the v7.41 spec calls out and lock in the
new behaviour:

1.  explicit numeric start_time inside a word is snapped safely;
2.  explicit numeric end_time inside a word is snapped safely;
3.  an explicit complete numeric window is still boundary-validated;
4.  a recommended_title with an unsupported number is rejected/replaced;
5.  a recommended_title about the whole video (not the clip) is rejected;
9.  reversed windows are recovered from text or rejected — never swapped;
10. short clips are not blindly padded with unrelated speech;
11. missing editorial scores are not treated as verified editorial scores;
13. process_segments never mutates the caller's raw_segments list;
14. the final validator rejects a missing end_time;
15. the final validator rejects an empty transcript.

Plus the save/cut/publish integration of the reusable final validator.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import create_viral_segments as cvs
from scripts import segment_validator as sv

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Ten 1-second words covering [0, 10): exact edges are always word edges.
_WORDS = [{"start": float(i), "end": float(i + 1), "word": "word{}".format(i)}
          for i in range(10)]
_WORD_TEXT = " ".join(word["word"] for word in _WORDS)


def _write_word_timings(project_folder):
    """WhisperX-style input.json with per-word timings."""
    payload = {"segments": [{"start": 0.0, "end": 10.0, "text": _WORD_TEXT,
                             "words": _WORDS}]}
    path = os.path.join(project_folder, "input.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return str(project_folder)


def _word_transcript():
    return [{"start": 0.0, "end": 10.0, "text": _WORD_TEXT}]


_ARABIC_TRANSCRIPT = [
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
# 1+2. Explicit numeric timestamps respect word boundaries
# ---------------------------------------------------------------------------

def test_explicit_start_inside_word_is_snapped(tmp_path):
    _write_word_timings(tmp_path)
    raw = [_candidate("WordStart", 1.5, 6.0, score=90)]
    result = cvs.process_segments(raw, _word_transcript(), 1, 60,
                                  snap_to_boundaries=False,
                                  project_folder=str(tmp_path))
    seg = result["segments"][0]
    assert seg["start_time"] == 1.0        # snapped back to word #1's start
    assert seg["start_time"] not in (1.5,)  # never inside a word


def test_explicit_end_inside_word_is_snapped(tmp_path):
    _write_word_timings(tmp_path)
    raw = [_candidate("WordEnd", 1.0, 6.5, score=90)]
    result = cvs.process_segments(raw, _word_transcript(), 1, 60,
                                  snap_to_boundaries=False,
                                  project_folder=str(tmp_path))
    seg = result["segments"][0]
    assert seg["end_time"] == 7.0          # finished the word it cut into


# ---------------------------------------------------------------------------
# 3. Explicit complete numeric windows are still validated
# ---------------------------------------------------------------------------

def test_explicit_numeric_window_carries_final_validation():
    raw = [_candidate("Validated", 1.0, 6.0, score=90)]
    result = cvs.process_segments(raw, _ARABIC_TRANSCRIPT, 5, 60,
                                  snap_to_boundaries=False)
    seg = result["segments"][0]
    assert "final_validation" in seg
    assert seg["final_validation"]["checks"]["end_after_start"] is True


def test_explicit_window_outside_media_keeps_no_segment():
    # A window clamped by a very short REAL media duration is re-clamped, so
    # the final validator never sees an out-of-range window in production.
    raw = [_candidate("Short", 30.0, 40.0, score=90)]
    result = cvs.process_segments(raw, _ARABIC_TRANSCRIPT, 5, 60,
                                  media_duration=3.0)
    for seg in result["segments"]:
        assert seg["end_time"] <= 3.0 + 1e-6


# ---------------------------------------------------------------------------
# 4+5. recommended_title validation (numbers + whole-video framing)
# ---------------------------------------------------------------------------

def test_recommended_title_with_unsupported_number_is_rejected():
    raw = [_candidate("عنوان محايد", 0.0, 5.0, score=90,
                      recommended_title="ربحت 5000 دولار في يوم واحد")]
    result = cvs.process_segments(raw, _ARABIC_TRANSCRIPT, 5, 60)
    seg = result["segments"][0]
    assert seg["recommended_title"] != "ربحت 5000 دولار في يوم واحد"
    rejected = seg.get("title_data", {}).get("rejected_titles", [])
    assert any("5000" in item.get("text", "") for item in rejected)


def test_recommended_title_about_whole_video_is_rejected():
    # A 5s clip inside a 52s transcript cannot honestly wear a "full episode"
    # title: the scope marker plus a tiny clip_ratio must reject it.
    raw = [_candidate("عنوان محايد", 0.0, 5.0, score=90,
                      recommended_title="ملخص الحلقة كاملة كل الفيديو")]
    result = cvs.process_segments(raw, _ARABIC_TRANSCRIPT, 5, 60)
    seg = result["segments"][0]
    rejected = seg.get("title_data", {}).get("rejected_titles", [])
    assert any("الحلقة كاملة" in item.get("text", "") for item in rejected) or (
        seg.get("title_validation", {}).get("status") != "verified")


# ---------------------------------------------------------------------------
# 10. Short clips are never padded with unrelated speech
# ---------------------------------------------------------------------------

def test_short_clip_is_not_extended_with_unrelated_speech():
    transcript = [
        {"start": 0.0, "end": 2.0, "text": "موضوع اول هنا"},
        {"start": 35.0, "end": 45.0, "text": "شيء مختلف تماما عن السابق"},
    ]
    raw = [_candidate("Short", 0.0, 2.0, score=90)]
    result = cvs.process_segments(raw, transcript, 15, 60)
    assert result["segments"], "candidate must stay (transcript-limited), not vanish"
    seg = result["segments"][0]
    assert seg["duration"] < 15.0
    assert seg["transcript_limited"] is True
    assert seg["end_time"] <= 3.0          # no jump into the 35s material


# ---------------------------------------------------------------------------
# 11. Missing editorial scores are unverified, not copied virality
# ---------------------------------------------------------------------------

def test_missing_editorial_scores_are_unverified_not_virality_copies():
    raw = [_candidate("NoEval", 2.0, 8.0, score=93)]
    seg = cvs.process_segments(raw, _word_transcript(), 1, 60,
                               snap_to_boundaries=False)["segments"][0]
    assert seg["quality_status"] == "unverified"
    assert seg["quality_missing"] is True
    assert seg["hook_strength"] is None
    assert seg["narrative_completeness"] is None
    assert seg["clarity_score"] is None
    # Not treated as a genuine 93/100 editorial evaluation anywhere.
    assert seg.get("hook_strength") != 93


def test_unverified_incomplete_candidate_requires_review():
    raw = [_candidate("NoEval", 2.0, 8.0, score=93)]
    seg = cvs.process_segments(raw, _word_transcript(), 1, 60,
                               snap_to_boundaries=False)["segments"][0]
    assert seg["completion_status"] != "complete"
    assert seg["requires_review"] is True
    assert seg["publish_blocked_reason"] == "manual_review_required"


# ---------------------------------------------------------------------------
# 13. process_segments never mutates the caller's list
# ---------------------------------------------------------------------------

def test_process_segments_does_not_mutate_input_list():
    low = _candidate("Low", 1.0, 6.0, score=10)
    high = _candidate("High", 0.0, 5.0, score=95)
    raw = [low, high]
    low_keys = set(low)
    cvs.process_segments(raw, _ARABIC_TRANSCRIPT, 1, 60)
    assert [item["title"] for item in raw] == ["Low", "High"]  # order intact
    assert set(low) == low_keys                                # no injected keys


# ---------------------------------------------------------------------------
# 14+15. Final validator structural rejections
# ---------------------------------------------------------------------------

def test_final_validator_rejects_missing_end_time():
    report = sv.validate_final_segment({"start_time": 10.0, "transcript_text": "hello"})
    assert report["ok"] is False
    assert any(item["code"] == "missing_end_time" for item in report["errors"])


def test_final_validator_rejects_empty_transcript():
    report = sv.validate_final_segment(
        {"start_time": 1.0, "end_time": 5.0, "transcript_text": "   "})
    assert report["ok"] is False
    assert any(item["code"] == "empty_transcript" for item in report["errors"])


# ---------------------------------------------------------------------------
# Integration: save / publish boundaries
# ---------------------------------------------------------------------------

def test_save_json_annotates_and_blocks_invalid_window(tmp_path):
    from scripts.save_json import save_viral_segments

    payload = {"segments": [{"title": "Bad", "start_time": 10, "end_time": 5,
                             "transcript_text": "hello world"}]}
    save_viral_segments(payload, str(tmp_path), overwrite=True)
    saved = json.loads((tmp_path / "viral_segments.txt").read_text(encoding="utf-8"))
    seg = saved["segments"][0]
    assert seg["final_validation"]["ok"] is False
    assert seg["requires_review"] is True
    assert seg["export_blocked"] is True


def test_upload_gate_refuses_review_flagged_segment(tmp_path):
    from scripts import upload_gate

    payload = {"segments": [{
        "title": "Clip", "start_time": 0.0, "end_time": 10.0,
        "transcript_text": "محادثة قصيرة",
        "requires_review": True,
        "publish_blocked_reason": "manual_review_required",
    }]}
    (tmp_path / "viral_segments.txt").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    verdict = upload_gate.check_clip(str(tmp_path), index=0,
                                     title="عنوان", caption="وصف")
    assert verdict["allowed"] is False
    assert any(reason.get("source") == "segment_validation"
               for reason in verdict["reasons"])


def test_review_title_choice_is_factually_validated(tmp_path):
    from webui import segments_review

    payload = {"segments": [{
        "title": "عنوان صحيح",
        "recommended_title": "عنوان صحيح",
        "alt_titles": ["ربحت 5000 دولار في يوم"],
        "start_time": 0.0, "end_time": 5.0,
        "transcript_text": "تحدثنا اليوم عن الخطوات الأساسية للنجاح",
        "title_data": {"title_language": "ar"},
    }]}
    (tmp_path / "viral_segments.txt").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    ok, message = segments_review.choose_title(
        str(tmp_path), 0, "ربحت 5000 دولار في يوم")
    assert ok is False
    assert message


# ---------------------------------------------------------------------------
# Provenance: transcript + weights fingerprints
# ---------------------------------------------------------------------------

def test_selection_config_records_transcript_and_weight_fingerprints():
    result = cvs.process_segments([_candidate("One", 0.0, 5.0, score=80)],
                                  _ARABIC_TRANSCRIPT, 1, 60)
    config = result["selection_config"]
    assert config["transcript_fingerprint"]
    assert config["weights_fingerprint"]
    assert config["title_schema_version"]


def test_transcript_fingerprint_changes_with_content():
    a = cvs.transcript_fingerprint([{"start": 0, "end": 1, "text": "hello"}])
    b = cvs.transcript_fingerprint([{"start": 0, "end": 1, "text": "world"}])
    assert a and b and a != b
