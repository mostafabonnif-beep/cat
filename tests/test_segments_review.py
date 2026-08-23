"""Tests for the segment review & selection logic (webui/segments_review.py)."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webui import segments_review

SAMPLE = {
    "segments": [
        {"title": "Hook A", "start_time": 10.0, "end_time": 40.0, "reasoning": "strong hook", "score": 95,
         "caption": "لن تصدق ما حدث!", "hashtags": ["viral", "story"]},
        {"title": "Story B", "start_time": 65.5, "end_time": 120.0, "reasoning": "emotional", "score": 80,
         "caption": "قصة حقيقية ملهمة", "hashtags": "motivation,daily"},
        {"title": "Tip C", "start_time": 200.0, "end_time": 230.0, "reasoning": "actionable", "score": 70},
    ]
}


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "viral_segments.txt").write_text(json.dumps(SAMPLE), encoding="utf-8")
    return str(proj)


def test_load_segments(project):
    segments = segments_review.load_segments(project)
    assert len(segments) == 3
    assert segments[0]["title"] == "Hook A"


def test_load_segments_missing_file(tmp_path):
    assert segments_review.load_segments(str(tmp_path)) == []


def test_load_segments_invalid_json(project):
    with open(os.path.join(project, "viral_segments.txt"), "w") as f:
        f.write("{not json")
    assert segments_review.load_segments(project) == []


def test_rows_from_segments():
    rows = segments_review.rows_from_segments(SAMPLE["segments"])
    assert len(rows) == 3
    # [selected, title, score, start, end, duration, reason]
    assert rows[0][0] is True
    assert rows[0][1] == "Hook A"
    assert rows[0][2] == 95
    assert rows[0][3] == "00:10"
    assert rows[1][3] == "01:06"  # 65.5s rounds to 66
    assert rows[0][5] == 30.0      # 40 - 10
    assert rows[1][5] == 54.5      # 120 - 65.5


def test_apply_selection_filters_and_backs_up(project):
    rows = segments_review.rows_from_segments(SAMPLE["segments"])
    rows[1][0] = False  # deselect segment B

    kept, total, cuts_cleared = segments_review.apply_selection(project, rows)

    assert (kept, total) == (2, 3)
    assert cuts_cleared is False  # no cuts dir yet

    # filtered file on disk
    segments = segments_review.load_segments(project)
    assert [s["title"] for s in segments] == ["Hook A", "Tip C"]

    # backup holds the ORIGINAL full set
    bak = os.path.join(project, "viral_segments.full_backup.json")
    assert os.path.exists(bak)
    backup = json.loads(open(bak, encoding="utf-8").read())
    assert len(backup["segments"]) == 3


def test_apply_selection_invalidates_stale_cuts(project):
    cuts = os.path.join(project, "cuts")
    os.makedirs(cuts)
    open(os.path.join(cuts, "seg_1.mp4"), "w").write("x")

    rows = segments_review.rows_from_segments(SAMPLE["segments"])
    rows[2][0] = False

    _, _, cuts_cleared = segments_review.apply_selection(project, rows)
    assert cuts_cleared is True
    assert not os.path.exists(cuts)


def test_apply_selection_all_selected_no_backup(project):
    rows = segments_review.rows_from_segments(SAMPLE["segments"])
    kept, total, cuts_cleared = segments_review.apply_selection(project, rows)
    assert (kept, total) == (3, 3)
    assert cuts_cleared is False
    assert not os.path.exists(os.path.join(project, "viral_segments.full_backup.json"))


def test_apply_selection_never_writes_empty(project):
    rows = segments_review.rows_from_segments(SAMPLE["segments"])
    for r in rows:
        r[0] = False
    kept, total, _ = segments_review.apply_selection(project, rows)
    assert kept == 3  # falls back to keeping everything
    assert len(segments_review.load_segments(project)) == 3


def test_apply_selection_pandas_like_input(project):
    class FakeDf:
        def __init__(self, col):
            self._col = col

        @property
        def iloc(self):
            df = self

            class _ILoc:
                def __getitem__(self, key):
                    return self

                def tolist(self):
                    return df._col

            return _ILoc()

    df = FakeDf([True, False, True])
    kept, total, _ = segments_review.apply_selection(project, df)
    assert (kept, total) == (2, 3)


def test_restore_all(project):
    rows = segments_review.rows_from_segments(SAMPLE["segments"])
    rows[0][0] = False
    segments_review.apply_selection(project, rows)
    assert len(segments_review.load_segments(project)) == 2

    assert segments_review.restore_all(project) is True
    assert len(segments_review.load_segments(project)) == 3


def test_restore_all_without_backup(tmp_path):
    assert segments_review.restore_all(str(tmp_path)) is False


def test_rows_include_caption():
    rows = segments_review.rows_from_segments(SAMPLE["segments"])
    assert rows[0][7] == "لن تصدق ما حدث!"
    assert rows[2][7] == ""  # missing caption -> empty


def test_export_publish_metadata(project):
    path, text = segments_review.export_publish_metadata(project)
    assert path is not None
    assert os.path.exists(path)
    assert "Hook A" in text
    assert "لن تصدق ما حدث!" in text
    assert "#viral" in text and "#story" in text
    assert "#motivation" in text  # string hashtags handled
    # file content matches returned text
    assert open(path, encoding="utf-8").read() == text


def test_export_publish_metadata_empty(tmp_path):
    path, text = segments_review.export_publish_metadata(str(tmp_path))
    assert path is None
    assert text == ""
