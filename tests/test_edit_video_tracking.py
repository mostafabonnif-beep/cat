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
