# -*- coding: utf-8 -*-
"""Selection & anchoring regression tests (Arabic/Darija segment selection).

Covers:
* Arabic-Indic (٠-٩) / Persian (۰-۹) timestamp parsing incl. Arabic decimal
  separators ٫ (U+066B) and ٬ (U+066C);
* dropping candidates that carry NO placement anchor (no more fabricated
  (0, min_duration) windows at the video head);
* repeated catchphrase end_text landing on the SECOND occurrence instead of
  collapsing to the start line;
* transcript-shorter-than-min_duration flags (``under_min`` +
  ``transcript_limited``);
* ``finalize_top_segments`` export-ordering helper;
* source-video staleness fingerprint helpers;
* ``scripts.subtitle_language`` script detection + the transcribe_video
  external-subtitle branch using it;
* scene-snap bounded drift in ``cut_segments``.
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.create_viral_segments import (
    _has_any_anchor,
    _parse_segment_time,
    finalize_top_segments,
    process_segments,
    segments_source_fingerprint,
    source_video_fingerprint,
)
from scripts.subtitle_language import (
    choose_alignment_language,
    detect_text_script,
)

# ---------------------------------------------------------------------------
# Arabic-Indic / Persian timestamp parsing (_parse_segment_time)
# ---------------------------------------------------------------------------

def test_parse_arabic_indic_seconds_only():
    assert _parse_segment_time("٩٠") == 90.0


def test_parse_mixed_ascii_minutes_with_arabic_seconds():
    # "1:٩٠" => 1 min + 90 s = 150 s (rollover must NOT be rejected)
    assert _parse_segment_time("1:٩٠") == 150.0


def test_parse_arabic_indic_mmss():
    # "٩:٥٠" => 9 min + 50 s = 590 s
    assert _parse_segment_time("٩:٥٠") == 590.0


def test_parse_arabic_decimal_dot_after_translation():
    assert _parse_segment_time("١٢.٥") == 12.5


def test_parse_arabic_decimal_separator_u066b():
    # ٫ = Arabic decimal separator (U+066B)
    assert _parse_segment_time("٩٫٥") == 9.5


def test_parse_arabic_comma_u066c_as_decimal_point():
    # ٬ = Arabic comma (U+066C), used as decimal point by some Darija output
    assert _parse_segment_time("٩٬٥") == 9.5


def test_parse_persian_digits():
    assert _parse_segment_time("۴۵") == 45.0
    assert _parse_segment_time("۰:۴۵") == 45.0


def test_parse_h_mm_ss_after_translation():
    assert _parse_segment_time("١:٠٢:٠٣") == 3723.0


def test_parse_plain_english_timestamp_unchanged():
    assert _parse_segment_time("00:01:30") == 90.0
    assert _parse_segment_time("00:00:12.5") == 12.5
    assert _parse_segment_time("1:30") == 90.0


def test_parse_trailing_arabic_unit_words():
    assert _parse_segment_time("٩٠ ثانية") == 90.0


def test_parse_numeric_and_empty_values_unchanged():
    assert _parse_segment_time(45) == 45.0
    assert _parse_segment_time(0) == 0.0
    assert _parse_segment_time(None, default=3.5) == 3.5
    assert _parse_segment_time("", default=2.0) == 2.0


def test_parse_genuinely_malformed_falls_back_to_default():
    # Fallback-to-default preserved ONLY for genuinely malformed input.
    assert _parse_segment_time("soon", default=7.0) == 7.0
    assert _parse_segment_time("forty five") == 0.0
    assert _parse_segment_time("لا يوجد وقت", default=11.0) == 11.0


# ---------------------------------------------------------------------------
# Unanchorable candidates are dropped (process_segments)
# ---------------------------------------------------------------------------

def test_process_segments_drops_unanchorable_candidates():
    transcript = [
        {"start": 0.0, "end": 5.0, "text": "hello there"},
        {"start": 6.0, "end": 12.0, "text": "world of testing"},
        {"start": 20.0, "end": 30.0, "text": "last words here"},
    ]
    raw = [
        {"title": "No anchor A", "score": 95},
        {"title": "No anchor B", "score": 80},
    ]
    result = process_segments(raw, transcript, 5, 30)
    assert result["segments"] == []


def test_has_any_anchor_flags_each_anchor_kind():
    assert not _has_any_anchor({"title": "x", "score": 90})
    assert not _has_any_anchor(None)
    assert not _has_any_anchor("not a dict")
    # "(0s)" is the LLM "no timestamp found" sentinel, not an anchor.
    assert not _has_any_anchor({"title": "x", "start_time_ref": "(0s)"})
    assert _has_any_anchor({"title": "x", "start_time_ref": "12s"})
    # numeric 0 IS a legitimate anchor (video head can be intentional).
    assert _has_any_anchor({"title": "x", "start_time": 0})
    assert _has_any_anchor({"title": "x", "end_time": 30})
    assert _has_any_anchor({"title": "x", "start_text": "first words"})


def test_process_segments_keeps_candidate_anchored_by_numeric_end_only():
    transcript = [
        {"start": 0.0, "end": 5.0, "text": "opening"},
        {"start": 10.0, "end": 20.0, "text": "middle"},
        {"start": 25.0, "end": 30.0, "text": "closing"},
    ]
    raw = [{"title": "Only end", "end_time": 30, "score": 80}]
    result = process_segments(raw, transcript, 5, 30)
    assert len(result["segments"]) == 1
    assert result["segments"][0]["title"] == "Only end"


# ---------------------------------------------------------------------------
# Repeated end-phrase: end_text must land on a LATER occurrence
# ---------------------------------------------------------------------------

def test_repeated_catchphrase_ends_on_second_occurrence():
    # Catchphrase "machi sahla" spoken at 0-5s and repeated at 20-25s.
    transcript = [
        {"start": 0.0, "end": 5.0, "text": "machi sahla"},
        {"start": 6.0, "end": 10.0, "text": "some middle content"},
        {"start": 11.0, "end": 19.0, "text": "more talking in between"},
        {"start": 20.0, "end": 25.0, "text": "machi sahla"},
        {"start": 26.0, "end": 30.0, "text": "ending outro"},
    ]
    raw = [{"title": "Repeat", "start_text": "machi sahla",
            "end_text": "machi sahla", "score": 90}]
    result = process_segments(raw, transcript, 5, 30)
    seg = result["segments"][0]
    # Must end near the SECOND occurrence (~25s), not collapse at 5s.
    assert abs(seg["end_time"] - 25.0) < 0.01
    assert seg["start_time"] == 0.0
    assert seg["duration"] > 15.0


def test_repeated_catchphrase_with_single_occurrence_uses_min_extension():
    # Only one occurrence: no later end match exists -> unmatched end falls
    # back to the min-extension path (never fabricates a far-away window).
    transcript = [
        {"start": 0.0, "end": 5.0, "text": "machi sahla"},
        {"start": 6.0, "end": 10.0, "text": "middle content"},
        {"start": 11.0, "end": 15.0, "text": "wrapping up"},
    ]
    raw = [{"title": "Solo", "start_text": "machi sahla",
            "end_text": "machi sahla", "score": 90}]
    result = process_segments(raw, transcript, 5, 30)
    seg = result["segments"][0]
    assert seg["start_time"] == 0.0
    assert seg["end_time"] == pytest.approx(5.0)
    assert seg["duration"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Transcript shorter than min_duration -> flags on the output segment
# ---------------------------------------------------------------------------

def test_transcript_limited_under_min_sets_flags_and_full_window():
    # Whole transcript spans only 20s while min_duration is 30s.
    transcript = [
        {"start": 0.0, "end": 10.0, "text": "opening words here"},
        {"start": 15.0, "end": 20.0, "text": "later detail"},
    ]
    raw = [{"title": "End hook", "start_time_ref": "15s",
            "start_text": "later detail", "end_text": "", "score": 90}]
    result = process_segments(raw, transcript, 30, 90)
    seg = result["segments"][0]
    assert seg["under_min"] is True
    assert seg["transcript_limited"] is True
    # Window equals the full transcript (clamped behaviour unchanged).
    assert seg["start_time"] == 0.0
    assert seg["end_time"] == 20.0
    assert seg["duration"] == 20.0


def test_normal_min_extension_does_not_set_flags():
    transcript = [
        {"start": 0.0, "end": 10.0, "text": "opening"},
        {"start": 12.0, "end": 20.0, "text": "middle"},
        {"start": 22.0, "end": 40.0, "text": "long enough tail"},
    ]
    raw = [{"title": "ok", "start_time_ref": "22s",
            "start_text": "long enough tail", "end_text": "", "score": 90}]
    result = process_segments(raw, transcript, 8, 90)
    seg = result["segments"][0]
    assert "under_min" not in seg
    assert "transcript_limited" not in seg


# ---------------------------------------------------------------------------
# finalize_top_segments export ordering
# ---------------------------------------------------------------------------

def test_finalize_respects_candidate_rank_order():
    segments = [
        {"title": "legacy-high", "selection_score": 99.0, "score": 99},
        {"title": "rank-2", "candidate_rank": 2, "selection_score": 1},
        {"title": "rank-1", "candidate_rank": 1, "selection_score": 1},
        {"title": "rank-3", "candidate_rank": 3, "selection_score": 1},
    ]
    out = finalize_top_segments(segments, 10)
    assert [s["title"] for s in out] == ["rank-1", "rank-2", "rank-3", "legacy-high"]


def test_finalize_legacy_entries_come_after_ranked_by_selection_score():
    segments = [
        {"title": "legacy-low", "selection_score": 40.0, "score": 40},
        {"title": "ranked", "candidate_rank": 1, "selection_score": 1},
        {"title": "legacy-high", "selection_score": 90.0, "score": 90},
    ]
    out = finalize_top_segments(segments, 10)
    assert [s["title"] for s in out] == ["ranked", "legacy-high", "legacy-low"]


def test_finalize_legacy_tie_breaks_by_score_then_stability():
    segments = [
        {"title": "same-sel-lower-score", "selection_score": 50.0, "score": 3},
        {"title": "same-sel-higher-score", "selection_score": 50.0, "score": 9},
        {"title": "lower-sel", "selection_score": 10.0, "score": 99},
    ]
    out = finalize_top_segments(segments, 10)
    assert [s["title"] for s in out] == [
        "same-sel-higher-score", "same-sel-lower-score", "lower-sel"]


def test_finalize_stability_preserves_original_index_on_full_ties():
    segments = [
        {"title": "a", "selection_score": 50.0, "score": 5},
        {"title": "b", "selection_score": 50.0, "score": 5},
        {"title": "c", "selection_score": 50.0, "score": 5},
    ]
    assert [s["title"] for s in finalize_top_segments(segments, 10)] == ["a", "b", "c"]
    # Same candidate_rank ties also stay in original order.
    ranked = [
        {"title": "x", "candidate_rank": 1},
        {"title": "y", "candidate_rank": 1},
        {"title": "z", "candidate_rank": 2},
    ]
    assert [s["title"] for s in finalize_top_segments(ranked, 10)] == ["x", "y", "z"]


def test_finalize_requested_count_larger_than_list_returns_all():
    segments = [{"title": "only", "candidate_rank": 1}]
    out = finalize_top_segments(segments, 50)
    assert len(out) == 1
    assert out[0]["title"] == "only"


def test_finalize_zero_or_negative_count_returns_empty():
    segments = [{"title": "a", "candidate_rank": 1}]
    assert finalize_top_segments(segments, 0) == []
    assert finalize_top_segments(segments, -5) == []
    assert finalize_top_segments(segments, None) == []
    assert finalize_top_segments(segments, "abc") == []
    assert finalize_top_segments(None, 5) == []


# ---------------------------------------------------------------------------
# Source-video staleness helpers
# ---------------------------------------------------------------------------

class _FakeStat(object):
    def __init__(self, size, mtime_ns):
        self.st_size = size
        self.st_mtime_ns = mtime_ns


def test_source_video_fingerprint_stable_for_same_stat(tmp_path, monkeypatch):
    path = str(tmp_path / "video.mp4")
    monkeypatch.setattr(os, "stat", lambda p: _FakeStat(size=12345, mtime_ns=1000))
    first = source_video_fingerprint(path)
    second = source_video_fingerprint(path)
    assert first == second
    assert first is not None


def test_source_video_fingerprint_differs_when_mtime_changes(tmp_path, monkeypatch):
    path = str(tmp_path / "video.mp4")
    monkeypatch.setattr(os, "stat", lambda p: _FakeStat(size=12345, mtime_ns=1000))
    before = source_video_fingerprint(path)
    monkeypatch.setattr(os, "stat", lambda p: _FakeStat(size=12345, mtime_ns=9999))
    after = source_video_fingerprint(path)
    assert before != after


def test_source_video_fingerprint_differs_when_size_changes(tmp_path, monkeypatch):
    path = str(tmp_path / "video.mp4")
    monkeypatch.setattr(os, "stat", lambda p: _FakeStat(size=12345, mtime_ns=1000))
    before = source_video_fingerprint(path)
    monkeypatch.setattr(os, "stat", lambda p: _FakeStat(size=54321, mtime_ns=1000))
    after = source_video_fingerprint(path)
    assert before != after


def test_source_video_fingerprint_missing_path_returns_none(tmp_path):
    assert source_video_fingerprint(str(tmp_path / "missing.mp4")) is None
    assert source_video_fingerprint(None) is None
    assert source_video_fingerprint("") is None


def test_segments_source_fingerprint_reads_nested_field():
    data = {"segments": [], "source_meta": {"source_video_fp": "abc123"}}
    assert segments_source_fingerprint(data) == "abc123"


def test_segments_source_fingerprint_tolerates_missing():
    assert segments_source_fingerprint({"segments": []}) is None
    assert segments_source_fingerprint({"source_meta": {}}) is None
    assert segments_source_fingerprint({}) is None
    assert segments_source_fingerprint(None) is None
    assert segments_source_fingerprint("not a dict") is None


# ---------------------------------------------------------------------------
# subtitle_language script detection
# ---------------------------------------------------------------------------

def test_detect_text_script_pure_arabic_returns_ar():
    assert detect_text_script("مرحبا بكم في هذه الحلقة") == "ar"
    assert detect_text_script("كيفاش داير مع مصطفى بونيف") == "ar"


def test_detect_text_script_english_returns_en():
    assert detect_text_script("hello world this is an english subtitle") == "en"


def test_detect_text_script_mixed_ratio_band():
    # 5 Arabic + 20 Latin letters -> ratio 0.2 -> mixed
    text = "مرحبا " + "and some more latin words here"
    assert detect_text_script(text) == "mixed"


def test_detect_text_script_ratio_boundaries():
    # ratio exactly 0.35 -> 'ar'; ratio exactly 0.10 -> 'en'
    assert detect_text_script("م" * 7 + "a" * 13) == "ar"
    assert detect_text_script("م" + "a" * 9) == "en"


def test_detect_text_script_no_letters_returns_none():
    assert detect_text_script("") == "none"
    assert detect_text_script("   ") == "none"
    assert detect_text_script("12345 !!! ---") == "none"
    assert detect_text_script(None) == "none"
    assert detect_text_script(12345) == "none"


def test_choose_alignment_language_arabic_returns_ar():
    assert choose_alignment_language("مرحبا بكم في هذه الحلقة") == "ar"


def test_choose_alignment_language_latin_returns_forced_default():
    assert choose_alignment_language("hello world") == "en"
    assert choose_alignment_language("bonjour tout le monde", forced_default="fr") == "fr"


def test_choose_alignment_language_empty_text_returns_default():
    assert choose_alignment_language("") == "en"
    assert choose_alignment_language("12345", forced_default="fr") == "fr"


# ---------------------------------------------------------------------------
# transcribe_video: external Arabic subtitles are no longer forced through
# the English wav2vec2 aligner
# ---------------------------------------------------------------------------

def _whisperx_stub(calls):
    """Minimal whisperx stand-in recording align-model language requests."""
    stub = types.ModuleType("whisperx")

    def load_audio(_path):
        return b"<audio>"

    def load_align_model(**kwargs):
        calls.append(dict(kwargs))
        return object(), {"language": kwargs.get("language_code")}

    def align(segments, _model_a, _metadata, _audio, _device,
              return_char_alignments=False):
        return {"segments": segments, "language": "x"}

    utils = types.ModuleType("whisperx.utils")

    def get_writer(fmt, folder):
        def writer(result, input_file, options):
            # Dummy artifacts so the existence checks in transcribe() pass.
            base = os.path.splitext(os.path.basename(input_file))[0]
            with open(os.path.join(folder, "{}.{}".format(base, fmt)), "w",
                      encoding="utf-8") as fh:
                fh.write("dummy")
        return writer

    utils.get_writer = get_writer
    stub.utils = utils
    stub.load_audio = load_audio
    stub.load_align_model = load_align_model
    stub.align = align
    return stub, utils


def _torch_stub():
    stub = types.ModuleType("torch")
    stub.__version__ = "0.0-stub"

    class _Cuda(object):
        @staticmethod
        def is_available():
            return False
    stub.cuda = _Cuda
    stub.load = lambda *a, **k: None
    return stub


def _run_external_subtitle_transcribe(tmp_path, monkeypatch, srt_text):
    import scripts.transcribe_video as tv

    calls = []
    stub, utils = _whisperx_stub(calls)
    monkeypatch.setattr(tv, "torch", _torch_stub())
    monkeypatch.setattr(tv, "whisperx", stub)
    # Register so the runtime `from whisperx.utils import get_writer` import
    # resolves inside transcribe() (and restore after the test).
    monkeypatch.setitem(sys.modules, "whisperx", stub)
    monkeypatch.setitem(sys.modules, "whisperx.utils", utils)
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    (project / "input.srt").write_text(srt_text, encoding="utf-8")
    input_file = str(tmp_path / "input.mp4")
    with open(input_file, "wb") as fh:
        fh.write(b"fake mp4 bytes")
    tv.transcribe(input_file, model_name="large-v3",
                  project_folder=str(project), device="cpu")
    assert calls, "whisperx.load_align_model must have been called"
    return calls


def test_transcribe_video_uses_arabic_aligner_for_arabic_subtitles(tmp_path, monkeypatch):
    arabic_srt = (
        "1\n00:00:00,000 --> 00:00:03,120\n"
        "مرحبا بكم في هذه الحلقة\n\n"
        "2\n00:00:03,120 --> 00:00:09,840\n"
        "نستضيف اليوم ضيفنا الكريم\n"
    )
    calls = _run_external_subtitle_transcribe(tmp_path, monkeypatch, arabic_srt)
    assert calls[0]["language_code"] == "ar"


def test_transcribe_video_keeps_english_aligner_for_english_subtitles(tmp_path, monkeypatch):
    english_srt = (
        "1\n00:00:00,000 --> 00:00:03,120\n"
        "Welcome to the show\n\n"
        "2\n00:00:03,120 --> 00:00:09,840\n"
        "Today we talk about video editing\n"
    )
    calls = _run_external_subtitle_transcribe(tmp_path, monkeypatch, english_srt)
    assert calls[0]["language_code"] == "en"


# ---------------------------------------------------------------------------
# cut_segments: scene-snap bounded drift
# ---------------------------------------------------------------------------

def test_accept_scene_snap_accepts_within_bounds():
    from scripts.cut_segments import _accept_scene_snap
    assert _accept_scene_snap(10.0, 10.0) is True
    assert _accept_scene_snap(10.0, 15.0) is True   # 1.5x boundary
    assert _accept_scene_snap(10.0, 5.0) is True    # 0.5x boundary
    assert _accept_scene_snap(10.0, 12.3) is True


def test_accept_scene_snap_rejects_overgrowth_and_overshrink():
    from scripts.cut_segments import _accept_scene_snap
    assert _accept_scene_snap(10.0, 15.01) is False
    assert _accept_scene_snap(10.0, 4.99) is False
    assert _accept_scene_snap(10.0, 40.0) is False
    assert _accept_scene_snap(10.0, 0.0) is False


def test_accept_scene_snap_floor_for_tiny_windows():
    from scripts.cut_segments import _accept_scene_snap
    # 1.0s floor: max(1.0, 0.5*orig) .. max(1.0, 1.5*orig)
    assert _accept_scene_snap(0.5, 1.0) is True    # both bounds clamp to 1.0
    assert _accept_scene_snap(0.5, 0.75) is False  # below the 1.0 floor
    assert _accept_scene_snap(0.5, 1.2) is False   # above the 1.0 ceiling
    assert _accept_scene_snap(2.0, 1.0) is True    # 0.5x of 2s == floor 1.0
    assert _accept_scene_snap(2.0, 0.9) is False


def test_accept_scene_snap_rejects_invalid_input():
    from scripts.cut_segments import _accept_scene_snap
    assert _accept_scene_snap("abc", 10.0) is False
    assert _accept_scene_snap(10.0, None) is False
    assert _accept_scene_snap(-1.0, 5.0) is False


def test_scene_snap_block_reverts_overgrown_window(tmp_path, monkeypatch):
    """A snap that more than doubles the clip must be reverted."""
    from scripts import cut_segments, scene_detect

    monkeypatch.setattr(scene_detect, "find_scene_cuts",
                        lambda path: [(0.0, 60.0)])
    # Fake snap that drags the end 40s later: 10s window -> 50s window.
    monkeypatch.setattr(scene_detect, "snap_to_scene",
                        lambda s0, s1, scenes: (s0, s1 + 40.0))

    source = tmp_path / "input.mp4"
    source.write_bytes(b"source")
    response = {"segments": [{"title": "clip", "start_time": 10, "end_time": 20}]}
    captured = []
    monkeypatch.setattr(cut_segments, "_detect_best_encoder",
                        lambda: ("copy", "fast", []))
    monkeypatch.setattr(
        cut_segments, "_process_segment",
        lambda index, segment, *a, **k: (captured.append(segment),
                                         {"ok": True, "index": index})[1])

    cut_segments.cut(response, project_folder=str(tmp_path), workers=1,
                     source_video=str(source), scene_snap=True)
    seg = captured[0]
    assert (seg["start_time"], seg["end_time"]) == (10.0, 20.0)
    assert not seg.get("scene_snapped")


def test_scene_snap_block_applies_bounded_snap(tmp_path, monkeypatch):
    """A snap that stays inside [0.5x, 1.5x] is accepted and marked."""
    from scripts import cut_segments, scene_detect

    monkeypatch.setattr(scene_detect, "find_scene_cuts",
                        lambda path: [(0.0, 60.0)])
    monkeypatch.setattr(scene_detect, "snap_to_scene",
                        lambda s0, s1, scenes: (s0 + 1.0, s1 + 2.0))

    source = tmp_path / "input.mp4"
    source.write_bytes(b"source")
    response = {"segments": [{"title": "clip", "start_time": 10, "end_time": 20}]}
    captured = []
    monkeypatch.setattr(cut_segments, "_detect_best_encoder",
                        lambda: ("copy", "fast", []))
    monkeypatch.setattr(
        cut_segments, "_process_segment",
        lambda index, segment, *a, **k: (captured.append(segment),
                                         {"ok": True, "index": index})[1])

    cut_segments.cut(response, project_folder=str(tmp_path), workers=1,
                     source_video=str(source), scene_snap=True)
    seg = captured[0]
    # 10s -> 11s window: inside bounds, snap applied.
    assert (seg["start_time"], seg["end_time"]) == (11.0, 22.0)
    assert seg.get("scene_snapped") is True
