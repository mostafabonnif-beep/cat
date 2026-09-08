# -*- coding: utf-8 -*-
"""v7.33.1 — persisted A/B title choice + top-level metadata preservation."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webui import segments_review


def _write_project(tmp_path, with_meta=True):
    project = tmp_path / "proj"
    project.mkdir()
    payload = {
        "segments": [
            {"title": "Main A", "recommended_title": "Main A",
             "start_time": 10.0, "end_time": 40.0, "score": 90,
             "alt_titles": ["Alt A1", "Alt A2"]},
            {"title": "Main B", "start_time": 50.0, "end_time": 80.0,
             "score": 80, "alt_titles": ["Alt B1"]},
        ],
    }
    if with_meta:
        payload["source_meta"] = {"source_video_fp": "abc123",
                                  "config_fp": "cfg456"}
    (project / "viral_segments.txt").write_text(
        json.dumps(payload), encoding="utf-8")
    return str(project), payload


def test_title_choices_for_returns_main_plus_alts(tmp_path):
    project, _ = _write_project(tmp_path)
    options, current = segments_review.title_choices_for(project, 0)
    assert current == "Main A"
    assert options == ["Main A", "Alt A1", "Alt A2"]
    options_b, current_b = segments_review.title_choices_for(project, 1)
    assert current_b == "Main B"
    assert options_b == ["Main B", "Alt B1"]


def test_title_choices_out_of_range(tmp_path):
    project, _ = _write_project(tmp_path)
    assert segments_review.title_choices_for(project, 5) == ([], "")
    assert segments_review.title_choices_for(project, -1) == ([], "")


def test_choose_title_persists_recommended_title(tmp_path):
    project, _ = _write_project(tmp_path)
    ok, message = segments_review.choose_title(project, 0, "Alt A1")
    assert ok is True
    assert "Alt A1" in message
    segments = segments_review.load_segments(project)
    assert segments[0]["recommended_title"] == "Alt A1"
    assert segments[0]["title_chosen"] == "Alt A1"


def test_choose_title_rejects_foreign_title(tmp_path):
    project, _ = _write_project(tmp_path)
    ok, _ = segments_review.choose_title(project, 0, "Clickbait not offered")
    assert ok is False
    # untouched
    assert segments_review.load_segments(project)[0]["recommended_title"] == "Main A"


def test_choose_title_same_value_is_noop(tmp_path):
    project, _ = _write_project(tmp_path)
    ok, message = segments_review.choose_title(project, 0, "Main A")
    assert ok is True
    assert "unchanged" in message.lower() or "لم يتغير" in message


def test_choose_title_out_of_range(tmp_path):
    project, _ = _write_project(tmp_path)
    assert segments_review.choose_title(project, 99, "Alt A1")[0] is False


def test_apply_selection_preserves_top_level_metadata(tmp_path):
    project, _ = _write_project(tmp_path)
    rows = segments_review.rows_from_segments(segments_review.load_segments(project))
    rows[1][0] = False  # keep only segment A
    segments_review.apply_selection(project, rows)
    payload = json.load(open(os.path.join(project, "viral_segments.txt"),
                             encoding="utf-8"))
    assert payload["source_meta"] == {"source_video_fp": "abc123",
                                      "config_fp": "cfg456"}
    assert len(payload["segments"]) == 1


def test_choose_title_preserves_top_level_metadata(tmp_path):
    project, _ = _write_project(tmp_path)
    segments_review.choose_title(project, 1, "Alt B1")
    payload = json.load(open(os.path.join(project, "viral_segments.txt"),
                             encoding="utf-8"))
    assert payload["source_meta"]["config_fp"] == "cfg456"
    assert payload["segments"][1]["recommended_title"] == "Alt B1"


def test_load_segments_handles_missing_and_invalid(tmp_path):
    assert segments_review.load_segments(str(tmp_path)) == []
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "viral_segments.txt").write_text("{not json", encoding="utf-8")
    assert segments_review.load_segments(str(bad)) == []
