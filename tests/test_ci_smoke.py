# -*- coding: utf-8 -*-
"""CI smoke test — real-video pipeline check (Roadmap 4.3).

Runs the actual ffmpeg chain on a tiny generated video: jump cuts,
watermark, background music, the upload gate and the metadata check.
Fast (<20s) and dependency-light: needs only ffmpeg + pytest.

The CI workflow installs ffmpeg on ubuntu-latest before `pytest`, so
these tests really execute (they are skipped locally when ffmpeg is
missing, like the other ffmpeg-dependent tests).
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import (
    background_music,
    branding,
    jump_cuts,
    media_validation,
    metadata_compliance,
    upload_gate,
)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(not FFMPEG or not FFPROBE,
                                reason="ffmpeg/ffprobe not available")


def _make_test_assets(tmp_path):
    """One video (with a silence gap + tone audio) + music + logo."""
    video = tmp_path / "000_Test.mp4"
    subprocess.run([
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=4",
        "-filter_complex",
        "[1:a]volume=0:enable='between(t,1.2,2.6)',volume=0.6:enable='not(between(t,1.2,2.6))'[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-shortest", str(video),
    ], check=True, capture_output=True)
    music = tmp_path / "bed.m4a"
    subprocess.run([
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=44100:duration=1",
        "-c:a", "aac", str(music),
    ], check=True, capture_output=True)
    logo = tmp_path / "logo.png"
    subprocess.run([
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=red:size=100x100,format=rgba",
        "-frames:v", "1", str(logo),
    ], check=True, capture_output=True)
    return video, music, logo


def _duration(path):
    res = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True)
    return float(res.stdout.strip())


def test_ci_pipeline_smoke(tmp_path):
    """End-to-end smoke: silence removal → watermark → music → gate."""
    video, music, logo = _make_test_assets(tmp_path)
    base = tmp_path / "project"
    (base / "final").mkdir(parents=True)
    (base / "subs").mkdir()

    # 1) jump cuts actually shorten the clip
    subs = base / "subs" / "000_Test_processed.json"
    subs.write_text(json.dumps({
        "segments": [{"start": 0.0, "end": 4.0, "text": "hello",
                      "words": [{"word": "hello", "start": 0.1, "end": 0.5},
                                {"word": "um", "start": 1.3, "end": 1.6}]}]
    }), encoding="utf-8")
    jc_out = tmp_path / "jc.mp4"
    jc_report = jump_cuts.process_file(str(video), subs_json=str(subs),
                                       out_path=str(jc_out))
    assert jc_report["ok"] is True
    assert _duration(str(jc_out)) < _duration(str(video)) - 0.8

    # 2) watermark produces a valid video
    wm_out = tmp_path / "wm.mp4"
    applied = branding.apply_watermark(str(jc_out), str(wm_out), str(logo))
    assert applied is True and os.path.exists(str(wm_out))

    # 3) background music lands without error
    bm_out = tmp_path / "bm.mp4"
    bm_report = background_music.apply_background_music(str(wm_out), str(music), str(bm_out))
    assert bm_report["ok"] is True

    # 4) the upload gate refuses a clip on the publish blocklist
    with open(base / upload_gate.PUBLISH_BLOCKLIST, "w", encoding="utf-8") as f:
        json.dump({"blocked": [{"index": 0, "title": "Bad",
                                "axes": {"reuse": {"score": 80}}}]}, f)
    verdict = upload_gate.check_clip(str(base), 0, "Title", "Caption", [])
    assert verdict["allowed"] is False

    # 5) metadata check flags a medical claim
    res = metadata_compliance.check_metadata("This cures cancer", "watch", [])
    assert res["ok"] is False

    # 6) ffprobe validation rejects empty output and accepts the rendered file.
    valid = media_validation.validate_media_file(str(bm_out), require_audio=True, expected_aspect="4:3")
    assert valid["ok"] is True
    assert media_validation.validate_media_file(str(bm_out), expected_aspect="9:16")["ok"] is False
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    assert media_validation.validate_media_file(str(broken))["ok"] is False
