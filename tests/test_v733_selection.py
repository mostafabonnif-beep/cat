# -*- coding: utf-8 -*-
"""v7.33 — professional-selection hardening tests.

Covers the clamp-aware sentence snapping, reversed-window realignment,
quality-missing transparency flags and the project-aware performance-weights
wiring in scripts/create_viral_segments.process_segments.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import create_viral_segments as cvs


def _sentence_transcript():
    """Blocks with clear pauses: A[0,10) B[11,21) C[22,32) + 1s gaps."""
    return [
        {"start": 0.0, "end": 10.0, "text": "first long sentence here"},
        {"start": 11.0, "end": 21.0, "text": "second long sentence here"},
        {"start": 22.0, "end": 32.0, "text": "third long sentence here"},
    ]


def test_reversed_explicit_window_is_swapped_not_relocated():
    transcript = _sentence_transcript()
    raw = [{"title": "Reversed", "start_time": 30, "end_time": 12,
            "score": 90}]
    result = cvs.process_segments(raw, transcript, 5, 60,
                                  snap_to_boundaries=False)
    seg = result["segments"][0]
    assert seg["start_time"] == 12.0
    assert seg["end_time"] == 30.0
    assert seg["duration"] == 18.0


def test_clamped_explicit_end_is_snapped_to_sentence_end():
    # Explicit end (12.0) is mid-sentence inside B; min-duration extends the
    # window to 26.0 which is still mid-sentence (inside C). The clamp moved
    # the model edge, so the professional contract says: snap to C's end.
    transcript = _sentence_transcript()
    raw = [{"title": "UnderMin", "start_time": 11.0, "end_time": 12.0,
            "score": 90}]
    result = cvs.process_segments(raw, transcript, 15, 60)
    seg = result["segments"][0]
    assert (round(seg["start_time"], 1), round(seg["end_time"], 1)) == (11.0, 32.0)
    assert seg["duration"] >= 15.0


def test_clean_explicit_window_is_still_trusted_unsnapped():
    # Fully explicit, never adjusted by the clamp: the model's own edges are
    # the documented contract and must remain byte-exact (no snapping).
    transcript = _sentence_transcript()
    raw = [{"title": "Exact", "start_time": 11.0, "end_time": 16.5,
            "score": 90}]
    result = cvs.process_segments(raw, transcript, 5, 60)
    seg = result["segments"][0]
    assert seg["start_time"] == 11.0
    assert seg["end_time"] == 16.5


def test_snap_never_breaks_min_duration_after_clamp():
    # Transcript too short to satisfy the minimum at a snapped boundary: the
    # fallback keeps the raw clamped window instead of an under-min snap.
    transcript = [{"start": 0.0, "end": 4.0, "text": "only sentence"}]
    raw = [{"title": "Tiny", "start_time": 0.0, "end_time": 1.0, "score": 80}]
    result = cvs.process_segments(raw, transcript, 3, 30)
    seg = result["segments"][0]
    assert seg["duration"] >= 3.0
    assert seg["end_time"] <= 4.0


def test_quality_missing_flag_when_ai_ships_no_self_evaluation():
    transcript = _sentence_transcript()
    raw = [{"title": "Lazy", "start_time": 11.0, "end_time": 21.0,
            "score": 90, "reasoning": "no components"}]
    seg = cvs.process_segments(raw, transcript, 5, 60)["segments"][0]
    assert seg.get("quality_missing") is True
    assert set(seg.get("missing_components", [])) == {
        "hook_strength", "narrative_completeness", "clarity_score", "novelty_score"}


def test_no_quality_missing_flag_when_components_shipped():
    transcript = _sentence_transcript()
    raw = [{"title": "Full", "start_time": 11.0, "end_time": 21.0, "score": 90,
            "hook_strength": 80, "narrative_completeness": 75,
            "clarity_score": 70, "novelty_score": 60}]
    seg = cvs.process_segments(raw, transcript, 5, 60)["segments"][0]
    assert "quality_missing" not in seg


def test_performance_weights_read_from_project_folder(tmp_path, monkeypatch):
    # The learning loop writes performance_insights.json into the PROJECT
    # folder; process_segments used to read os.getcwd() and silently miss it.
    project = tmp_path / "proj"
    project.mkdir()
    insights = {
        "with_metrics": 5,
        "correlations": {
            "hook_strength": {"vs_views": 0.5},
            "duration": {"vs_views": 0.6},
            "title_quality_score": {"vs_views": 0.6},
        },
    }
    (project / "performance_insights.json").write_text(
        json.dumps(insights), encoding="utf-8")

    from scripts import performance_weights
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)  # cwd contains NO insights file
        transcript = _sentence_transcript()
        raw = [{"title": "Learned", "start_time": 11.0, "end_time": 21.0,
                "score": 90, "hook_strength": 80, "narrative_completeness": 75,
                "clarity_score": 70, "novelty_score": 60}]
        result = cvs.process_segments(raw, transcript, 5, 60,
                                      project_folder=str(project))
        seg = result["segments"][0]
        breakdown = seg.get("selection_breakdown", {})
        assert breakdown.get("performance_basis") == "performance_insights"
        assert breakdown.get("title_boost") == pytest.approx(0.12)
        assert breakdown.get("duration_bonus") == pytest.approx(0.12)
    finally:
        os.chdir(original_cwd)


def test_performance_weights_defaults():
    from scripts import performance_weights
    weights = performance_weights.load_weights(None)
    assert weights["basis"] == "defaults"
    assert weights["title_boost"] == 0.0
    assert weights["duration_bonus"] == 0.0
