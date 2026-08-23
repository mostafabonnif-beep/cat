# -*- coding: utf-8 -*-
"""
Thumbnail generator (v7.23) — صور مصغرة احترافية للمقاطع.

Creates YouTube-ready 1280x720 thumbnails from a video frame (or an image)
with:

* **Face-aware framing** — when OpenCV finds a face, the frame is cropped so
  the face sits in the upper-third (classic YouTube thumbnail composition).
* **Arabic/English text** — title + optional hook line, rendered with the
  bundled Montserrat fonts (Arabic falls back to DejaVu if needed), with
  smart line wrapping.
* **Professional styling** — optional gradient overlay for readability,
  accent bar, and a hook badge.

Dependencies: Pillow + OpenCV (both already in requirements). Everything
degrades gracefully (no face -> center crop; missing font -> default).

    python scripts/thumbnail_generator.py video.mp4 --title "كسب المال" --out thumb.png
    python scripts/thumbnail_generator.py frame.jpg --title "Title" --hook "SECRET!" --out t.png
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    HAS_PIL = True
except Exception:  # pragma: no cover
    Image = ImageDraw = ImageFilter = ImageFont = None
    HAS_PIL = False

try:
    import cv2
    HAS_CV2 = True
except Exception:  # pragma: no cover
    cv2 = None
    HAS_CV2 = False

TARGET_W, TARGET_H = 1280, 720
FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")


def _font_path(name: str) -> str:
    path = os.path.join(FONT_DIR, name)
    return path if os.path.isfile(path) else ""


def _load_font(size: int, bold: bool = True, text: str = ""):
    if not HAS_PIL:
        return None
    # Arabic text needs an Arabic font (Montserrat has no Arabic glyphs).
    has_arabic = any("\u0600" <= ch <= "\u06FF" for ch in (text or ""))
    if has_arabic:
        candidates = [
            _font_path("Cairo-Bold.ttf") if bold else _font_path("Cairo-Regular.ttf"),
            _font_path("Cairo-Bold.ttf"),
        ]
        for path in candidates:
            if path:
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
    candidates = [
        _font_path("Montserrat-ExtraBold.ttf") if bold else _font_path("Montserrat-Regular.ttf"),
        _font_path("Montserrat-Bold.ttf"),
        _font_path("Montserrat-Regular.ttf"),
    ]
    for path in candidates:
        if path:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def extract_frame(video_path: str, at_seconds: float = 0.0):
    """Return a PIL image from a video frame (or load an image directly)."""
    if not HAS_CV2:
        return None
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        if fps <= 0 or fps > 240:
            fps = 30.0
        frame_index = int(at_seconds * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = cap.read()
        if not ok or frame is None:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
        if not ok or frame is None:
            return None
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)
    finally:
        cap.release()


def _face_bbox(image):
    """Return (x1, y1, x2, y2) of the largest face, or None.

    Tries OpenCV's FaceDetectorYN (works in cv2 4.5+ and 5.x) first, then
    the legacy Haar cascade path (cv2 < 5.0). Returns None when no face is
    found or OpenCV is unavailable — the caller falls back to center crop.
    """
    if not HAS_CV2:
        return None
    import numpy as np
    rgb = np.array(image.convert("RGB"))
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]

    # Preferred: FaceDetectorYN (works on cv2 5.x)
    detector = None
    try:
        detector = cv2.FaceDetectorYN_create(
            "", "", (w, h),
            score_threshold=0.6, nms_threshold=0.3, top_k=5)
        detector.setInputSize((w, h))
        _ok, faces = detector.detect(bgr)
        if faces is not None and len(faces) > 0:
            # faces: N x 15 (bbox + landmarks + score); take the largest
            best = max(faces, key=lambda f: f[2] * f[3])
            x, y, fw, fh = (int(v) for v in best[:4])
            return (x, y, x + fw, y + fh)
    except Exception:
        pass

    # Legacy fallback: Haar cascade (cv2 < 5.0)
    try:
        cascade = cv2.CascadeClassifier(
            os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                         minSize=(60, 60))
        if len(faces) == 0:
            return None
        x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        return (int(x), int(y), int(x + fw), int(y + fh))
    except Exception:
        return None


def _cover_crop(image, target_w=TARGET_W, target_h=TARGET_H, face_box=None):
    """Crop to 16:9, favoring the face in the upper-third when known."""
    w, h = image.size
    target_ratio = target_w / target_h
    img_ratio = w / h

    if img_ratio > target_ratio:
        crop_w = int(h * target_ratio)
        crop_h = h
    else:
        crop_w = w
        crop_h = int(w / target_ratio)

    # horizontal center by default, or face center
    center_x = w // 2
    if face_box:
        center_x = int((face_box[0] + face_box[2]) / 2)
    crop_x = max(0, min(center_x - crop_w // 2, w - crop_w))

    # vertical: face in the upper third (y ≈ 30% of crop height)
    if face_box:
        face_cy = int((face_box[1] + face_box[3]) / 2)
        crop_y = int(face_cy - crop_h * 0.30)
    else:
        crop_y = (h - crop_h) // 2
    crop_y = max(0, min(crop_y, h - crop_h))

    return image.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))


def _wrap_text(text: str, font, max_width: int) -> list[str]:
    if not HAS_PIL:
        return [text]
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    words = str(text or "").split()
    lines, current = [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:3]  # max 3 lines


def generate_thumbnail(source: str, *, title: str = "", hook: str = "",
                       out: str = "thumbnail.png", at_seconds: float = 0.0,
                       accent: str = "#FFD400") -> dict[str, Any]:
    """Create a YouTube thumbnail from a video or image source."""
    if not HAS_PIL:
        return {"ok": False, "error": "Pillow is required (pip install Pillow)"}

    image = extract_frame(source, at_seconds) if source.lower().endswith(
        (".mp4", ".mov", ".mkv", ".webm", ".avi")) else None
    if image is None and os.path.isfile(source):
        try:
            image = Image.open(source).convert("RGB")
        except Exception as exc:
            return {"ok": False, "error": "cannot open source: {}".format(exc)}
    if image is None:
        return {"ok": False, "error": "no frame/image extracted from {}".format(source)}

    face_box = _face_bbox(image)
    cropped = _cover_crop(image, face_box=face_box).resize(
        (TARGET_W, TARGET_H), Image.LANCZOS)

    # subtle dark gradient at the bottom for text readability
    overlay = Image.new("RGBA", (TARGET_W, TARGET_H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for y in range(int(TARGET_H * 0.45), TARGET_H):
        alpha = int(130 * (y - TARGET_H * 0.45) / (TARGET_H * 0.55))
        od.line([(0, y), (TARGET_W, y)], fill=(0, 0, 0, min(150, alpha)))
    cropped = Image.alpha_composite(cropped.convert("RGBA"), overlay).convert("RGB")

    draw = ImageDraw.Draw(cropped)

    # accent bar
    try:
        accent_rgb = tuple(int(accent.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        accent_rgb = (255, 212, 0)
    draw.rectangle([0, TARGET_H - 14, TARGET_W, TARGET_H], fill=accent_rgb)

    # hook badge (top-left)
    if hook:
        hook_font = _load_font(64, bold=True, text=hook)
        hw = draw.textlength(hook, font=hook_font) + 48
        draw.rounded_rectangle([24, 24, 24 + hw, 112], radius=16, fill=accent_rgb)
        draw.text((48, 40), hook, font=hook_font, fill=(10, 10, 10))

    # title (bottom, wrapped)
    if title:
        title_font = _load_font(78, bold=True, text=title)
        lines = _wrap_text(title, title_font, TARGET_W - 80)
        y = TARGET_H - 40 - len(lines) * 92
        for line in lines:
            draw.text((40, y), line, font=title_font, fill=(255, 255, 255),
                      stroke_width=6, stroke_fill=(0, 0, 0))
            y += 92

    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    cropped.save(out, "PNG")
    return {"ok": True, "out": out, "size": (TARGET_W, TARGET_H),
            "face_detected": face_box is not None,
            "engine": "pillow+opencv"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="video file or image")
    parser.add_argument("--title", default="", help="main title (bottom)")
    parser.add_argument("--hook", default="", help="hook badge (top-left)")
    parser.add_argument("--out", default="thumbnail.png")
    parser.add_argument("--at", type=float, default=0.0,
                        help="video frame time in seconds (default 0)")
    parser.add_argument("--accent", default="#FFD400", help="accent hex color")
    args = parser.parse_args(argv)

    result = generate_thumbnail(
        args.source, title=args.title, hook=args.hook, out=args.out,
        at_seconds=args.at, accent=args.accent)
    if not result["ok"]:
        print("[thumbnail] ERROR: {}".format(result["error"]))
        return 1
    print("[thumbnail] OK -> {} (face detected: {})".format(
        result["out"], result["face_detected"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
