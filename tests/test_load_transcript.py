"""load_transcript must read input.tsv/input.srt AND fall back to top-level
transcript artifacts named after the video (local/external files keep their
original basename — v7.27 fix for "Could not parse transcript from TSV or SRT")."""

import os

import pytest

from scripts.create_viral_segments import load_transcript

TSV = "start\tend\ttext\n0\t3120\tمرحبا بكم في هذه الحلقة\n3120\t9840\tنستضيف اليوم المحامي نجيب بيطام\n"
SRT = ("1\n00:00:00,000 --> 00:00:03,120\nمرحبا بكم في هذه الحلقة\n\n"
       "2\n00:00:03,120 --> 00:00:09,840\nنستضيف اليوم المحامي نجيب بيطام\n")


def test_loads_input_tsv(tmp_path):
    (tmp_path / "input.tsv").write_text(TSV, encoding="utf-8")
    segs = load_transcript(str(tmp_path))
    assert len(segs) == 2
    assert segs[0]["start"] == 0.0
    assert segs[0]["end"] == 3.12
    assert "مرحبا" in segs[0]["text"]


def test_falls_back_to_input_srt(tmp_path):
    (tmp_path / "input.srt").write_text(SRT, encoding="utf-8")
    segs = load_transcript(str(tmp_path))
    assert len(segs) == 2
    assert segs[1]["start"] == 3.12


def test_falls_back_to_video_basename_tsv(tmp_path):
    # Local video kept its original basename: artifacts are NOT input.*
    (tmp_path / "مع مصطفى بونيف لقاء خاص.tsv").write_text(TSV, encoding="utf-8")
    segs = load_transcript(str(tmp_path))
    assert len(segs) == 2


def test_falls_back_to_video_basename_srt(tmp_path):
    (tmp_path / "some_video.srt").write_text(SRT, encoding="utf-8")
    segs = load_transcript(str(tmp_path))
    assert len(segs) == 2


def test_raises_when_no_transcript(tmp_path):
    with pytest.raises(ValueError, match="Could not parse transcript"):
        load_transcript(str(tmp_path))


def test_prefers_tsv_over_srt(tmp_path):
    (tmp_path / "input.tsv").write_text(TSV, encoding="utf-8")
    (tmp_path / "input.srt").write_text(SRT.replace("مرحبا", "سليم"), encoding="utf-8")
    segs = load_transcript(str(tmp_path))
    assert "سليم" not in segs[0]["text"]


def test_ignores_input_files_when_scanning_fallback(tmp_path):
    # Broken/empty input.* must not shadow a real video-named transcript.
    (tmp_path / "input.tsv").write_text("start\tend\ttext\n", encoding="utf-8")
    (tmp_path / "مع مصطفى بونيف.tsv").write_text(TSV, encoding="utf-8")
    segs = load_transcript(str(tmp_path))
    assert len(segs) == 2
