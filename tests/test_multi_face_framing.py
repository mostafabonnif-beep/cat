import numpy as np

from scripts.two_face import crop_and_resize_multi_faces, crop_and_resize_two_faces


def _frame():
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[:, :320] = (20, 40, 80)
    frame[:, 320:640] = (40, 80, 120)
    frame[:, 640:960] = (80, 120, 160)
    frame[:, 960:] = (120, 160, 200)
    return frame


def test_multi_face_empty_returns_portrait_canvas():
    result = crop_and_resize_multi_faces(_frame(), [])
    assert result.shape == (1920, 1080, 3)
    assert np.all(result == 0)


def test_multi_face_three_uses_grid_layout():
    result = crop_and_resize_multi_faces(
        _frame(),
        [(20, 100, 180, 220), (500, 100, 180, 220), (980, 100, 180, 220)],
        layout="auto",
    )
    assert result.shape == (1920, 1080, 3)
    assert result.dtype == np.uint8
    # The three tiles occupy distinct regions; the unused fourth tile remains valid.
    assert np.unique(result.reshape(-1, 3), axis=0).shape[0] > 3


def test_multi_face_four_supports_speaker_layout():
    result = crop_and_resize_multi_faces(
        _frame(),
        [(20, 100, 180, 220), (350, 100, 180, 220),
         (680, 100, 180, 220), (1010, 100, 220, 280)],
        layout="speaker",
    )
    assert result.shape == (1920, 1080, 3)
    assert np.any(result != 0)


def test_multi_face_clamps_out_of_bounds_boxes():
    result = crop_and_resize_multi_faces(
        _frame(), [(-100, -40, 500, 500), (1100, 600, 400, 300)], max_faces=2
    )
    assert result.shape == (1920, 1080, 3)


def test_legacy_two_face_function_keeps_portrait_shape():
    result = crop_and_resize_two_faces(
        _frame(), [(20, 100, 180, 220), (900, 100, 180, 220)]
    )
    assert result.shape == (1920, 1080, 3)
