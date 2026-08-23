# -*- coding: utf-8 -*-
"""Tests for the visual ONNX classifier hook (Roadmap 2.1)."""

import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import visual_check as vc

FFMPEG = shutil.which("ffmpeg")


def _make_video(path, seconds=3):
    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=320x240:duration={}".format(seconds),
        "-pix_fmt", "yuv420p", "-c:v", "libx264", str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


class TestExtractFrames:
    @pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")
    def test_extracts_frames(self, tmp_path):
        video = tmp_path / "clip.mp4"
        _make_video(video)
        frames = vc.extract_frames(str(video), num_frames=4, work_dir=str(tmp_path))
        assert 1 <= len(frames) <= 4
        for f in frames:
            assert os.path.exists(f) and os.path.getsize(f) > 0

    def test_missing_video_returns_empty(self, tmp_path):
        assert vc.extract_frames(str(tmp_path / "nope.mp4")) == []

    def test_zero_frames_returns_empty(self, tmp_path):
        assert vc.extract_frames(str(tmp_path / "nope.mp4"), num_frames=0) == []


class TestClassifierFallback:
    def test_missing_model_unavailable(self, tmp_path):
        clf = vc.NudeNetClassifier(str(tmp_path / "missing.onnx"))
        assert clf.available is False
        assert "not found" in clf.error

    def test_analyze_video_without_model_is_safe(self, tmp_path):
        clf = vc.NudeNetClassifier(str(tmp_path / "missing.onnx"))
        report = clf.analyze_video(str(tmp_path / "whatever.mp4"))
        assert report["available"] is False
        assert report["graphic_score"] is None
        assert report["graphic"] is False

    def test_make_classifier_returns_none_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vc, "default_model_path",
                            lambda base_dir=None: str(tmp_path / "absent.onnx"))
        assert vc.make_classifier() is None


class FakeSession:
    """Minimal stand-in for onnxruntime InferenceSession."""

    def __init__(self, probs_by_frame=None):
        self._probs = probs_by_frame or [0.9]
        self.calls = 0

    def get_inputs(self):
        class Inp:
            name = "input"
        return [Inp()]

    def run(self, *_args, **_kwargs):
        import numpy as np
        idx = min(self.calls, len(self._probs) - 1)
        self.calls += 1
        p = self._probs[idx]
        return [np.asarray([p], dtype=np.float32)]


class TestClassifierInference:
    @pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")
    def test_graphic_content_detected(self, tmp_path, monkeypatch):
        video = tmp_path / "clip.mp4"
        _make_video(video)
        model = tmp_path / "fake.onnx"
        model.write_bytes(b"fake")

        import numpy as np
        monkeypatch.setattr("numpy.exp", np.exp)  # noqa: F401 (softmax path uses np.exp)

        fake = FakeSession(probs_by_frame=[
            [0.01, 0.85, 0.03, 0.08, 0.03],  # hentai 0.85 → graphic
        ])

        clf = vc.NudeNetClassifier(str(model))
        # inject the fake session (simulates onnxruntime being available)
        clf._session = fake
        report = clf.analyze_video(str(video), num_frames=2, work_dir=str(tmp_path))
        assert report["available"] is True
        assert report["graphic_score"] is not None
        assert report["graphic_score"] >= 70.0
        assert report["graphic"] is True
        assert report["top_class"] == "hentai"

    @pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")
    def test_neutral_content_low_score(self, tmp_path, monkeypatch):
        video = tmp_path / "clip.mp4"
        _make_video(video)
        model = tmp_path / "fake2.onnx"
        model.write_bytes(b"fake")
        fake = FakeSession(probs_by_frame=[
            [0.02, 0.01, 0.92, 0.03, 0.02],  # neutral 0.92
        ])
        clf = vc.NudeNetClassifier(str(model))
        clf._session = fake
        report = clf.analyze_video(str(video), num_frames=2, work_dir=str(tmp_path))
        assert report["graphic"] is False
        assert report["graphic_score"] <= 10.0

    def test_sidecar_meta_overrides(self, tmp_path):
        model = tmp_path / "custom.onnx"
        model.write_bytes(b"x")
        meta = tmp_path / "custom.onnx.json"
        meta.write_text(json.dumps({"input_size": 224, "classes": ["a", "b"],
                                    "graphic": ["b"]}), encoding="utf-8")
        clf = vc.NudeNetClassifier(str(model))
        assert clf.input_size == 224
        assert clf.classes == ["a", "b"]
        assert clf.graphic_classes == ["b"]


class TestDownload:
    def test_download_skips_existing(self, tmp_path, monkeypatch):
        model = tmp_path / "model.onnx"
        model.write_bytes(b"present")
        def fake_urlopen(*a, **k):
            raise AssertionError("should not download when file exists")
        monkeypatch.setattr(vc.urllib.request, "urlopen", fake_urlopen)
        assert vc.download_model(str(model)) == str(model)


class TestScorecardIntegration:
    def test_score_segment_accepts_classifier(self, tmp_path):
        from scripts import risk_scorecard as rs
        segment = {"index": 0, "title": "T", "start_time": 0, "end_time": 5}
        entry = rs.score_segment(segment, 0, str(tmp_path), words=[],
                                 source_video=None, visual_classifier=None)
        assert entry["axes"]["visual"]["score"] == 0
        assert entry["axes"]["visual"]["model"] is None
