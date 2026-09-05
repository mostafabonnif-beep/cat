import numpy as np

from scripts.face_detection_insightface import crop_and_resize_insightface


def test_crop_accepts_float_face_coordinates():
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    result = crop_and_resize_insightface(
        frame,
        [310.75, 120.25, 520.5, 410.9],
        target_width=90,
        target_height=160,
    )
    assert result.shape == (160, 90, 3)


def test_crop_clamps_float_face_coordinates_to_frame():
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    result = crop_and_resize_insightface(
        frame,
        [-20.4, -10.8, 1400.2, 800.6],
        target_width=90,
        target_height=160,
        headroom=0.2,
    )
    assert result.shape == (160, 90, 3)
