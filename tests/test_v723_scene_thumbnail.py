# -*- coding: utf-8 -*-
"""Tests for v7.23: scene detection + thumbnail generator."""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import scene_detect, thumbnail_generator

FFMPEG = __import__("shutil").which("ffmpeg")


def _make_video(tmp_path):
    """Two visually different segments (testsrc then blue) in one file."""
    video = tmp_path / "scenes.mp4"
    subprocess.run([
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24:duration=3",
        "-f", "lavfi", "-i", "color=blue:size=320x180:rate=24:duration=3",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", str(video),
    ], check=True, capture_output=True)
    return str(video)


class TestSceneDetect:
    def test_missing_file(self):
        assert scene_detect.find_scene_cuts("nope.mp4") == []

    def test_finds_boundary(self, tmp_path):
        if not FFMPEG:
            pytest.skip("ffmpeg not available")
        video = _make_video(tmp_path)
        scenes = scene_detect.find_scene_cuts(video)
        # there should be a boundary around 3s (testsrc -> blue)
        boundaries = [s for s, e in scenes]
        assert any(2.0 <= b <= 4.0 for b in boundaries), scenes

    def test_snap_to_scene(self):
        scenes = [(0.0, 5.0), (5.0, 10.0), (10.0, 15.0)]
        # start near 5.0 snaps forward, end near 10.0 snaps back
        start, end = scene_detect.snap_to_scene(4.6, 10.4, scenes)
        assert start == 5.0 and end == 10.0

    def test_snap_no_scenes(self):
        assert scene_detect.snap_to_scene(1.0, 2.0, []) == (1.0, 2.0)

    def test_snap_far_boundaries_unchanged(self):
        scenes = [(0.0, 5.0)]
        assert scene_detect.snap_to_scene(7.0, 8.0, scenes) == (7.0, 8.0)


class TestThumbnail:
    def test_requires_pil(self, monkeypatch, tmp_path):
        monkeypatch.setattr(thumbnail_generator, "HAS_PIL", False)
        result = thumbnail_generator.generate_thumbnail(
            str(tmp_path / "x.mp4"), title="t")
        assert result["ok"] is False

    def test_missing_source(self, tmp_path):
        result = thumbnail_generator.generate_thumbnail(
            str(tmp_path / "missing.mp4"), title="t")
        assert result["ok"] is False

    def test_generates_from_video(self, tmp_path):
        if not FFMPEG:
            pytest.skip("ffmpeg not available")
        video = _make_video(tmp_path)
        out = tmp_path / "thumb.png"
        result = thumbnail_generator.generate_thumbnail(
            video, title="عنوان تجريبي", hook="سر!", out=str(out))
        assert result["ok"] is True
        assert os.path.exists(str(out))
        assert os.path.getsize(str(out)) > 1000

    def test_generates_from_image(self, tmp_path):
        from PIL import Image
        img = tmp_path / "src.png"
        Image.new("RGB", (640, 360), (30, 60, 120)).save(str(img))
        out = tmp_path / "thumb2.png"
        result = thumbnail_generator.generate_thumbnail(
            str(img), title="Test Title", out=str(out))
        assert result["ok"] is True
        assert os.path.getsize(str(out)) > 1000

    def test_wrap_text(self):
        from PIL import ImageFont
        font = thumbnail_generator._load_font(30, bold=True, text="abc")
        lines = thumbnail_generator._wrap_text(
            "one two three four five six seven eight nine ten", font, 120)
        assert isinstance(lines, list) and lines
        assert all(len(l) <= 30 for l in lines)

    def test_arabic_font_choice(self):
        ar = thumbnail_generator._load_font(40, bold=True, text="كسب المال")
        en = thumbnail_generator._load_font(40, bold=True, text="money")
        assert ar is not None and en is not None
