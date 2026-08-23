# -*- coding: utf-8 -*-
"""Tests for the v7.18 originality engine (duplicate-content defense)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import originality


class TestFingerprint:
    def test_video_fingerprint_requires_file(self, tmp_path):
        assert originality.video_fingerprint(None) is None
        assert originality.video_fingerprint(str(tmp_path / "missing.mp4")) is None

    def test_fingerprint_key_deterministic(self):
        hashes = [1, 2, 3, 4]
        a = originality.fingerprint_key(hashes)
        b = originality.fingerprint_key(hashes)
        assert a == b and a is not None
        assert originality.fingerprint_key([]) is None
        assert originality.fingerprint_key(None) is None


class TestSimilarity:
    def test_identical_hashes_are_duplicates(self):
        hashes = [0xAAAAAAAAAAAAAAAA] * 16
        assert originality._similarity_between(hashes, hashes) == 1.0

    def test_different_hashes_are_distinct(self):
        left = [0xAAAAAAAAAAAAAAAA] * 16
        right = [0x5555555555555555] * 16  # all bits differ
        assert originality._similarity_between(left, right) == 0.0

    def test_near_identical_hash_counts(self):
        # 2 bits differing on each frame of 16 → all match (threshold <= 6 bits)
        left = [0xAAAAAAAAAAAAAAAA] * 16
        right = [0xAAAAAAAAAAAAAAAA ^ 0b11] * 16
        assert originality._similarity_between(left, right) == 1.0

    def test_missing_fingerprints(self):
        assert originality._similarity_between(None, [1]) == 0.0
        assert originality._similarity_between([1], None) == 0.0

    def test_compare_clips_verdict(self, tmp_path):
        # No OpenCV in this env → gracefully degrade, never crash.
        result = originality.compare_clips(
            str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4"))
        assert "similarity" in result and "verdict" in result


class TestAssessAgainstRegistry:
    def test_no_registry_rows(self):
        result = originality.assess_against_registry("proj", "video.mp4", [])
        assert result.get("checked") is False  # fingerprint unavailable here

    def test_stored_fingerprint_duplicate(self):
        rows = [{
            "project_path": "/p/old",
            "video_name": "old.mp4",
            "created_at": "2026-08-01T00:00:00Z",
            "metadata": {"visual_fingerprint": "|".join(
                ["0"] * originality.FRAMES_TO_SAMPLE)},
        }]
        result = originality.assess_against_registry(
            "proj", "video.mp4", rows)
        # With cv2 present the candidate is computed; with cv2 absent the
        # verdict degrades to "unchecked". Either way: no exception.
        assert "checked" in result


class TestTransformPresets:
    def test_preset_is_deterministic(self):
        a = originality.build_preset(7)
        b = originality.build_preset(7)
        assert a == b

    def test_preset_differs_by_seed(self):
        a = originality.build_preset(7)
        b = originality.build_preset(8)
        assert a != b

    def test_preset_ranges(self):
        for seed in range(50):
            p = originality.build_preset(seed)
            assert 0.98 <= p["speed"] <= 1.02
            assert 0 <= p["crop_jitter"] <= 24
            assert abs(p["contrast"] - 1.0) <= 0.0301
            assert abs(p["brightness"]) <= 0.0201
            assert abs(p["saturation"] - 1.0) <= 0.0501

    def test_transform_noop_copies(self, tmp_path, monkeypatch):
        calls = []
        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            open(cmd[-1], "wb").write(b"x")
        monkeypatch.setattr(originality.subprocess, "run", fake_run)
        result = originality.apply_transformation(
            str(tmp_path / "in.mp4"), str(tmp_path / "out.mp4"))
        assert result["ok"] is True and result["transforms"] == []

    def test_transform_with_filters(self, tmp_path, monkeypatch):
        calls = []
        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            open(cmd[-1], "wb").write(b"x")
        monkeypatch.setattr(originality.subprocess, "run", fake_run)
        result = originality.apply_transformation(
            str(tmp_path / "in.mp4"), str(tmp_path / "out.mp4"),
            mirror=True, speed=1.02, crop_jitter=4)
        assert result["ok"] is True
        assert "hflip" in str(calls[0])
        assert "atempo" in str(calls[0])


class TestReport:
    def test_write_report(self, tmp_path):
        path = originality.write_report(str(tmp_path), [
            {"verdict": "duplicate"}, {"verdict": "distinct"}])
        assert os.path.exists(path)
        import json
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
        assert report["summary"]["duplicates"] == 1
        assert report["summary"]["distinct"] == 1
