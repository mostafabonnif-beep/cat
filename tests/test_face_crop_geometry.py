# -*- coding: utf-8 -*-
"""Regression tests for the v7.31 face-crop geometry fixes.

Covers:
* InsightFace crop: width-aware zoom relaxation (a 9:16 window narrower
  than the face used to bisect it) + the extreme-close-up full-height
  fallback + unchanged geometry when the face already fits;
* two-face composition: ``preserve_order`` keeps identity-stable slots
  while the default still sorts left-to-right;
* ``scaled_dead_zone``: resolution-scaled stabilization threshold
  (1080p identical, 4K ~doubled, <=0 disables);
* ``face_join_hold``: 1→2 split hysteresis (grace count, reset, no
  re-entry delay);
* reframe: clips already at the target size are skipped (no lossy
  re-encode), legacy behavior when probing is unavailable.
"""

import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from scripts import edit_video, reframe
from scripts.face_detection_insightface import crop_and_resize_insightface
from scripts.two_face import _safe_face_boxes, crop_and_resize_multi_faces


def _white_pixels(img):
    """Per-pixel white count (all three channels above 128)."""
    return float(np.all(np.asarray(img) > 128, axis=2).sum())


# ---------------------------------------------------------------------------
# (a) InsightFace crop: horizontal face bisection fix
# ---------------------------------------------------------------------------

def test_face_zoom_relaxes_width_to_keep_whole_face():
    """A zoom that used to bisect the face must now relax so the whole face
    (with ~6% horizontal margin) fits inside the 9:16 window."""
    # 1080p source; face = 480w x 300h white block centered at (900, 540).
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    face = [660, 390, 1140, 690]
    frame[390:690, 660:1140, :] = 255

    out = crop_and_resize_insightface(frame, face, headroom=0.0, face_zoom=0.5)
    assert out.shape == (1920, 1080, 3)

    white = _white_pixels(out)
    # OLD geometry for face_zoom=0.5 bisected this face: window was only
    # int(300 / 0.5 * 9/16) = 337px wide vs. a 480px face. NEW geometry
    # relaxes the zoom to z_fit = (300*1080) / ((480*1.06)*1920) ≈ 0.332,
    # giving a 904x508 window that holds the entire 480x300 block, so the
    # output must keep ~480*300 * (1080*1920)/(904*508) white pixels.
    expected = 480 * 300 * (1080 * 1920) / (904 * 508)
    assert white >= 0.95 * expected, (
        f"face bisected: {white} < 95% of expected {expected}")
    # A bisecting crop would pack the partial face into the frame and yield
    # ~1.59x that count; a fitted crop must stay near the expected value.
    assert white < 1.30 * expected, (
        f"unexpectedly tight crop: {white} vs expected {expected}")
    # Black context must survive on BOTH sides of the frame: a bisecting
    # window (x 732..1069, entirely inside the white block) would push white
    # all the way to the output edges.
    assert out[:, :12, :].mean() < 15.0
    assert out[:, -12:, :].mean() < 15.0


def test_face_zoom_geometry_unchanged_when_face_already_fits():
    """Normal cases must keep the exact legacy math: when the requested zoom
    already fits the face width (z_fit >= zoom_fraction) nothing relaxes."""
    # Face is only 200px wide, so zoom 0.5 (window 337px) already fits.
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    face = [800, 390, 1000, 690]
    frame[390:690, 800:1000, :] = 255

    out = crop_and_resize_insightface(frame, face, headroom=0.0, face_zoom=0.5)
    assert out.shape == (1920, 1080, 3)

    # z_fit = (300*1080)/((200*1.06)*1920) = 0.796 > 0.5 → NOT applied; the
    # window stays int(300/0.5)=600 high and int(600*9/16)=337 wide.
    expected = 200 * 300 * (1080 * 1920) / (600 * 337)
    assert _white_pixels(out) >= 0.95 * expected


def test_face_zoom_extreme_closeup_falls_back_to_full_height_crop():
    """A face wider than ANY 9:16 window in the source (extreme close-up)
    must fall back to the legacy centered full-height crop (best effort),
    byte-identical to the face_zoom=0 geometry."""
    # 700px-wide face: the widest window the 1080p source can produce is
    # the full-height one, int(1080*9/16) = 607px < 700px.
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    face = [610, 340, 1310, 740]
    frame[340:740, 610:1310, :] = 255

    out = crop_and_resize_insightface(frame, face, headroom=0.0, face_zoom=0.4)
    legacy = crop_and_resize_insightface(frame, face, headroom=0.0, face_zoom=0.0)
    assert out.shape == (1920, 1080, 3)
    assert np.array_equal(out, legacy), (
        "extreme close-up must use the centered full-height crop")
    assert _white_pixels(out) > 0


# ---------------------------------------------------------------------------
# (b) Split-cell identity: preserve_order
# ---------------------------------------------------------------------------

# Tiny two-tone frame: A = red block on the left, B = blue block on the
# right. In a vertical stack the first box owns the TOP cell.
def _two_tone_frame():
    frame = np.zeros((120, 240, 3), dtype=np.uint8)
    frame[40:80, 20:60, :] = (0, 0, 255)      # A: red
    frame[40:80, 180:220, :] = (255, 0, 0)    # B: blue
    return frame


BOX_A = (20, 40, 40, 40)
BOX_B = (180, 40, 40, 40)


def _stack_top_bottom_channels(result):
    top = result[:result.shape[0] // 2].reshape(-1, 3).astype(int)
    bottom = result[result.shape[0] // 2:].reshape(-1, 3).astype(int)
    return top, bottom


def test_multi_face_default_still_sorts_left_to_right():
    """Default behavior is unchanged: caller order [B, A] is re-sorted so
    the left face (A, red) owns the top cell."""
    result = crop_and_resize_multi_faces(
        _two_tone_frame(), [BOX_B, BOX_A], target_w=90, target_h=180,
        layout="auto", max_faces=2, zoom_out_factor=1.0,
    )
    assert result.shape == (180, 90, 3)
    top, _ = _stack_top_bottom_channels(result)
    assert top[:, 2].mean() > 200.0, "left face (A, red) must be on top"
    assert top[:, 0].mean() < 20.0


def test_multi_face_preserve_order_keeps_caller_slots():
    """preserve_order=True must keep the caller's identity-stable order:
    [B, A] keeps B (blue) in the top cell even though B sits on the right."""
    result = crop_and_resize_multi_faces(
        _two_tone_frame(), [BOX_B, BOX_A], target_w=90, target_h=180,
        layout="auto", max_faces=2, zoom_out_factor=1.0, preserve_order=True,
    )
    top, bottom = _stack_top_bottom_channels(result)
    assert top[:, 0].mean() > 200.0, "caller-first face (B, blue) must be on top"
    assert top[:, 2].mean() < 20.0
    assert bottom[:, 2].mean() > 200.0, "second caller box (A, red) on the bottom"


def test_safe_face_boxes_preserve_order_still_clamps_and_truncates():
    """preserve_order skips only the LTR sort — clamping/validation and the
    max_faces truncation keep working."""
    # Box partly outside the frame gets clamped but keeps its caller slot.
    boxes = _safe_face_boxes([(-100, -40, 500, 500), BOX_A],
                             (120, 240, 3), max_faces=2, preserve_order=True)
    assert boxes[0] == (0, 0, 240, 120)
    assert boxes[1] == BOX_A
    # Default still LTR-sorts the same input.
    ltr = _safe_face_boxes([BOX_B, BOX_A], (120, 240, 3), max_faces=2)
    assert ltr == [BOX_A, BOX_B]
    # max_faces truncation applies in caller order too.
    truncated = _safe_face_boxes([BOX_B, BOX_A], (120, 240, 3),
                                 max_faces=1, preserve_order=True)
    assert truncated == [BOX_B]


# ---------------------------------------------------------------------------
# (c) Resolution-scaled dead zone
# ---------------------------------------------------------------------------

def test_scaled_dead_zone_1080p_keeps_legacy_value():
    assert edit_video.scaled_dead_zone(40, 1920, 1080) == 40.0
    # Any source at or below the ~1080p reference diagonal keeps factor 1.0.
    assert edit_video.scaled_dead_zone(40, 1280, 720) == 40.0
    assert edit_video.scaled_dead_zone(40, 100, 100) == 40.0


def test_scaled_dead_zone_scales_with_frame_diagonal():
    # 4K (3840x2160) has ~2x the reference diagonal → ~80px for the 40px default.
    scaled = edit_video.scaled_dead_zone(40, 3840, 2160)
    assert scaled == pytest.approx(40 * math.hypot(3840, 2160) / 2203.0)
    assert scaled == pytest.approx(80.0, abs=0.1)
    # Odd sizes scale linearly too.
    assert edit_video.scaled_dead_zone(10, 3840, 2160) == pytest.approx(scaled / 4.0)


def test_scaled_dead_zone_zero_or_negative_disables():
    assert edit_video.scaled_dead_zone(0, 1920, 1080) == 0.0
    assert edit_video.scaled_dead_zone(0, 3840, 2160) == 0.0
    assert edit_video.scaled_dead_zone(-5, 1920, 1080) == 0.0


# ---------------------------------------------------------------------------
# (d) 1→2 split join grace (face_join_hold)
# ---------------------------------------------------------------------------

def test_face_join_hold_allows_only_after_grace_consecutive_wants():
    allow, count = edit_video.face_join_hold(0, True)  # default grace=2
    assert (allow, count) == (False, 1), "first wanted cycle must not split yet"
    allow, count = edit_video.face_join_hold(count, True)
    assert (allow, count) == (True, 2), "second consecutive wanted cycle allows split"


def test_face_join_hold_resets_on_false():
    assert edit_video.face_join_hold(1, False) == (False, 0)
    assert edit_video.face_join_hold(0, False) == (False, 0)
    # After a reset the grace must be earned again from zero.
    allow, count = edit_video.face_join_hold(0, True, grace=2)
    assert allow is False


def test_face_join_hold_immediate_when_count_already_past_grace():
    # Layout already in a 2-face state keeps a warmed counter → no re-entry delay.
    assert edit_video.face_join_hold(2, True) == (True, 3)
    assert edit_video.face_join_hold(7, True, grace=3) == (True, 8)


def test_face_join_hold_grace_one_splits_on_first_want():
    assert edit_video.face_join_hold(0, True, grace=1) == (True, 1)


# ---------------------------------------------------------------------------
# (e) Reframe no-op for already-matching clips
# ---------------------------------------------------------------------------

def test_reframe_skips_clip_already_at_target_size(monkeypatch, tmp_path):
    """A 9:16 clip 'reframed' to 9:16 must not be re-encoded lossily."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        assert "ffprobe" in cmd[0], f"unexpected transcode ran: {cmd}"
        return SimpleNamespace(returncode=0, stdout="1080x1920\n", stderr="")

    monkeypatch.setattr(reframe.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(reframe.subprocess, "run", fake_run)
    clip = tmp_path / "x_subtitled.mp4"
    clip.write_bytes(b"fake video bytes")

    r = reframe.reframe_file(str(clip), (1080, 1920), "crop")
    assert r["ok"] is True
    assert r["skipped"] is True
    assert r["reason"] == "already 1080x1920"
    assert r["clip"] == str(clip)
    # Only the ffprobe probe ran — no ffmpeg transcode command was issued.
    assert len(calls) == 1
    assert "ffprobe" in calls[0][0]


def test_reframe_still_transcodes_when_dimensions_differ(monkeypatch, tmp_path):
    """Clips that really need reframing keep the legacy transcode path."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if "ffprobe" in cmd[0]:
            return SimpleNamespace(returncode=0, stdout="1280x720\n", stderr="")
        Path(cmd[-1]).write_bytes(b"encoded")  # pretend the temp output exists
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(reframe.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(reframe.subprocess, "run", fake_run)
    clip = tmp_path / "x_subtitled.mp4"
    clip.write_bytes(b"fake video bytes")

    r = reframe.reframe_file(str(clip), (1080, 1920), "crop")
    assert r["ok"] is True
    assert not r.get("skipped")
    kinds = [c[0] for c in calls]
    assert kinds == ["ffprobe", "ffmpeg"], f"expected probe then transcode, got {kinds}"


def test_reframe_missing_ffprobe_keeps_legacy_behavior(monkeypatch, tmp_path):
    """When ffprobe is unavailable the clip is transcoded as before (legacy)."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"encoded")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        reframe.shutil, "which",
        lambda name: None if "probe" in name else "/usr/bin/ffmpeg")
    monkeypatch.setattr(reframe.subprocess, "run", fake_run)
    clip = tmp_path / "x_subtitled.mp4"
    clip.write_bytes(b"fake video bytes")

    r = reframe.reframe_file(str(clip), (1080, 1920), "crop")
    assert r["ok"] is True
    assert not r.get("skipped")
    assert len(calls) == 1 and calls[0][0] == "ffmpeg"
