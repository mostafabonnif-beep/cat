# -*- coding: utf-8 -*-
"""Tests for the bleep/censor engine (word-level muting + subtitle masking)."""

import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import censor_engine as ce

FFMPEG = shutil.which("ffmpeg")


# ---------------------------------------------------------------------------
# Word-level span computation
# ---------------------------------------------------------------------------

WORDS = [
    {"word": "مرحبا", "start": 0.0, "end": 0.5},
    {"word": "بكم", "start": 0.5, "end": 1.0},
    {"word": "هؤلاء", "start": 1.0, "end": 1.5},
    {"word": "القردة", "start": 1.5, "end": 2.0},
    {"word": "والخنازير", "start": 2.0, "end": 2.5},
    {"word": "يجب", "start": 2.5, "end": 3.0},
    {"word": "اذبحهم", "start": 3.0, "end": 3.5},
    {"word": "جميعا", "start": 3.5, "end": 4.0},
]

SEGMENT = {"title": "test", "start_time": 0.0, "end_time": 4.0}


class TestComputeSpans:
    def test_finds_offending_words(self):
        spans = ce.compute_censor_spans(SEGMENT, WORDS)
        assert spans
        # القردة (1.5-2.0) + والخنازير (2.0-2.5) merge into one padded span
        # اذبحهم (3.0-3.5) is its own span
        assert len(spans) == 2
        # padding applied
        assert spans[0]["start"] == pytest.approx(1.5 - ce.PAD_SECONDS, abs=1e-6)
        assert spans[1]["start"] == pytest.approx(3.0 - ce.PAD_SECONDS, abs=1e-6)
        terms = " ".join(s["term"] for s in spans)
        assert "قردة" in terms and "اذبحهم" in terms

    def test_respects_segment_window(self):
        seg = {"start_time": 2.6, "end_time": 4.0}
        spans = ce.compute_censor_spans(seg, WORDS)
        assert len(spans) == 1  # only اذبحهم inside window
        assert "اذبحهم" in spans[0]["term"]

    def test_min_severity_high_skips_medium(self):
        words = [{"word": "يا", "start": 0.0, "end": 0.3},
                 {"word": "كلب", "start": 0.3, "end": 0.8}]  # medium harassment
        seg = {"start_time": 0.0, "end_time": 1.0}
        assert ce.compute_censor_spans(seg, words, min_severity="medium")
        assert not ce.compute_censor_spans(seg, words, min_severity="high")

    def test_allowlist_excludes(self):
        spans = ce.compute_censor_spans(SEGMENT, WORDS, allow_terms=["اذبحهم"])
        assert all("اذبحهم" not in s["term"] for s in spans)

    def test_empty_words(self):
        assert ce.compute_censor_spans(SEGMENT, []) == []

    def test_clean_words(self):
        clean = [{"word": "طبخ", "start": 0.0, "end": 0.5},
                 {"word": "وسفر", "start": 0.5, "end": 1.0}]
        assert ce.compute_censor_spans(SEGMENT, clean) == []


class TestRelativeSpans:
    def test_relative_conversion(self):
        spans = [{"start": 12.0, "end": 13.0, "term": "x"}]
        rel = ce.spans_to_relative(spans, 10.0, 20.0)
        assert rel[0]["start"] == 2.0 and rel[0]["end"] == 3.0

    def test_clamped_to_zero(self):
        spans = [{"start": 9.0, "end": 11.0, "term": "x"}]
        rel = ce.spans_to_relative(spans, 10.0)
        assert rel[0]["start"] == 0.0

    def test_clamped_to_duration(self):
        spans = [{"start": 18.0, "end": 25.0, "term": "x"}]
        rel = ce.spans_to_relative(spans, 10.0, 20.0)
        assert rel[0]["end"] == 10.0


# ---------------------------------------------------------------------------
# Subtitle masking
# ---------------------------------------------------------------------------

class TestMaskSubtitles:
    def test_masks_overlapping_words(self, tmp_path):
        subs = {"segments": [{
            "start": 0, "end": 4, "text": "مرحبا هؤلاء القردة",
            "words": [
                {"word": "مرحبا", "start": 0.0, "end": 0.5},
                {"word": "القردة", "start": 1.5, "end": 2.0},
                {"word": "بكم", "start": 2.5, "end": 3.0},
            ],
        }]}
        path = tmp_path / "sub.json"
        path.write_text(json.dumps(subs, ensure_ascii=False), encoding="utf-8")
        spans = [{"start": 1.42, "end": 2.08, "term": "قردة"}]
        n = ce.mask_subtitle_json(str(path), spans)
        assert n == 1
        data = json.loads(path.read_text(encoding="utf-8"))
        words = data["segments"][0]["words"]
        assert words[1]["word"] == ce.MASK_TEXT
        assert words[0]["word"] == "مرحبا" and words[2]["word"] == "بكم"

    def test_missing_file(self):
        assert ce.mask_subtitle_json("/nonexistent.json", [{"start": 0, "end": 1}]) == 0


# ---------------------------------------------------------------------------
# ffmpeg audio muting (real end-to-end, skipped without ffmpeg)
# ---------------------------------------------------------------------------

def _mean_volume(path, start, end):
    """Return mean_volume in dB for a window of the file's audio."""
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "info",
        "-ss", str(start), "-to", str(end), "-i", path,
        "-af", "volumedetect", "-f", "null", "-",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True)
    for line in (out.stderr or "").splitlines():
        if "mean_volume" in line:
            return float(line.split("mean_volume:")[1].strip().split()[0])
    return None


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")
class TestAudioCensor:
    def _make_video(self, path, seconds=6):
        # 6s of loud sine wave with a tiny black video track
        cmd = [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration={}".format(seconds),
            "-f", "lavfi", "-i", "color=black:size=64x64:duration={}".format(seconds),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)

    def test_mute_span_silences_audio(self, tmp_path):
        video = tmp_path / "clip.mp4"
        self._make_video(video)
        spans = [{"start": 2.0, "end": 4.0, "term": "x"}]
        assert ce.apply_audio_censor(str(video), spans)
        # muted window should be (near) silence, outside should be loud
        muted_db = _mean_volume(str(video), 2.3, 3.7)
        loud_db = _mean_volume(str(video), 0.2, 1.5)
        assert muted_db is not None and loud_db is not None
        assert muted_db < -60.0, f"muted window not silent: {muted_db} dB"
        assert loud_db > -40.0, f"unmuted window lost audio: {loud_db} dB"

    def test_filter_string(self):
        spans = [{"start": 1.0, "end": 2.0}, {"start": 5.5, "end": 6.0}]
        af = ce.build_mute_filter(spans)
        assert af == "volume=0:enable='between(t,1.000,2.000)+between(t,5.500,6.000)'"

    def test_no_spans_noop(self, tmp_path):
        video = tmp_path / "clip.mp4"
        self._make_video(video)
        assert ce.apply_audio_censor(str(video), []) is False


# ---------------------------------------------------------------------------
# Project orchestration
# ---------------------------------------------------------------------------

class TestCensorProject:
    def _make_project(self, tmp_path):
        project = tmp_path / "proj"
        (project / "cuts").mkdir(parents=True)
        (project / "subs").mkdir()
        # word-level transcript
        input_json = {"segments": [{"start": 0, "end": 4, "text": "x", "words": WORDS}]}
        (project / "input.json").write_text(
            json.dumps(input_json, ensure_ascii=False), encoding="utf-8")
        # fake cut video + subtitle json for segment 0
        (project / "cuts" / "000_test_original_scale.mp4").write_bytes(b"fake")
        subs = {"segments": [{"start": 0, "end": 4, "text": "x", "words": [
            {"word": w["word"], "start": w["start"], "end": w["end"]} for w in WORDS]}]}
        (project / "subs" / "000_test_processed.json").write_text(
            json.dumps(subs, ensure_ascii=False), encoding="utf-8")
        return str(project)

    def test_end_to_end_map_and_masking(self, tmp_path):
        project = self._make_project(tmp_path)
        viral = {"segments": [dict(SEGMENT)]}
        cmap = ce.censor_project(project, viral)
        assert cmap["total_muted_words"] == 2
        entry = cmap["segments"]["0"]
        assert entry["muted_words"] == 2
        # ffmpeg fails on the fake video but masking must still happen
        assert entry["subtitle_masked"] >= 2
        # map file written
        assert os.path.exists(os.path.join(project, "censor_map.json"))
        # subtitle words actually masked
        data = json.loads((tmp_path / "proj" / "subs" / "000_test_processed.json")
                          .read_text(encoding="utf-8"))
        masked = [w["word"] for w in data["segments"][0]["words"] if w["word"] == ce.MASK_TEXT]
        assert len(masked) >= 2

    def test_no_word_transcript(self, tmp_path):
        project = tmp_path / "empty"
        project.mkdir()
        cmap = ce.censor_project(str(project), {"segments": [dict(SEGMENT)]})
        assert cmap.get("error") == "no_word_transcript"
