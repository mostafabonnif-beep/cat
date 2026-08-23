"""Tests for JSON segment cutting logic (timestamps clamp + clipping)."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.cut_json import _clamp_time, cut_json_transcript, process_segments


@pytest.mark.parametrize(
    "value,expected",
    [
        ("5", 5.0),
        (5, 5.0),
        (5.7, 5.7),
        (-3, 0.0),
        ("abc", 0.0),
        (None, 0.0),
        ("", 0.0),
    ],
)
def test_clamp_time(value, expected):
    assert _clamp_time(value) == expected


SAMPLE_DATA = {
    "segments": [
        {"start": 0.0, "end": 10.0, "text": "intro"},
        {"start": 10.0, "end": 20.0, "text": "middle", "words": [
            {"start": 12.0, "end": 13.0, "word": "a"},
            {"start": 14.5, "end": 15.5, "word": "b"},
            {"start": 18.0, "end": 19.0, "word": "c"},
        ]},
        {"start": 50.0, "end": 60.0, "text": "later"},
    ]
}


def test_process_segments_keeps_overlapping_and_retimes():
    result = process_segments(SAMPLE_DATA, start_time=5, end_time=15)
    assert len(result["segments"]) == 2

    seg0 = result["segments"][0]
    assert seg0["start"] == 0.0
    assert seg0["end"] == 5.0  # clipped at end_time, shifted

    seg1 = result["segments"][1]
    assert seg1["start"] == 5.0  # 10 - 5
    assert seg1["end"] == 10.0   # 15 - 5
    # Only words inside [5, 15] survive, re-timed
    assert [w["word"] for w in seg1["words"]] == ["a", "b"]
    assert seg1["words"][0]["start"] == 7.0
    assert seg1["words"][0]["end"] == 8.0


def test_process_segments_drops_out_of_range():
    result = process_segments(SAMPLE_DATA, start_time=0, end_time=25)
    ids = [s["text"] for s in result["segments"]]
    assert "later" not in ids
    assert len(result["segments"]) == 2


def test_process_segments_full_window():
    result = process_segments(SAMPLE_DATA, start_time=0, end_time=100)
    assert len(result["segments"]) == 3
    assert result["segments"][2]["start"] == 50.0


def test_process_segments_inverted_times():
    # start > end must not crash; end is clamped up to start
    result = process_segments(SAMPLE_DATA, start_time=20, end_time=5)
    assert result["segments"] == []


def test_cut_json_transcript_writes_file(tmp_path):
    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.json"
    input_path.write_text(json.dumps(SAMPLE_DATA), encoding="utf-8")

    cut_json_transcript(str(input_path), str(output_path), 5, 15)

    assert output_path.exists()
    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(data["segments"]) == 2


def test_cut_json_transcript_missing_input_does_not_crash(tmp_path, capsys):
    output_path = tmp_path / "out.json"
    cut_json_transcript(str(tmp_path / "missing.json"), str(output_path), 0, 10)
    assert not output_path.exists()
