# -*- coding: utf-8 -*-
"""
Scene-cut detection (v7.23) — كشف حدود المشاهد لتحسين التقطيع.

Integrates **PySceneDetect** (BSD-3-Clause, Breakthrough/PySceneDetect) as
the primary engine, with a built-in OpenCV fallback when the library is not
installed. Scene boundaries are the *professional* cut points: a clip that
starts/ends at a scene change looks intentional, while a cut in the middle
of a moving shot looks broken.

Usage
-----
    python scripts/scene_detect.py video.mp4 --json scenes.json
    python scripts/scene_detect.py video.mp4 --list

Integration
-----------
``find_scene_cuts(path)`` returns [(start_sec, end_sec), ...] using
PySceneDetect when available, otherwise the OpenCV content-diff fallback.
The cutting pipeline can snap segment edges to the nearest scene boundary
(see ``snap_to_scene``).
"""

from __future__ import annotations

import argparse
import json
import os

try:
    from scenedetect import (  # type: ignore
        ContentDetector,
        SceneManager,
        open_video,
    )
    HAS_SCENEDETECT = True
except Exception:  # pragma: no cover - optional dependency
    HAS_SCENEDETECT = False

try:
    import cv2
    HAS_CV2 = True
except Exception:  # pragma: no cover
    cv2 = None
    HAS_CV2 = False

DEFAULT_THRESHOLD = 27.0      # ContentDetector sensitivity (lower = more cuts)
MIN_SCENE_SECONDS = 1.5       # ignore ultra-short scenes (noise)


# ---------------------------------------------------------------------------
# PySceneDetect path
# ---------------------------------------------------------------------------

def _scenes_with_pyscenedetect(path: str, threshold: float) -> list[tuple[float, float]]:
    video = open_video(path)
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold))
    manager.detect_scenes(video, show_progress=False)
    scenes = []
    for start, end in manager.get_timecode_list():
        try:
            start_s = float(start.get_seconds())
            end_s = float(end.get_seconds())
        except Exception:
            continue
        if end_s - start_s >= MIN_SCENE_SECONDS:
            scenes.append((round(start_s, 2), round(end_s, 2)))
    return scenes


# ---------------------------------------------------------------------------
# OpenCV fallback
# ---------------------------------------------------------------------------

def _scenes_with_opencv(path: str, threshold: float) -> list[tuple[float, float]]:
    """Content-diff scene detection (frame histogram comparison)."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return []
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        if fps <= 0 or fps > 240:
            fps = 30.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        step = max(1, int(fps * 0.5))  # sample every 0.5s
        prev_hist = None
        scenes = []
        scene_start = 0.0
        frame_index = 0
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                break
            if frame_index % step == 0:
                small = cv2.resize(frame, (64, 36))
                hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
                hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
                cv2.normalize(hist, hist)
                if prev_hist is not None:
                    diff = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA)
                    if diff > (threshold / 100.0):
                        scene_end = frame_index / fps
                        if scene_end - scene_start >= MIN_SCENE_SECONDS:
                            scenes.append((round(scene_start, 2), round(scene_end, 2)))
                        scene_start = scene_end
                prev_hist = hist
            frame_index += 1
        # tail scene
        if total and (total / fps) - scene_start >= MIN_SCENE_SECONDS:
            scenes.append((round(scene_start, 2), round(total / fps, 2)))
        return scenes
    finally:
        cap.release()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_scene_cuts(path: str, threshold: float = DEFAULT_THRESHOLD) -> list[tuple[float, float]]:
    """Return scene boundaries as [(start, end), ...] seconds.

    Uses PySceneDetect when installed, otherwise the OpenCV fallback.
    Returns [] on any failure (callers treat that as "no scene info").
    """
    if not path or not os.path.isfile(path):
        return []
    if HAS_SCENEDETECT:
        try:
            return _scenes_with_pyscenedetect(path, threshold)
        except Exception:
            pass  # fall through to OpenCV
    if HAS_CV2:
        try:
            return _scenes_with_opencv(path, threshold)
        except Exception:
            return []
    return []


def snap_to_scene(start: float, end: float, scenes: list[tuple[float, float]],
                  tolerance: float = 2.0) -> tuple[float, float]:
    """Snap a [start, end] window to the nearest scene boundaries.

    Returns the adjusted window, clamped so it never shrinks below
    ``min_duration`` (callers enforce their own min). When no scene is close
    enough, the raw window is returned unchanged.
    """
    start = max(0.0, float(start))
    end = max(start + 0.1, float(end))
    if not scenes:
        return start, end

    # nearest boundary at/after start (to open on a clean scene start)
    best_start = start
    best_start_dist = tolerance
    for s, _e in scenes:
        if s >= start - 0.05 and abs(s - start) <= best_start_dist:
            best_start_dist = abs(s - start)
            best_start = s
    # nearest boundary at/before end (to close on a clean scene end)
    best_end = end
    best_end_dist = tolerance
    for _s, e in scenes:
        if e <= end + 0.05 and abs(e - end) <= best_end_dist:
            best_end_dist = abs(e - end)
            best_end = e
    # never let snapping collapse the window
    if best_end - best_start < 1.0:
        return start, end
    return round(best_start, 2), round(best_end, 2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", help="input video file")
    parser.add_argument("--json", dest="json_out", default=None,
                        help="write scenes to a JSON file")
    parser.add_argument("--list", action="store_true",
                        help="print scenes one per line (srt-like)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help="detector sensitivity (default 27, lower = more cuts)")
    args = parser.parse_args(argv)

    scenes = find_scene_cuts(args.video, args.threshold)
    if args.json_out:
        payload = {"source": args.video, "engine": (
            "pyscenedetect" if HAS_SCENEDETECT else "opencv-fallback"),
            "scenes": [{"start": s, "end": e} for s, e in scenes]}
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print("[scene-detect] wrote {} scenes -> {}".format(len(scenes), args.json_out))
    if args.list:
        for s, e in scenes:
            print("{:.2f} --> {:.2f}".format(s, e))
    if not args.json_out and not args.list:
        print(json.dumps([{"start": s, "end": e} for s, e in scenes],
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
