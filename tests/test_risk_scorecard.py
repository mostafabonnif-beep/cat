# -*- coding: utf-8 -*-
"""Tests for the per-clip YouTube risk scorecard (reuse/monetization/visual)."""

import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import risk_scorecard as rs

FFMPEG = shutil.which("ffmpeg")


def _make_video(path, seconds=4, pattern="testsrc", size="160x160"):
    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "{}=size={}:duration={}".format(pattern, size, seconds),
        "-pix_fmt", "yuv420p", "-c:v", "libx264",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


# ---------------------------------------------------------------------------
# dHash / frame similarity
# ---------------------------------------------------------------------------

class TestFrameSimilarity:
    @pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")
    def test_identical_files_high_similarity(self, tmp_path):
        a = tmp_path / "a.mp4"
        b = tmp_path / "b.mp4"
        _make_video(a)
        _make_video(b)
        sim = rs.frame_similarity(str(a), str(b), [0.5])
        assert sim is not None and sim >= 90.0

    @pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")
    def test_different_content_low_similarity(self, tmp_path):
        a = tmp_path / "a.mp4"
        b = tmp_path / "b.mp4"
        _make_video(a, pattern="testsrc")
        _make_video(b, pattern="smptebars")
        sim = rs.frame_similarity(str(a), str(b), [0.5])
        assert sim is not None and sim <= 60.0

    def test_missing_file_returns_none(self, tmp_path):
        assert rs.frame_similarity(str(tmp_path / "nope.mp4"),
                                   str(tmp_path / "nope2.mp4"), [0.5]) is None

    def test_independent_timestamps_compare_clip_to_source_window(self, monkeypatch):
        calls = []

        def fake_grab(path, at_seconds, width=9, height=8):
            calls.append((path, at_seconds))
            return [0] * (width * height)

        monkeypatch.setattr(rs, "_grab_gray_frame", fake_grab)
        score = rs.frame_similarity("clip.mp4", "source.mp4", [0.5], [42.5])
        assert score == 100.0
        assert calls == [("clip.mp4", 0.5), ("source.mp4", 42.5)]


class TestLetterbox:
    @pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")
    def test_pillarboxed_video_detected(self, tmp_path):
        # 160x160 content centered inside a 320x160 canvas → side bars
        v = tmp_path / "pb.mp4"
        cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
               "-f", "lavfi", "-i", "testsrc=size=160x160:duration=3",
               "-vf", "pad=320:160:80:0:black", "-pix_fmt", "yuv420p",
               "-c:v", "libx264", str(v)]
        subprocess.run(cmd, check=True, capture_output=True)
        assert rs._letterbox_ratio(str(v), 1.5) >= 0.3

    @pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")
    def test_fullframe_video_clean(self, tmp_path):
        v = tmp_path / "ff.mp4"
        _make_video(v, pattern="testsrc")
        assert rs._letterbox_ratio(str(v), 1.5) < 0.3


# ---------------------------------------------------------------------------
# Text signals
# ---------------------------------------------------------------------------

class TestProfanityWindow:
    def test_profanity_inside_first7(self):
        segment = {"start_time": 10.0, "end_time": 20.0}
        words = [
            {"word": "مرحبا", "start": 10.0, "end": 10.4},
            {"word": "يا", "start": 11.0, "end": 11.2},
            {"word": "كلب", "start": 11.2, "end": 11.8},   # medium harassment
            {"word": "متابعة", "start": 18.0, "end": 18.5},
        ]
        any_off, profanity = rs.profanity_in_first_seconds(segment, words)
        assert any_off is True
        assert any(m["term"] == "يا كلب" for m in profanity)

    def test_offense_after_window_ignored(self):
        segment = {"start_time": 0.0, "end_time": 20.0}
        words = [
            {"word": "مقدمة", "start": 0.0, "end": 0.5},
            {"word": "اذبحهم", "start": 12.0, "end": 12.5},  # outside 7s
        ]
        any_off, profanity = rs.profanity_in_first_seconds(segment, words)
        assert any_off is False and profanity == []

    def test_clean_text(self):
        segment = {"start_time": 0.0, "end_time": 10.0}
        words = [{"word": "طبخ", "start": 0.0, "end": 0.5},
                 {"word": "سفر", "start": 1.0, "end": 1.5}]
        assert rs.profanity_in_first_seconds(segment, words) == (False, [])


# ---------------------------------------------------------------------------
# Segment scoring
# ---------------------------------------------------------------------------

class TestScoreSegment:
    def test_no_videos_reuse_zero_text_scored(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        segment = {"title": "x", "start_time": 0.0, "end_time": 10.0}
        words = [{"word": "اذبحهم", "start": 1.0, "end": 1.5}]
        entry = rs.score_segment(segment, 0, str(project), words, None)
        assert entry["axes"]["reuse"]["score"] == 0
        assert entry["axes"]["text"]["first7s"] == 80  # violence in first 7s
        assert entry["overall"] in ("high", "danger")

    def test_clean_segment_low(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        segment = {"title": "نظيف", "start_time": 0.0, "end_time": 30.0}
        entry = rs.score_segment(segment, 0, str(project), [], None)
        assert entry["overall"] == "low"

    def test_semantic_review_is_publish_risk(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        segment = {"title": "سياق حساس", "start_time": 0.0, "end_time": 30.0}
        safety_entry = {
            "index": 0,
            "status": "manual_review",
            "semantic": {"action": "review", "category": "context_required"},
        }
        entry = rs.score_segment(segment, 0, str(project), [], None, safety_entry=safety_entry)
        assert entry["axes"]["text"]["semantic_policy"] == 75
        assert entry["axes"]["text"]["hate_speech"] == 75
        assert entry["overall"] == "high"


# ---------------------------------------------------------------------------
# Project analysis + gate
# ---------------------------------------------------------------------------

class TestAnalyzeProject:
    def _project(self, tmp_path, segments):
        project = tmp_path / "proj"
        project.mkdir()
        (project / "viral_segments.txt").write_text(
            json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8")
        return str(project)

    def test_graceful_without_videos(self, tmp_path):
        project = self._project(tmp_path, [
            {"title": "نظيف", "start_time": 0.0, "end_time": 30.0}])
        report = rs.analyze_project(project)
        assert report["summary"]["total"] == 1
        assert report["summary"]["low"] == 1
        assert os.path.exists(os.path.join(project, rs.SCORECARD_FILENAME))

    @pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")
    def test_reused_clip_gets_blocked(self, tmp_path):
        # build a project: source video + a "cut" video that is a copy of the
        # same source window → high similarity → blocked by the gate
        project = tmp_path / "proj"
        (project / "final").mkdir(parents=True)
        source = project / "input.mp4"
        _make_video(source, seconds=4)
        (project / "final" / "000_test.mp4").write_bytes(source.read_bytes())

        segments = [{"title": "منسوخ", "start_time": 0.0, "end_time": 4.0}]
        (project / "viral_segments.txt").write_text(
            json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8")

        report = rs.analyze_project(str(project), gate_threshold=70.0)
        entry = report["segments"][0]
        assert entry["axes"]["reuse"]["score"] >= 70.0
        assert len(report["blocked"]) == 1
        # the gate blacklist file was written
        blacklist = json.loads((project / rs.PUBLISH_BLOCKLIST_FILENAME).read_text(encoding="utf-8"))
        assert blacklist["blocked"][0]["title"] == "منسوخ"

    def test_gate_threshold_logic(self):
        # pure-logic check via analyze_project on entries without videos
        # (reuse stays 0, but a first-7s violence word pushes overall high)
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            project = os.path.join(d, "p")
            os.makedirs(project)
            segments = [{"title": "خطير", "start_time": 0.0, "end_time": 10.0}]
            with open(os.path.join(project, "viral_segments.txt"), "w", encoding="utf-8") as f:
                json.dump({"segments": segments}, f, ensure_ascii=False)
            with open(os.path.join(project, "input.json"), "w", encoding="utf-8") as f:
                json.dump({"segments": [{"start": 0, "end": 10, "words": [
                    {"word": "اذبحهم", "start": 2.0, "end": 2.5}]}]}, f, ensure_ascii=False)
            report = rs.analyze_project(project)
            assert len(report["blocked"]) == 1


def test_build_scorecard_html_shape():
    """v6.16: readable HTML report (functional completeness)."""
    report = {
        "summary": {"total": 2, "low": 1, "medium": 0, "high": 1, "danger": 0,
                    "blocked_for_publish": 1},
        "segments": [
            {"index": 0, "title": "كلب لطيف", "overall": "low", "overall_score": 12.0,
             "axes": {"text": {"first7s": {"score": 5}}, "reuse": {"similarity": 0.1},
                      "visual": {"score": 0}}},
            {"index": 1, "title": "مقطع خطير", "overall": "high", "overall_score": 78.0,
             "axes": {"text": {"first7s": {"score": 65}}, "reuse": {"similarity": 0.85},
                      "visual": {"score": 0}}},
        ],
        "blocked": [
            {"index": 1, "title": "مقطع خطير", "overall": "high",
             "axes": {"text": {"first7s": {"score": 65}}, "reuse": {"similarity": 0.85},
                      "visual": {"score": 0}}},
        ],
    }
    html = rs.build_scorecard_html(report)
    assert "مقطع خطير" in html
    assert "منخفض" in html and "مرتفع" in html
    assert "⛔" in html
    assert "ممنوع النشر" in html
    assert "85%" in html  # reuse similarity formatted


def test_render_html_report_writes_file(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    with open(project / rs.SCORECARD_FILENAME, "w", encoding="utf-8") as fh:
        json.dump({"summary": {}, "segments": [], "blocked": []}, fh)
    out = rs.render_html_report(str(project))
    assert out and os.path.exists(out)
    content = open(out, encoding="utf-8").read()
    assert "<html" in content and "تقرير" in content


def test_render_html_report_missing(tmp_path):
    assert rs.render_html_report(str(tmp_path)) is None
