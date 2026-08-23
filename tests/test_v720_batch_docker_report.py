# -*- coding: utf-8 -*-
"""Tests for v7.20 additions: batch processor, Docker config, HTML report
extensions, and previously untested modules (audio_analysis, organize_output)."""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import audio_analysis, organize_output


class TestAudioAnalysis:
    def test_bad_fps_returns_none(self, tmp_path):
        assert audio_analysis.get_audio_energy("missing.mp4", fps=0) is None
        assert audio_analysis.get_audio_energy("missing.mp4", fps=-5) is None

    def test_missing_file_returns_none(self):
        # ffmpeg fails -> function must not crash
        result = audio_analysis.get_audio_energy("does_not_exist.mp4", fps=30)
        assert result is None or isinstance(result, (list, tuple))

    def test_energy_shape(self, tmp_path):
        ffmpeg = __import__("shutil").which("ffmpeg")
        if not ffmpeg:
            pytest.skip("ffmpeg not available")
        video = tmp_path / "tone.mp4"
        subprocess.run([
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=1",
            "-c:a", "aac", str(video),
        ], check=True, capture_output=True)
        energies = audio_analysis.get_audio_energy(str(video), fps=10)
        assert energies is not None
        assert len(energies) >= 5
        assert all(0.0 <= e <= 1.0 for e in energies)


class TestOrganizeOutput:
    def test_sanitize_filename(self):
        assert organize_output.sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "abcdefghij"
        assert organize_output.sanitize_filename("  clean  ") == "clean"

    def test_organize_copies_files(self, tmp_path, monkeypatch):
        # Build a fake project: tmp/viral_segments.txt + burned_sub/output000_*.mp4
        (tmp_path / "tmp").mkdir()
        (tmp_path / "burned_sub").mkdir()
        (tmp_path / "tmp" / "viral_segments.txt").write_text(json.dumps({
            "segments": [{"title": "First Clip"}, {"title": "Second Clip"}]
        }), encoding="utf-8")
        (tmp_path / "burned_sub" / "output000_original_scale_subtitled.mp4").write_bytes(b"v0")
        (tmp_path / "burned_sub" / "output001_original_scale_subtitled.mp4").write_bytes(b"v1")

        monkeypatch.chdir(tmp_path)
        organize_output.organize()
        assert (tmp_path / "VIRALS" / "First Clip" / "First Clip.mp4").is_file()
        assert (tmp_path / "VIRALS" / "Second Clip" / "Second Clip.mp4").is_file()
        assert (tmp_path / "VIRALS" / "First Clip" / "First Clip.json").is_file()

    def test_organize_missing_metadata(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        organize_output.organize()  # must not raise


class TestBatchProcessor:
    def test_read_urls(self, tmp_path):
        from scripts import batch_process
        f = tmp_path / "urls.txt"
        f.write_text("# comment\nhttps://youtube.com/watch?v=1\n\nhttps://youtube.com/live/x\n",
                     encoding="utf-8")
        assert batch_process._read_urls(str(f)) == [
            "https://youtube.com/watch?v=1", "https://youtube.com/live/x"]

    def test_build_command_contains_flags(self):
        from scripts import batch_process
        class Args:
            segments = 4
            min_duration = 15
            max_duration = 90
            ai_backend = "manual"
            workflow = 1
            viral = True
            themes = "tech"
            live_wait = 120
            sponsorblock = "sponsor"
            quality = "1080p"
            safety_mode = "block"
            upload = False
            dry_run = False
            privacy = None
            extra = []
        cmd = batch_process.build_command("https://youtube.com/live/abc", Args())
        assert "--url" in cmd and "https://youtube.com/live/abc" in cmd
        assert "--live-wait" in cmd and "120" in cmd
        assert "--sponsorblock" in cmd and "sponsor" in cmd
        assert "--segments" in cmd

    def test_upload_project_no_clips(self, tmp_path):
        from scripts import batch_process
        class Args:
            dry_run = True
            privacy = "private"
        project = tmp_path / "proj"
        project.mkdir()
        result = batch_process.upload_project(str(project), Args())
        assert result["ok"] is False
        assert "no mp4 clips" in result["detail"]


class TestDocker:
    def test_dockerfiles_exist(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        assert os.path.isfile(os.path.join(root, "Dockerfile"))
        assert os.path.isfile(os.path.join(root, "docker-compose.yml"))
        with open(os.path.join(root, "Dockerfile"), encoding="utf-8") as f:
            content = f.read()
        assert "python:3.11" in content
        assert "ffmpeg" in content
        assert "7860" in content
        with open(os.path.join(root, "docker-compose.yml"), encoding="utf-8") as f:
            assert "7860:7860" in f.read()


class TestProjectReportHTML:
    def test_html_has_new_sections(self, tmp_path):
        from scripts import project_report as pr
        report = {
            "project": {"path": str(tmp_path), "name": "p", "status": "ok"},
            "readiness": {"ready_for_publish": True, "errors": []},
            "safety": {"total": 1, "counts": {"blocked": 0, "manual_review": 0}},
            "risk": {"blocked": 0},
            "media": {"count": 1, "valid": 1, "invalid": 0, "files": []},
            "content_guard": {"blocked": 0, "kept": 1,
                              "channel": {"locked": False, "count": 0}},
            "publishing": {"history": {}, "last_batch": {}},
            "tracking": {"present": True, "backend": "insightface",
                         "requested_active_speaker": True,
                         "active_speaker_applied": True,
                         "smoothing": 0.55, "headroom": 0.12},
        }
        html = pr.render_html(report)
        assert "التتبع" in html
        assert "insightface" in html
        assert "حماية المحتوى المكرر" in html
        assert "النشر" in html
