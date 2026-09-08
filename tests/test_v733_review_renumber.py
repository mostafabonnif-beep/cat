# -*- coding: utf-8 -*-
"""v7.33 — review-table rank renumbering after manual selection."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webui import segments_review


def _sample_segments():
    return [
        {"title": "A", "start_time": 10.0, "end_time": 40.0, "score": 95,
         "selection_score": 88.0, "candidate_rank": 1},
        {"title": "B", "start_time": 60.0, "end_time": 90.0, "score": 80,
         "selection_score": 91.0, "candidate_rank": 2},
        {"title": "C", "start_time": 100.0, "end_time": 130.0, "score": 70,
         "selection_score": 60.0, "candidate_rank": 3},
    ]


def test_renumber_single_function_is_sequential():
    segments = _sample_segments()
    # Drop B out of the middle on purpose.
    kept = [segments[0], segments[2]]
    ordered = segments_review._renumber_candidate_ranks(kept)
    assert [item["candidate_rank"] for item in ordered] == [1, 2]
    # Order preserved: rank-1 candidate stays first.
    assert [item["title"] for item in ordered] == ["A", "C"]


def test_renumber_promotes_higher_selection_score_legacy():
    legacy = [
        {"title": "Low", "start_time": 1.0, "end_time": 5.0, "score": 99,
         "selection_score": 40.0},
        {"title": "High", "start_time": 6.0, "end_time": 9.0, "score": 70,
         "selection_score": 92.0},
    ]
    ordered = segments_review._renumber_candidate_ranks(legacy)
    assert [item["title"] for item in ordered] == ["High", "Low"]
    assert [item["candidate_rank"] for item in ordered] == [1, 2]


def test_apply_selection_renumbers_ranks_on_disk(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    payload = {"segments": _sample_segments()}
    (project / "viral_segments.txt").write_text(
        json.dumps(payload), encoding="utf-8")

    rows = segments_review.rows_from_segments(payload["segments"])
    rows[1][0] = False  # deselect B (rank 2)
    kept, total, _ = segments_review.apply_selection(str(project), rows)
    assert (kept, total) == (2, 3)

    on_disk = segments_review.load_segments(str(project))
    assert [item["candidate_rank"] for item in on_disk] == [1, 2]
    assert [item["title"] for item in on_disk] == ["A", "C"]


def test_apply_selection_renumber_keeps_full_selection_stable(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    payload = {"segments": _sample_segments()}
    (project / "viral_segments.txt").write_text(
        json.dumps(payload), encoding="utf-8")
    rows = segments_review.rows_from_segments(payload["segments"])
    kept, total, _ = segments_review.apply_selection(str(project), rows)
    assert (kept, total) == (3, 3)
    on_disk = segments_review.load_segments(str(project))
    assert [item["title"] for item in on_disk] == ["A", "B", "C"]
    assert [item["candidate_rank"] for item in on_disk] == [1, 2, 3]
