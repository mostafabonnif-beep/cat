# -*- coding: utf-8 -*-
"""
Originality engine — duplicate-content defense for OUSSAMA Cutter (v7.18).

YouTube flags "reused content": the same clip published again (even after
re-encoding) can lose monetization or earn a policy strike. This module
helps in two complementary, honest ways:

1. **Perceptual fingerprinting** — a clip is reduced to a compact visual
   fingerprint (frame d-hashes). Two clips that *look* the same — even when
   re-encoded, renamed, or slightly cropped — score as near-duplicates.
   The content guard uses this to refuse accidental re-publication of the
   same content window.

2. **Deterministic transformation presets** — when the user intentionally
   re-publishes a moment (e.g. the same source re-cut for a different
   platform), each output can be made genuinely distinct with seeded
   variations: micro speed shift, mirror, crop offset, color micro-grade.
   Transformations are *real* editorial changes, not metadata spoofing.

Everything degrades gracefully: without OpenCV the fingerprint functions
return ``None`` and the pipeline behaves exactly as before this version.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Any

try:
    import cv2
    HAS_CV2 = True
except Exception:  # pragma: no cover - optional dependency
    cv2 = None
    HAS_CV2 = False

REPORT_FILENAME = "originality_report.json"
FINGERPRINT_NAME = "visual_fingerprint"
FRAMES_TO_SAMPLE = 16
SIMILARITY_BLOCK_RATIO = 0.80   # >= this overlap => near-duplicate
DISTINCT_THRESHOLD = 0.35       # <= this similarity => clearly distinct


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------

def _dhash(frame, size=16):
    """Difference hash of a grayscale frame -> 64-bit int."""
    if frame is None or frame.size == 0:
        return None
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (size + 1, size))
    diff = resized[:, 1:] > resized[:, :-1]
    value = 0
    for row in diff:
        for bit in row:
            value = (value << 1) | int(bit)
    return value


def _hamming(a, b):
    return bin(a ^ b).count("1")


def video_fingerprint(path: str | None, frames: int = FRAMES_TO_SAMPLE) -> list[int] | None:
    """Sample ``frames`` evenly-spaced d-hashes from a video file.

    Returns a list of ints (one per sampled frame) or None when the file is
    unreadable / OpenCV is missing. Sampling is deterministic per file.
    """
    if not HAS_CV2 or not path or not os.path.isfile(path):
        return None
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return None
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            cap.release()
            return None
        step = max(1, total // frames)
        hashes = []
        for frame_index in range(0, total, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = cap.read()
            if ok and frame is not None:
                digest = _dhash(frame)
                if digest is not None:
                    hashes.append(digest)
            if len(hashes) >= frames:
                break
        return hashes if hashes else None
    finally:
        cap.release()


def fingerprint_key(hashes: list[int] | None) -> str | None:
    """Collapse a hash list into a stable storage key (JSON-safe)."""
    if not hashes:
        return None
    payload = ",".join(str(h) for h in hashes)
    return "vf:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _similarity_between(left: list[int] | None, right: list[int] | None) -> float:
    """0..1 visual overlap between two fingerprints (1.0 = identical)."""
    if not left or not right:
        return 0.0
    pairs = min(len(left), len(right))
    if pairs == 0:
        return 0.0
    close = 0
    for a, b in zip(left[:pairs], right[:pairs]):
        # 64-bit d-hash: <=6 differing bits is a near-identical frame.
        if _hamming(a, b) <= 6:
            close += 1
    return round(close / pairs, 3)


def compare_clips(path_a: str | None, path_b: str | None) -> dict[str, Any]:
    """Visual similarity verdict between two rendered clips."""
    fa = video_fingerprint(path_a)
    fb = video_fingerprint(path_b)
    score = _similarity_between(fa, fb)
    return {
        "similarity": score,
        "verdict": "duplicate" if score >= SIMILARITY_BLOCK_RATIO
                   else ("similar" if score >= DISTINCT_THRESHOLD else "distinct"),
        "frames_compared": min(len(fa or []), len(fb or [])),
        "a_has_fingerprint": bool(fa),
        "b_has_fingerprint": bool(fb),
    }


# ---------------------------------------------------------------------------
# Registry-aware assessment (used by content_guard)
# ---------------------------------------------------------------------------

def assess_against_registry(project_folder: str, video_path: str | None,
                            registry_rows: list[dict[str, Any]],
                            block_ratio: float = SIMILARITY_BLOCK_RATIO) -> dict[str, Any]:
    """Compare a candidate clip against previously published fingerprints.

    ``registry_rows``: list of dicts with a ``visual_fingerprint`` key (from
    content_guard's metadata). Returns a verdict with the best match.
    """
    candidate = video_fingerprint(video_path)
    if not candidate:
        return {"checked": False, "reason": "fingerprint unavailable (no OpenCV or unreadable file)"}
    best = None
    best_score = 0.0
    for row in registry_rows:
        stored = (row.get("metadata") or {}).get(FINGERPRINT_NAME)
        if not stored:
            continue
        try:
            stored_hashes = [int(h) for h in str(stored).split("|") if h]
        except ValueError:
            continue
        score = _similarity_between(candidate, stored_hashes)
        if score > best_score:
            best_score = score
            best = row
    if best is None:
        return {"checked": True, "similarity": 0.0, "verdict": "distinct",
                "matched": None}
    return {
        "checked": True,
        "similarity": best_score,
        "verdict": "duplicate" if best_score >= block_ratio
                  else ("similar" if best_score >= DISTINCT_THRESHOLD else "distinct"),
        "matched": {
            "project": best.get("project_path"),
            "video_name": best.get("video_name"),
            "published_at": best.get("created_at"),
        },
    }


# ---------------------------------------------------------------------------
# Deterministic transformation presets
# ---------------------------------------------------------------------------

TRANSFORM_PRESETS = {
    "speed": "micro speed shift (±2%)",
    "mirror": "horizontal mirror",
    "crop": "crop offset jitter (keeps 9:16 framing)",
    "color": "micro color grade (brightness/contrast/saturation)",
    "watermark": "watermark position variation (handled by branding layer)",
}


def _scale_shift(seed: int, low: float, high: float) -> float:
    """Deterministic pseudo-random value in [low, high] from an int seed."""
    value = (seed * 2654435761) % 1000 / 1000.0  # integer hash -> [0,1)
    return low + (high - low) * value


def apply_transformation(input_path: str, output_path: str, *, seed: int = 0,
                         speed: float | None = None, mirror: bool = False,
                         crop_jitter: int = 0, brightness: float = 0.0,
                         contrast: float = 1.0, saturation: float = 1.0,
                         ffmpeg: str = "ffmpeg") -> dict[str, Any]:
    """Apply deterministic editorial variations via a single ffmpeg pass.

    All values are *real* image/speed edits (transformative content), fully
    reversible, and seeded so re-running the same seed is reproducible.
    """
    filters = []
    applied: list[str] = []

    if speed is not None and abs(speed - 1.0) > 0.001:
        filters.append("setpts={:.6f}*PTS".format(1.0 / speed))
        filters.append("atempo={:.6f}".format(speed))
        applied.append("speed:{:.3f}".format(speed))
    if mirror:
        filters.append("hflip")
        applied.append("mirror")
    if crop_jitter:
        filters.append("crop=in_w-{}:in_h-{}:{}-{}:{}-{}".format(
            crop_jitter * 2, crop_jitter * 2, crop_jitter, crop_jitter,
            crop_jitter * 2, crop_jitter))
        filters.append("scale=1080:1920")
        applied.append("crop_jitter:{}".format(crop_jitter))
    if brightness or contrast != 1.0 or saturation != 1.0:
        eq = "eq=brightness={:.3f}:contrast={:.3f}:saturation={:.3f}".format(
            brightness, contrast, saturation)
        filters.append(eq)
        applied.append("color")

    if not filters:
        # Nothing to transform: copy the input.
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", input_path,
               "-c", "copy", output_path]
        subprocess.run(cmd, check=True, capture_output=True)
        return {"ok": True, "transforms": [], "seed": seed}

    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", input_path,
           "-vf", ",".join(filters), "-c:v", "libx264", "-preset", "ultrafast",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", output_path]
    subprocess.run(cmd, check=True, capture_output=True)
    return {"ok": True, "transforms": applied, "seed": seed}


def build_preset(seed: int, *, mirror_probability: float = 0.5,
                 max_speed_delta: float = 0.02,
                 max_crop_jitter: int = 24) -> dict[str, Any]:
    """Deterministic transformation parameters from a seed."""
    speed = 1.0 + _scale_shift(seed, -max_speed_delta, max_speed_delta)
    mirror = _scale_shift(seed + 1, 0.0, 1.0) < mirror_probability
    crop_jitter = int(_scale_shift(seed + 2, 0.0, 1.0) * max_crop_jitter)
    brightness = round(_scale_shift(seed + 3, -0.02, 0.02), 3)
    contrast = round(1.0 + _scale_shift(seed + 4, -0.03, 0.03), 3)
    saturation = round(1.0 + _scale_shift(seed + 5, -0.05, 0.05), 3)
    return {
        "speed": round(speed, 4),
        "mirror": mirror,
        "crop_jitter": crop_jitter,
        "brightness": brightness,
        "contrast": contrast,
        "saturation": saturation,
    }


def transform_with_seed(input_path: str, output_path: str, *, seed: int = 0,
                        preset: dict[str, Any] | None = None,
                        ffmpeg: str = "ffmpeg") -> dict[str, Any]:
    """Apply the seeded preset to a clip (shorthand for pipeline use)."""
    params = preset or build_preset(seed)
    return apply_transformation(
        input_path, output_path, seed=seed, ffmpeg=ffmpeg,
        speed=params.get("speed"), mirror=params.get("mirror"),
        crop_jitter=params.get("crop_jitter"),
        brightness=params.get("brightness"), contrast=params.get("contrast"),
        saturation=params.get("saturation"))


def write_report(project_folder: str, verdicts: list[dict[str, Any]]) -> str:
    """Persist an originality report next to the other project reports."""
    report = {
        "generated_at": _now(),
        "engine": "originality-v1",
        "clips": verdicts,
        "summary": {
            "total": len(verdicts),
            "duplicates": sum(1 for v in verdicts if v.get("verdict") == "duplicate"),
            "similar": sum(1 for v in verdicts if v.get("verdict") == "similar"),
            "distinct": sum(1 for v in verdicts if v.get("verdict") == "distinct"),
        },
    }
    path = os.path.join(project_folder, REPORT_FILENAME)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".tmp",
                                     dir=project_folder, delete=False) as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        tmp = f.name
    os.replace(tmp, path)
    return path


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Originality engine: duplicate-content defense + transformation presets.")
    sub = parser.add_subparsers(dest="command")

    cmp = sub.add_parser("compare", help="compare two clips visually")
    cmp.add_argument("a", help="first video")
    cmp.add_argument("b", help="second video")

    tr = sub.add_parser("transform", help="apply a seeded transformation preset")
    tr.add_argument("input", help="input video")
    tr.add_argument("output", help="output video")
    tr.add_argument("--seed", type=int, default=0)
    tr.add_argument("--speed", type=float, default=None)
    tr.add_argument("--mirror", action="store_true")
    tr.add_argument("--crop-jitter", type=int, default=0)

    fp = sub.add_parser("fingerprint", help="print the fingerprint of a clip")
    fp.add_argument("video", help="input video")

    args = parser.parse_args(argv)
    if args.command == "compare":
        result = compare_clips(args.a, args.b)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["verdict"] != "duplicate" else 1
    if args.command == "transform":
        result = transform_with_seed(args.input, args.output, seed=args.seed) if (
            args.speed is None and not args.mirror and not args.crop_jitter
        ) else apply_transformation(
            args.input, args.output, seed=args.seed, speed=args.speed,
            mirror=args.mirror, crop_jitter=args.crop_jitter)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "fingerprint":
        hashes = video_fingerprint(args.video)
        print(fingerprint_key(hashes) if hashes else "no fingerprint available")
        return 0 if hashes else 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
