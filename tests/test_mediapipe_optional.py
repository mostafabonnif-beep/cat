# -*- coding: utf-8 -*-
"""v6.9.1: mediapipe must be OPTIONAL across the editing modules.

Before the fix, `import mediapipe as mp` at the top of edit_video.py /
one_face.py / two_face.py meant a missing mediapipe install killed the
whole pipeline mid-run with a bare ModuleNotFoundError. The usage site in
edit_video.py already falls back to OpenCV Haar Cascade — these tests pin
the import-time guard.
"""

import importlib
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _import_with_fake_cv2(block_mediapipe):
    """Import the three editing modules with cv2/numpy faked and mediapipe
    optionally blocked. Returns the imported modules."""
    saved = {}
    names = ["cv2", "numpy", "scripts.edit_video", "scripts.one_face", "scripts.two_face"]
    for n in names:
        saved[n] = sys.modules.get(n)
        sys.modules.pop(n, None)
    # Remove an already-imported package and its submodules. This matters when
    # the optional dependency is installed: reloading only the top-level name
    # can otherwise reuse cached mediapipe.* modules and bypass the blocker.
    for n in list(sys.modules):
        if n == "mediapipe" or n.startswith("mediapipe."):
            saved[n] = sys.modules.pop(n)

    fake_cv2 = types.ModuleType("cv2")
    fake_np = types.ModuleType("numpy")
    sys.modules["cv2"] = fake_cv2
    sys.modules["numpy"] = fake_np

    class _Blocker:
        def find_spec(self, name, path=None, target=None):
            if name == "mediapipe" or name.startswith("mediapipe."):
                raise ImportError("mediapipe blocked for test")
            return None

        # Keep compatibility with Python versions that still consult the
        # legacy import-hook protocol.
        def find_module(self, name, path=None):
            if name == "mediapipe" or name.startswith("mediapipe."):
                return self
            return None

        def load_module(self, name):
            raise ImportError("mediapipe blocked for test")

    blocker = _Blocker() if block_mediapipe else None
    if blocker:
        sys.meta_path.insert(0, blocker)
    try:
        import scripts.edit_video as ev
        import scripts.one_face as of
        import scripts.two_face as tf
        importlib.reload(of)
        importlib.reload(tf)
        importlib.reload(ev)
        return ev, of, tf
    finally:
        if blocker:
            sys.meta_path.remove(blocker)
        for n, mod in saved.items():
            sys.modules.pop(n, None)
            if mod is not None:
                sys.modules[n] = mod


def test_imports_survive_without_mediapipe():
    ev, of, tf = _import_with_fake_cv2(block_mediapipe=True)
    assert ev.MEDIAPIPE_AVAILABLE is False
    assert ev.mp is None
    # the fallback gate in edit_video relies on this being False for mp=None
    assert hasattr(ev.mp, "solutions") is False


def test_modules_expose_expected_functions():
    ev, of, tf = _import_with_fake_cv2(block_mediapipe=True)
    for fn in ("crop_and_resize_single_face", "resize_with_padding",
               "detect_face_or_body", "crop_center_zoom"):
        assert callable(getattr(of, fn)), fn
    assert callable(tf.detect_face_or_body_two_faces)
    assert callable(ev.get_best_encoder)
