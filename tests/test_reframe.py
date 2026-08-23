"""Tests for the output reframe stage (scripts/reframe.py)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import reframe


def test_resolve_aspect():
    assert reframe.resolve_aspect("9:16") == (1080, 1920)
    assert reframe.resolve_aspect("4:5") == (1080, 1350)
    assert reframe.resolve_aspect("1:1") == (1080, 1080)
    assert reframe.resolve_aspect("16:9") == (1920, 1080)
    assert reframe.resolve_aspect(None) is None
    assert reframe.resolve_aspect("bogus") is None


def test_default_mode_by_aspect():
    assert reframe.DEFAULT_MODE.get("16:9") == "pad"
    assert "4:5" not in reframe.DEFAULT_MODE  # defaults to crop


def test_build_filter_crop():
    vf = reframe.build_ffmpeg_filter((1080, 1350), "crop")
    assert "scale=1080:1350:force_original_aspect_ratio=increase" in vf
    assert "crop=1080:1350" in vf


def test_build_filter_pad():
    vf = reframe.build_ffmpeg_filter((1920, 1080), "pad")
    assert "boxblur" in vf
    assert "overlay=(W-w)/2:(H-h)/2" in vf


def test_find_subtitled_clips(tmp_path):
    final = tmp_path / "final"
    final.mkdir()
    (final / "output000_original_scale_subtitled.mp4").write_text("x")
    (final / "output000_original_scale.mp4").write_text("x")  # not subtitled
    polished = tmp_path / "final_polished"
    polished.mkdir()
    (polished / "output001_original_scale_subtitled.mp4").write_text("x")
    clips = reframe.find_subtitled_clips(str(tmp_path))
    assert len(clips) == 2
    assert all("subtitled" in os.path.basename(c) for c in clips)


def test_reframe_file_dry_run_returns_command(tmp_path):
    clip = tmp_path / "x_subtitled.mp4"
    clip.write_text("fake")
    r = reframe.reframe_file(str(clip), (1080, 1350), "crop", dry_run=True)
    assert r["ok"] and r["dry_run"]
    assert any("scale=1080:1350" in c for c in r["cmd"])
    # dry run must not touch the file
    assert os.path.exists(clip)


def test_reframe_file_missing_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(reframe.shutil, "which", lambda name: None)
    clip = tmp_path / "x_subtitled.mp4"
    clip.write_text("fake")
    r = reframe.reframe_file(str(clip), (1080, 1350), "crop")
    assert not r["ok"]
    assert "ffmpeg not found" in r["error"]


def test_reframe_file_failed_ffmpeg(tmp_path, monkeypatch):
    class Proc:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(reframe.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(reframe.subprocess, "run", lambda *a, **k: Proc())
    clip = tmp_path / "x_subtitled.mp4"
    clip.write_text("fake")
    r = reframe.reframe_file(str(clip), (1080, 1350), "crop")
    assert not r["ok"]
    assert "boom" in r["error"]
    assert os.path.exists(clip)  # original untouched on failure


def test_reframe_project_unknown_aspect(tmp_path):
    with pytest.raises(ValueError):
        reframe.reframe_project(str(tmp_path), "bogus")


def test_reframe_project_no_clips(tmp_path):
    results = reframe.reframe_project(str(tmp_path), "4:5", dry_run=True)
    assert results == []


def test_reframe_project_dry_run_all(tmp_path):
    final = tmp_path / "final"
    final.mkdir()
    (final / "a_subtitled.mp4").write_text("x")
    (final / "b_subtitled.mp4").write_text("x")
    results = reframe.reframe_project(str(tmp_path), "4:5", dry_run=True)
    assert len(results) == 2
    assert all(r["ok"] and r["dry_run"] for r in results)
