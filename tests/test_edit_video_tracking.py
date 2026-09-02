from types import SimpleNamespace

import numpy as np

from scripts import edit_video
from scripts.active_speaker import ActiveSpeakerSelector


def test_finalize_video_rejects_audio_extraction_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        edit_video.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr="audio failed"),
    )
    assert edit_video.finalize_video(
        str(tmp_path / "input.mp4"), str(tmp_path / "frames.mp4"), 0, 30,
        str(tmp_path), str(tmp_path / "final"),
    ) is False


def test_finalize_video_requires_validated_mux_output(tmp_path, monkeypatch):
    cuts = tmp_path / "cuts"
    final = tmp_path / "final"
    cuts.mkdir()
    final.mkdir()
    frames = tmp_path / "frames.mp4"
    frames.write_bytes(b"frames")

    def fake_run(command, **kwargs):
        from pathlib import Path
        Path(command[-1]).write_bytes(b"encoded")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(edit_video.subprocess, "run", fake_run)
    monkeypatch.setattr(edit_video, "get_best_encoder", lambda: ("libx264", "ultrafast"))
    monkeypatch.setattr(edit_video, "validate_media_file", lambda path, require_audio=True: {"ok": True})

    assert edit_video.finalize_video(
        str(tmp_path / "input.mp4"), str(frames), 0, 30,
        str(tmp_path), str(final),
    ) is True
    assert (final / "final-output000_processed.mp4").exists()


def test_mouth_ratio_106_uses_mouth_landmarks():
    landmarks = np.zeros((106, 2), dtype=float)
    landmarks[52:72, 0] = np.linspace(10, 30, 20)
    landmarks[52:72, 1] = 20
    landmarks[60, 1] = 10
    landmarks[64, 1] = 30
    ratio = edit_video.calculate_mouth_ratio_106(landmarks)
    assert ratio > 0.5


def test_mouth_ratio_106_rejects_invalid_shape():
    assert edit_video.calculate_mouth_ratio_106(np.zeros((20, 2))) == 0.0


def test_active_speaker_order_survives_face_size_difference():
    selector = ActiveSpeakerSelector(switch_margin=1.0, hold_frames=1)
    small_talker = {"center": (100, 100), "activity_score": 8.0}
    large_silent = {"center": (300, 100), "activity_score": 1.0}
    ordered, switched = edit_video.order_faces_for_crop(
        [large_silent, small_talker],
        focus_active_speaker=True,
        selector=selector,
        frame_index=0,
    )
    assert switched is True
    assert ordered[0] is small_talker


def test_tracking_disabled_preserves_detection_order():
    first = {"center": (100, 100), "activity_score": 8.0}
    second = {"center": (300, 100), "activity_score": 1.0}
    ordered, switched = edit_video.order_faces_for_crop(
        [first, second], focus_active_speaker=False, selector=None)
    assert ordered == [first, second]
    assert switched is False

def test_smooth_boxes_per_slot_alpha_one_is_identity():
    # alpha=1.0 must return the raw boxes untouched (both layouts).
    smoothers = [edit_video.SmoothBox(alpha=1.0) for _ in range(4)]
    boxes = [[10.0, 20.0, 110.0, 220.0], [300.0, 50.0, 400.0, 150.0]]
    out, count = edit_video.smooth_boxes_per_slot(
        boxes, smoothers, 1.0, 0, 1920, 1080)
    assert count == 2
    for got, raw in zip(out, boxes):
        assert [round(v, 6) for v in got] == raw


def test_smooth_boxes_per_slot_ema_converges_and_moves_halfway():
    # alpha=0.5: after one update the centre sits halfway between old and new.
    smoothers = [edit_video.SmoothBox(alpha=0.5) for _ in range(4)]
    first = edit_video.smooth_boxes_per_slot(
        [[0.0, 0.0, 100.0, 100.0]], smoothers, 0.5, 0, 1920, 1080)[0][0]
    assert [round(v, 6) for v in first] == [0.0, 0.0, 100.0, 100.0]
    # Face moved +10 in x/y -> centre (50,50) -> (55,55); size unchanged.
    moved = edit_video.smooth_boxes_per_slot(
        [[10.0, 10.0, 110.0, 110.0]], smoothers, 0.5, 1, 1920, 1080)[0][0]
    assert round(moved[0], 6) == 5.0
    assert round(moved[1], 6) == 5.0
    assert round(moved[2], 6) == 105.0
    assert round(moved[3], 6) == 105.0


def test_smooth_boxes_per_slot_multi_face_keeps_slots_independent():
    smoothers = [edit_video.SmoothBox(alpha=0.5) for _ in range(4)]
    first, count = edit_video.smooth_boxes_per_slot(
        [[0.0, 0.0, 100.0, 100.0], [500.0, 500.0, 600.0, 600.0]],
        smoothers, 0.5, 0, 1920, 1080)
    assert count == 2
    # Only slot 0 moves; slot 1 untouched -> EMA must not bleed across slots.
    second, _ = edit_video.smooth_boxes_per_slot(
        [[10.0, 0.0, 110.0, 100.0], [500.0, 500.0, 600.0, 600.0]],
        smoothers, 0.5, 2, 1920, 1080)
    assert round(second[0][0], 6) == 5.0          # slot0 moved halfway
    assert round(second[1][0], 6) == 500.0        # slot1 untouched


def test_smooth_boxes_per_slot_resets_on_count_change():
    # Switching 1 face -> 2 faces must reset history so the new slot starts
    # at the raw box instead of inheriting a stale centre.
    smoothers = [edit_video.SmoothBox(alpha=0.5) for _ in range(4)]
    edit_video.smooth_boxes_per_slot(
        [[0.0, 0.0, 100.0, 100.0]], smoothers, 0.5, 0, 1920, 1080)
    out, count = edit_video.smooth_boxes_per_slot(
        [[1000.0, 0.0, 1100.0, 100.0], [0.0, 1000.0, 100.0, 1100.0]],
        smoothers, 0.5, 1, 1920, 1080)
    assert count == 2
    assert round(out[0][0], 6) == 1000.0  # fresh start, no EMA lag
    assert round(out[1][0], 6) == 0.0


def test_smooth_boxes_per_slot_clamps_to_frame():
    smoothers = [edit_video.SmoothBox(alpha=1.0) for _ in range(4)]
    out, _ = edit_video.smooth_boxes_per_slot(
        [[-20.0, -10.0, 30.0, 40.0], [900.0, 900.0, 1200.0, 1100.0]],
        smoothers, 1.0, 0, 1000, 1000)
    assert out[0] == [0.0, 0.0, 30.0, 40.0]
    assert out[1] == [900.0, 900.0, 1000.0, 1000.0]


def test_smooth_disabled_returns_raw_without_state():
    smoothers = [edit_video.SmoothBox(alpha=0.5) for _ in range(4)]
    boxes = [[1.0, 2.0, 3.0, 4.0]]
    out, count = edit_video.smooth_boxes_per_slot(
        boxes, smoothers, 0.0, 1, 1920, 1080)
    assert out == boxes and count == 1


def test_face_count_hold_rides_through_blips_then_drops():
    # 2-face layout, second face missing: hold for `grace` cycles (fast
    # re-detects) and only then allow the drop to 1 face.
    assert edit_video.face_count_hold(2, 1, [[0, 0, 1, 1], [2, 2, 3, 3]], 0, 2) == (True, 1)
    assert edit_video.face_count_hold(2, 1, [[0, 0, 1, 1], [2, 2, 3, 3]], 1, 2) == (True, 2)
    assert edit_video.face_count_hold(2, 1, [[0, 0, 1, 1], [2, 2, 3, 3]], 2, 2) == (False, 0)
    # Face came back -> reset immediately, no hold.
    assert edit_video.face_count_hold(2, 2, [[0, 0, 1, 1], [2, 2, 3, 3]], 1, 2) == (False, 0)


def test_face_count_hold_ignores_single_face_and_empty():
    # Single-face layout / no faces: no hold behaviour, misses preserved.
    assert edit_video.face_count_hold(1, 1, [[0, 0, 1, 1]], 0, 2) == (False, 0)
    assert edit_video.face_count_hold(1, 0, None, 3, 2) == (False, 3)
    assert edit_video.face_count_hold(2, 0, [[0, 0, 1, 1], [2, 2, 3, 3]], 1, 2) == (False, 1)
