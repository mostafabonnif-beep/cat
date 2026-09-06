# -*- coding: utf-8 -*-
"""Regression tests for the v7.29 tracking / selection / title fixes.

Covers:
* FaceTracker time-normalised velocity (prediction survives variable
  detection intervals);
* InsightFace crop headroom actually applying to small faces, and the
  face-size-aware ``face_zoom`` framing;
* clip-window clamping that never extends past the transcript end;
* two-edge sentence snapping (no more mid-word clip endings);
* clean_json_response no longer corrupting valid escaped newlines;
* SEO title determinism across processes and word-boundary hook matching.
"""

import json
import os
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.face_tracker import FaceTracker


def box(cx, cy, half=50):
    return [cx - half, cy - half, cx + half, cy + half]


# ---------------------------------------------------------------------------
# FaceTracker: time-normalised velocity
# ---------------------------------------------------------------------------

def test_velocity_normalised_per_frame_after_long_cycle():
    """A 4px/frame face measured over a 30-frame cycle must NOT store
    120px/cycle as its velocity — prediction would overshoot massively."""
    tr = FaceTracker(match_gate=1.0, velocity_alpha=1.0)  # alpha clamps to 0.95
    tr.update(0, [box(100, 100)])
    tr.update(30, [box(220, 100)])  # moved 120px over 30 frames => 4px/frame
    track = tr._tracks[0]
    assert track.velocity == pytest.approx((0.95 * 4.0, 0.0))


def test_prediction_scales_with_elapsed_frames():
    tr = FaceTracker(velocity_alpha=1.0)  # effective alpha 0.95 after clamp
    tr.update(0, [box(100, 100)])
    tr.update(10, [box(140, 100)])  # 4px/frame -> velocity 3.8 after EMA
    track = tr._tracks[0]
    assert track.predict_center(20) == pytest.approx((140 + 3.8 * 10, 100.0))
    assert track.predict_center(35) == pytest.approx((140 + 3.8 * 25, 100.0))


def test_reacquires_moving_face_at_predicted_position_after_gap():
    """Face moving 4px/frame, unseen for a cycle: the match gate must look
    ahead by velocity x elapsed frames, where the face really is."""
    tr = FaceTracker(velocity_alpha=0.6, match_gate=0.45)
    tr.update(0, [box(100, 100)])
    tr.update(10, [box(140, 100)])
    tr.update(20, [box(180, 100)])
    tr.update(30, [])  # detector blip: face missing for one cycle
    # Reappears at 40 near the velocity-predicted position (~+4px/frame).
    ids = tr.update(40, [box(258, 104)])
    assert ids == [0], "returning face must keep its identity"
    assert tr.track_count() == 1


# ---------------------------------------------------------------------------
# InsightFace crop: headroom + face_zoom
# ---------------------------------------------------------------------------

def _stripe_frame(h=1000, w=2000, stripe_rows=(430, 440)):
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[stripe_rows[0]:stripe_rows[1], :, :] = 255
    return frame


def _brightest_row(img):
    gray = img.mean(axis=(1, 2))
    return int(np.argmax(gray))


def test_headroom_actually_shifts_crop_for_small_faces():
    """With a face-size-aware crop the vertical position can move; increasing
    headroom must push the world DOWN in the output (face gains space above).
    The old face_h/4 cap made headroom a silent no-op for small faces."""
    from scripts.face_detection_insightface import crop_and_resize_insightface

    frame = _stripe_frame()
    face = [950, 550, 1050, 650]  # center (1000, 600), 100px tall
    plain = crop_and_resize_insightface(frame, face, headroom=0.0, face_zoom=0.25)
    raised = crop_and_resize_insightface(frame, face, headroom=0.3, face_zoom=0.25)
    assert _brightest_row(raised) > _brightest_row(plain)


def test_headroom_never_pushes_chin_out_of_frame():
    from scripts.face_detection_insightface import crop_and_resize_insightface

    frame = np.zeros((1000, 2000, 3), dtype=np.uint8)
    face = [950, 880, 1050, 980]  # chin at y=980, near the bottom
    out = crop_and_resize_insightface(frame, face, headroom=0.35, face_zoom=0.25)
    assert out.shape == (1920, 1080, 3)  # no crash, full canvas


def test_face_zoom_makes_face_fill_fraction_of_frame():
    from scripts.face_detection_insightface import crop_and_resize_insightface

    frame = np.zeros((1000, 2000, 3), dtype=np.uint8)
    face = [950, 550, 1050, 650]  # 100px face in a 1000px-tall source
    frame[550:650, 950:1050, :] = 255
    legacy = crop_and_resize_insightface(frame, face, headroom=0.0, face_zoom=0.0)
    zoomed = crop_and_resize_insightface(frame, face, headroom=0.0, face_zoom=0.25)
    legacy_fill = float((legacy > 128).mean())
    zoomed_fill = float((zoomed > 128).mean())
    # Legacy full-height crop: face ≈ 10% of frame height. Zoom 0.25: ≈ 25%.
    assert zoomed_fill > legacy_fill * 2


# ---------------------------------------------------------------------------
# Clip window clamping + sentence snapping
# ---------------------------------------------------------------------------

def _cvs():
    from scripts import create_viral_segments as cvs
    return cvs


def test_min_duration_extension_never_exceeds_transcript_end():
    cvs = _cvs()
    transcript = [
        {"start": 0.0, "end": 10.0, "text": "opening words here"},
        {"start": 15.0, "end": 20.0, "text": "later detail"},
    ]
    raw = [{"title": "End hook", "start_time_ref": "15s",
            "start_text": "later detail", "end_text": "", "score": 90}]
    result = cvs.process_segments(raw, transcript, 30, 90)
    seg = result["segments"][0]
    assert seg["end_time"] <= 20.0
    assert seg["start_time"] >= 0.0


def test_max_duration_cut_never_exceeds_transcript_end():
    cvs = _cvs()
    transcript = [
        {"start": 0.0, "end": 10.0, "text": "start"},
        {"start": 15.0, "end": 30.0, "text": "middle"},
        {"start": 35.0, "end": 44.0, "text": "final words"},
    ]
    raw = [{"title": "Long", "start_time": 40, "end_time": 300, "score": 90}]
    result = cvs.process_segments(raw, transcript, 15, 90)
    seg = result["segments"][0]
    assert seg["end_time"] <= 44.0


def test_snap_aligns_end_to_sentence_end():
    cvs = _cvs()
    transcript = [
        {"start": 0.0, "end": 5.0, "text": "first sentence"},
        {"start": 6.0, "end": 12.0, "text": "second sentence"},
        {"start": 13.0, "end": 20.0, "text": "third sentence"},
    ]
    # End lands mid-sentence (10.5 inside the 6-12 block): finish it.
    assert cvs.snap_segment_boundaries(2.0, 10.5, transcript) == (0.0, 12.0)


def test_snap_trims_end_that_lands_in_a_pause():
    cvs = _cvs()
    transcript = [
        {"start": 0.0, "end": 5.0, "text": "first sentence"},
        {"start": 6.0, "end": 12.0, "text": "second sentence"},
        {"start": 13.0, "end": 20.0, "text": "third sentence"},
    ]
    # End lands inside the pause at 12.5: trim to the finished sentence.
    assert cvs.snap_segment_boundaries(2.0, 12.5, transcript) == (0.0, 12.0)


def test_snap_never_jumps_start_forward_past_hook():
    cvs = _cvs()
    transcript = [
        {"start": 10.0, "end": 20.0, "text": "only sentence"},
    ]
    start, _ = cvs.snap_segment_boundaries(5.0, 15.0, transcript)
    assert start == 5.0


def test_process_segments_keeps_explicit_window_unsnapped():
    cvs = _cvs()
    transcript = [{"start": float(i), "end": float(i + 1), "text": f"word {i}"}
                  for i in range(0, 61, 5)]
    raw = [{"title": "Exact", "start_time": 10, "end_time": 20, "score": 90}]
    result = cvs.process_segments(raw, transcript, 5, 30)
    seg = result["segments"][0]
    assert (round(seg["start_time"]), round(seg["end_time"])) == (10, 20)


def test_process_segments_sorts_mixed_score_types():
    cvs = _cvs()
    transcript = [{"start": float(i * 30), "end": float(i * 30 + 10),
                   "text": f"moment {i}"} for i in range(6)]
    raw = [
        {"title": "floaty", "start_time": 0, "end_time": 20, "score": "95.5"},
        {"title": "inty", "start_time": 30, "end_time": 50, "score": 80},
        {"title": "broken", "start_time": 60, "end_time": 80, "score": "abc"},
    ]
    result = cvs.process_segments(raw, transcript, 10, 30)
    titles = [s["title"] for s in result["segments"]]
    assert titles[0] == "floaty"
    assert titles[-1] == "broken"


def test_load_transcript_returns_chronological_order(tmp_path):
    cvs = _cvs()
    (tmp_path / "input.tsv").write_text(
        "start\tend\ttext\n"
        "30000\t31000\tthird\n"
        "0\t1000\tfirst\n"
        "15000\t16000\tsecond\n",
        encoding="utf-8")
    segments = cvs.load_transcript(str(tmp_path))
    assert [s["start"] for s in segments] == [0.0, 15.0, 30.0]


# ---------------------------------------------------------------------------
# clean_json_response: valid escaped content must survive
# ---------------------------------------------------------------------------

def test_clean_json_preserves_escaped_newline_inside_title():
    cvs = _cvs()
    payload = json.dumps({"segments": [
        {"title": "سطر أول\nسطر ثاني", "start_time": 1, "end_time": 5},
    ]}, ensure_ascii=False)
    parsed = cvs.clean_json_response(payload)
    assert len(parsed["segments"]) == 1
    assert parsed["segments"][0]["title"] == "سطر أول\nسطر ثاني"


def test_clean_json_still_recovers_double_escaped_payload():
    cvs = _cvs()
    response = ('Here is the result:\\n```JSON\\n'
                '{\\"segments\\": [{\\"start_time\\": 1, \\"end_time\\": 5}]}'
                '\\n```')
    parsed = cvs.clean_json_response(response)
    assert len(parsed["segments"]) == 1


# ---------------------------------------------------------------------------
# SEO titles
# ---------------------------------------------------------------------------

def test_generate_titles_deterministic_across_processes():
    code = (
        "import json,sys; sys.path.insert(0, '.');"
        "from scripts import seo_titles;"
        "print(json.dumps([t['title'] for t in seo_titles.generate_titles("
        "'الربح من الانترنت', count=4)], ensure_ascii=False))"
    )
    env1 = dict(os.environ, PYTHONHASHSEED="1")
    env2 = dict(os.environ, PYTHONHASHSEED="2")
    out1 = subprocess.check_output([sys.executable, "-c", code], env=env1,
                                   cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out2 = subprocess.check_output([sys.executable, "-c", code], env=env2,
                                   cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert out1 == out2


def test_hook_words_match_on_word_boundaries_only():
    from scripts import seo_titles
    show = seo_titles.score_title("Show me the money in 5 steps")
    how = seo_titles.score_title("How to make money in 5 steps")
    # "show" must not collect the "how" hook bonus.
    assert how["breakdown"]["hook"] > show["breakdown"]["hook"]


def test_arabic_hook_words_still_detected():
    from scripts import seo_titles
    plain = seo_titles.score_title("نتيجة مفاجئة في خمس خطوات")
    hooked = seo_titles.score_title("كيف تصل إلى نتيجة في خمس خطوات")
    assert hooked["breakdown"]["hook"] > plain["breakdown"]["hook"]
