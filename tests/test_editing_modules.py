# -*- coding: utf-8 -*-
"""Tests for Sprint-3 editing modules: jump cuts, punch zoom, music, branding, polish."""

import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import background_music, branding, jump_cuts, polish, punch_zoom

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _probe_duration(path):
    if not os.path.exists(path):
        return None
    res = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True)
    return float(res.stdout.strip())


def _make_video_with_silence(path, tone_segments=((0, 1.2), (1.2, 2.6), (2.6, 4.0)),
                             silence_window=(1.2, 2.6)):
    """Video with tone during tone windows and digital silence in between."""
    # audio: tone 0-1.2, silence 1.2-2.6, tone 2.6-4.0
    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=4",
        "-filter_complex",
        "[1:a]volume=0:enable='between(t,1.2,2.6)',volume=0.6:enable='not(between(t,1.2,2.6))'[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
        "-shortest", str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)


def _make_audio(path, duration=3.0, freq=880):
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency={}:sample_rate=44100:duration={}".format(freq, duration),
         "-c:a", "aac", str(path)],
        check=True, capture_output=True, timeout=60)


def _make_logo(path):
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=red:size=100x100,format=rgba",
         "-frames:v", "1", str(path)],
        check=True, capture_output=True, timeout=60)


SAMPLE_WORDS = [
    {"word": "hello", "start": 0.1, "end": 0.5},
    {"word": "um", "start": 0.7, "end": 1.0},
    {"word": "uh", "start": 1.0, "end": 1.2},
    {"word": "world", "start": 1.4, "end": 1.8},
    {"word": "wow", "start": 2.2, "end": 2.5},
    {"word": "okay", "start": 2.9, "end": 3.2},
]


# ---------------------------------------------------------------------------
# jump_cuts — pure logic
# ---------------------------------------------------------------------------

class TestJumpCutsLogic:
    def test_filler_spans_merged(self):
        spans = jump_cuts.find_filler_spans(SAMPLE_WORDS)
        assert any(abs(s - 0.7) < 0.01 and abs(e - 1.2) < 0.01 for s, e in spans)

    def test_plan_cuts_merges_close(self):
        cuts = jump_cuts.plan_cuts([(0.5, 1.0, 0.5)], [(1.05, 1.5)],
                                   duration=5.0, min_keep=0.35)
        assert len(cuts) == 1
        assert abs(cuts[0][0] - 0.5) < 0.01

    def test_plan_cuts_drops_short_silence(self):
        cuts = jump_cuts.plan_cuts([(0.5, 0.7, 0.2)], [], duration=5.0, min_silence=0.4)
        assert cuts == []

    def test_plan_cuts_clips_to_duration(self):
        cuts = jump_cuts.plan_cuts([(0.5, 10.0, 9.5)], [], duration=5.0, max_cut=4.0)
        assert cuts[0][1] == pytest.approx(4.5, abs=0.01)  # 0.5 + 4.0

    def test_load_words_no_file(self, tmp_path):
        assert jump_cuts.load_words(str(tmp_path / "nope.json")) == []


# ---------------------------------------------------------------------------
# jump_cuts — ffmpeg integration
# ---------------------------------------------------------------------------

class TestJumpCutsVideo:
    @pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="ffmpeg/ffprobe required")
    def test_silence_detected(self, tmp_path):
        video = tmp_path / "clip.mp4"
        _make_video_with_silence(video)
        silences = jump_cuts.detect_silences(str(video), threshold_db=-30, min_duration=0.4)
        assert any(e - s >= 0.9 for s, e, _ in silences)

    @pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="ffmpeg/ffprobe required")
    def test_apply_reduces_duration(self, tmp_path):
        video = tmp_path / "clip.mp4"
        _make_video_with_silence(video)
        before = _probe_duration(str(video))
        out = tmp_path / "out.mp4"
        removed = jump_cuts.apply_jump_cuts(str(video), str(out), [(1.2, 2.6)])
        after = _probe_duration(str(out))
        assert removed == pytest.approx(1.4, abs=0.01)
        assert after < before - 1.0

    @pytest.mark.skipif(not FFMPEG, reason="ffmpeg required")
    def test_no_cuts_copies(self, tmp_path):
        video = tmp_path / "clip.mp4"
        _make_video_with_silence(video)
        out = tmp_path / "out.mp4"
        removed = jump_cuts.apply_jump_cuts(str(video), str(out), [])
        assert removed == 0.0
        assert os.path.exists(out)


# ---------------------------------------------------------------------------
# punch_zoom — logic + integration
# ---------------------------------------------------------------------------

class TestPunchZoom:
    def test_plan_keyword_punch(self):
        punches = punch_zoom.plan_punches(SAMPLE_WORDS, keywords=["world"], emotional=False)
        assert any(p[0] <= 1.4 and p[1] >= 1.8 for p in punches)

    def test_plan_emotional_word(self):
        punches = punch_zoom.plan_punches(SAMPLE_WORDS, emotional=True, hook=False)
        assert any(p[0] <= 2.2 for p in punches)

    def test_plan_hook_always_first(self):
        punches = punch_zoom.plan_punches(SAMPLE_WORDS, hook=True, emotional=False)
        assert punches[0][0] <= 0.3

    def test_plan_auto_interval(self):
        punches = punch_zoom.plan_punches([], hook=False, emotional=False,
                                          auto_interval=1.0, duration=4.0)
        assert len(punches) >= 2

    @pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="ffmpeg required")
    def test_apply_zoom_produces_video(self, tmp_path):
        video = tmp_path / "clip.mp4"
        _make_video_with_silence(video)
        out = tmp_path / "zoomed.mp4"
        n = punch_zoom.apply_punch_zoom(str(video), str(out), [(0.5, 1.0)])
        assert n == 1
        assert os.path.exists(out)
        assert _probe_duration(str(out)) > 3.0


# ---------------------------------------------------------------------------
# background_music
# ---------------------------------------------------------------------------

class TestBackgroundMusic:
    def test_find_music_in_folder(self, tmp_path):
        music_dir = tmp_path / "music"
        music_dir.mkdir()
        _make_audio(str(music_dir / "bed.m4a"), duration=1.0)
        found = background_music.find_music_file(project_folder=str(tmp_path))
        assert found is not None and found.endswith("bed.m4a")

    @pytest.mark.skipif(not FFMPEG, reason="ffmpeg required")
    def test_music_added(self, tmp_path):
        video = tmp_path / "clip.mp4"
        _make_video_with_silence(video)
        music = tmp_path / "bed.m4a"
        _make_audio(str(music), duration=1.0)
        out = tmp_path / "with_music.mp4"
        report = background_music.apply_background_music(str(video), str(music), str(out))
        assert report["ok"] is True
        assert report["music"] == "bed.m4a"
        assert os.path.exists(out)

    @pytest.mark.skipif(not FFMPEG, reason="ffmpeg required")
    def test_missing_music_copies(self, tmp_path):
        video = tmp_path / "clip.mp4"
        _make_video_with_silence(video)
        out = tmp_path / "out.mp4"
        report = background_music.apply_background_music(str(video), None, str(out))
        assert report["ok"] is True
        assert report.get("skipped") is not None


# ---------------------------------------------------------------------------
# branding
# ---------------------------------------------------------------------------

class TestBranding:
    @pytest.mark.skipif(not FFMPEG, reason="ffmpeg required")
    def test_watermark(self, tmp_path):
        video = tmp_path / "clip.mp4"
        _make_video_with_silence(video)
        logo = tmp_path / "logo.png"
        _make_logo(str(logo))
        out = tmp_path / "wm.mp4"
        applied = branding.apply_watermark(str(video), str(out), str(logo))
        assert applied is True
        assert os.path.exists(out)

    @pytest.mark.skipif(not FFMPEG, reason="ffmpeg required")
    def test_missing_logo_copies(self, tmp_path):
        video = tmp_path / "clip.mp4"
        _make_video_with_silence(video)
        out = tmp_path / "out.mp4"
        applied = branding.apply_watermark(str(video), str(out), None)
        assert applied is False
        assert os.path.exists(out)

    @pytest.mark.skipif(not FFMPEG, reason="ffmpeg required")
    def test_process_file_marks_missing_logo_degraded(self, tmp_path):
        video = tmp_path / "clip.mp4"
        _make_video_with_silence(video)
        out = tmp_path / "branded.mp4"
        report = branding.process_file(str(video), str(out), logo_path=str(tmp_path / "missing.png"))
        assert report["ok"] is False
        assert report["degraded"] is True
        assert out.exists()

    @pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="ffmpeg required")
    def test_intro_outro(self, tmp_path):
        video = tmp_path / "clip.mp4"
        _make_video_with_silence(video)
        intro = tmp_path / "intro.mp4"
        _make_video_with_silence(intro)
        outro = tmp_path / "outro.mp4"
        _make_video_with_silence(outro)
        out = tmp_path / "branded.mp4"
        applied = branding.apply_intro_outro(str(video), str(out),
                                             intro=str(intro), outro=str(outro))
        assert applied is True
        assert os.path.exists(out)
        assert _probe_duration(str(out)) > _probe_duration(str(video))


# ---------------------------------------------------------------------------
# polish orchestrator + retime
# ---------------------------------------------------------------------------

class TestPolish:
    def test_retime_subs(self, tmp_path):
        subs = tmp_path / "clip_processed.json"
        subs.write_text(json.dumps({
            "segments": [
                {"start": 0.0, "end": 5.0, "text": "a", "words": [
                    {"word": "a", "start": 0.1, "end": 0.5},
                    {"word": "b", "start": 2.0, "end": 2.5},
                    {"word": "c", "start": 4.0, "end": 4.5}]}
            ]}), encoding="utf-8")
        polish.retime_subs(str(subs), [(1.0, 3.0)], intro_duration=0.5)
        data = json.loads(subs.read_text(encoding="utf-8"))
        words = data["segments"][0]["words"]
        # word b (2.0-2.5) was fully inside the cut → dropped
        assert [w["word"] for w in words] == ["a", "c"]
        # a: 0.1 - 0 + 0.5 = 0.6 ; c: 4.0 - 2.0 + 0.5 = 2.5
        assert words[0]["start"] == pytest.approx(0.6)
        assert words[1]["start"] == pytest.approx(2.5)
        assert data["segments"][0]["start"] == pytest.approx(0.5)

    @pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="ffmpeg required")
    def test_polish_project_end_to_end(self, tmp_path):
        project = tmp_path / "proj"
        final = project / "final"
        subs = project / "subs"
        final.mkdir(parents=True)
        subs.mkdir()
        video = final / "000_Test.mp4"
        _make_video_with_silence(video)
        subs_json = subs / "000_Test_processed.json"
        subs_json.write_text(json.dumps({
            "segments": [{"start": 0.0, "end": 4.0, "text": "hello",
                          "words": [{"word": "hello", "start": 0.1, "end": 0.5}]}]
        }), encoding="utf-8")
        music = tmp_path / "music"
        music.mkdir()
        _make_audio(str(music / "bed.m4a"), duration=1.0)
        logo = tmp_path / "logo.png"
        _make_logo(str(logo))

        reports = polish.polish_project(
            str(project), enable=["jump_cuts", "punch_zoom", "background_music", "branding"],
            logo_path=str(logo), music_volume=0.1)
        assert len(reports) == 1
        assert reports[0]["ok"] is True
        assert reports[0]["quality_status"] in {"enhanced", "partial"}
        assert "branding" in reports[0]["requested_stages"]
        assert "branding" in reports[0]["applied_stages"]
        assert reports[0]["media_validated"] is True
        out = final.parent / "final_polished" / "000_Test.mp4"
        assert out.exists()
        assert _probe_duration(str(out)) > 0
        report_path = project / "polish_report.json"
        assert report_path.exists()
        persisted = json.loads(report_path.read_text(encoding="utf-8"))
        assert persisted["summary"]["enhanced"] + persisted["summary"]["partial"] == 1

    @pytest.mark.skipif(not FFMPEG, reason="ffmpeg required")
    def test_polish_empty_project(self, tmp_path):
        project = tmp_path / "proj"
        (project / "final").mkdir(parents=True)
        reports = polish.polish_project(str(project))
        assert reports == []
