# -*- coding: utf-8 -*-
"""
Visual Check — local ONNX frame classifier for the risk scorecard.

Roadmap item 2.1 ("فحص بصري بنموذج حقيقي"). The scorecard already exposes a
`visual_model_path` hook; this module makes it real:

    * extracts 3-5 frames per clip with ffmpeg (pure stdlib + ffmpeg),
    * runs them through a small local ONNX classifier (NudeNet-lite
      convention by default: 320x320 RGB, classes
      [drawing, hentai, neutral, porn, sexy]),
    * reports a per-clip "graphic content probability" (0..100) with
      per-frame breakdown,
    * degrades gracefully: no model file → `available=False`, no
      onnxruntime → same. The pipeline never crashes because of vision.

The model is NOT bundled with the repo (gitignore excludes models/*.onnx).
Use `--auto-download-visual` (or `python -m scripts.visual_check
--download`) to fetch the small NudeNet-lite model into models/ once;
after that everything runs fully offline.

Class metadata (input size / class list) can be overridden with a sidecar
JSON file next to the model, e.g. models/nudenet.onnx.json:
    {"input_size": 320, "classes": ["drawing","hentai","neutral","porn","sexy"],
     "graphic": ["hentai","porn","sexy"]}
"""

import json
import os
import subprocess
import sys
import urllib.request

# Default NudeNet-lite classifier convention.
DEFAULT_CLASSES = ["drawing", "hentai", "neutral", "porn", "sexy"]
DEFAULT_GRAPHIC_CLASSES = ["hentai", "porn", "sexy"]
DEFAULT_INPUT_SIZE = 320

# Pinned release asset (small classifier, not the 27 MB detector).
DEFAULT_MODEL_URL = (
    "https://github.com/notAI-tech/NudeNet/releases/download/v0.0.1/classifier_v2.onnx"
)
MODEL_HINT = "NudeNet-lite classifier (classifier_v2.onnx, ~6 MB)"


def default_model_path(base_dir=None):
    base = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "models", "nudenet_lite.onnx")


def _load_model_meta(model_path):
    """Sidecar JSON next to the model (same basename + .json)."""
    meta_path = model_path + ".json"
    if not os.path.exists(meta_path):
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def extract_frames(video_path, num_frames=4, work_dir=None, width=320, height=320):
    """Extract `num_frames` evenly-spaced frames as PNGs. Returns list of paths.

    Falls back to fewer frames for very short clips. Returns [] on any error
    (never raises).
    """
    if num_frames < 1 or not os.path.exists(video_path):
        return []
    frames = []
    try:
        # get duration
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30)
        duration = float(probe.stdout.strip() or 0)
    except Exception:
        duration = 0.0
    if duration <= 0:
        duration = 10.0  # unknown → sample at 0..10s
        step = 2.5
    else:
        step = max(0.05, duration / max(num_frames, 1))

    work = work_dir or os.path.dirname(video_path)
    os.makedirs(work, exist_ok=True)
    base = os.path.splitext(os.path.basename(video_path))[0]
    at = 0.0
    i = 0
    while at < duration and i < num_frames:
        out = os.path.join(work, "{}.vis{}.png".format(base, i))
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", "{:.3f}".format(at), "-i", video_path,
            "-frames:v", "1", "-vf", "scale={}:{}:force_original_aspect_ratio=decrease,pad={}:{}:(ow-iw)/2:(oh-ih)/2".format(
                width, height, width, height),
            out,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=60, check=True)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                frames.append(out)
        except Exception:
            break
        at += step
        i += 1
    return frames


class NudeNetClassifier:
    """Thin ONNX classifier wrapper. Never raises on environment problems."""

    def __init__(self, model_path=None, classes=None, graphic_classes=None,
                 input_size=None):
        self.model_path = model_path or default_model_path()
        meta = _load_model_meta(self.model_path)
        self.classes = classes or meta.get("classes") or DEFAULT_CLASSES
        self.graphic_classes = (graphic_classes or meta.get("graphic")
                                or DEFAULT_GRAPHIC_CLASSES)
        self.input_size = int(input_size or meta.get("input_size") or DEFAULT_INPUT_SIZE)
        self._session = None
        self._error = None
        self._load()

    def _load(self):
        if not os.path.exists(self.model_path):
            self._error = "model not found at {}".format(self.model_path)
            return
        try:
            import onnxruntime as ort  # noqa: F401  (deliberately lazy)
        except ImportError:
            self._error = "onnxruntime not installed (pip install onnxruntime)"
            return
        try:
            self._session = ort.InferenceSession(
                self.model_path, providers=["CPUExecutionProvider"])
        except Exception as e:
            self._error = "failed to load ONNX model: {}".format(e)

    @property
    def available(self):
        return self._session is not None

    @property
    def error(self):
        return self._error

    def predict_frame(self, image_path):
        """Run one frame through the model → {class: probability} dict.

        The frame is decoded with ffmpeg (raw rgb24 pipe) so the only
        inference dependencies are numpy + onnxruntime — no PIL needed.
        """
        if not self.available:
            return None
        try:
            import numpy as np

            size = self.input_size
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", image_path,
                "-vf", "scale={}:{}".format(size, size),
                "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
            ]
            res = subprocess.run(cmd, capture_output=True, timeout=60)
            expected = size * size * 3
            if len(res.stdout) < expected:
                self._error = "could not decode frame ({} bytes)".format(len(res.stdout))
                return None
            arr = np.frombuffer(res.stdout[:expected], dtype=np.uint8)
            arr = arr.reshape(1, 3, size, size).astype(np.float32) / 255.0  # NCHW
            name = self._session.get_inputs()[0].name
            out = self._session.run(None, {name: arr})[0]
            probs = out[0]
            # Some ONNX classifiers already emit normalized probabilities.
            # Only apply softmax when the raw output clearly isn't normalized.
            s = float(probs.sum())
            if not (0.9 <= s <= 1.1):
                exp = np.exp(probs - probs.max())
                probs = exp / exp.sum()
            result = {}
            for i, cls in enumerate(self.classes):
                if i < len(probs):
                    result[cls] = float(probs[i])
            return result
        except Exception as e:
            self._error = "inference failed: {}".format(e)
            return None

    def analyze_video(self, video_path, num_frames=4, work_dir=None):
        """Score a video clip. Returns a report dict (never raises)."""
        report = {
            "model": os.path.basename(self.model_path) if os.path.exists(self.model_path) else None,
            "available": self.available,
            "error": self._error,
            "frames": [],
            "graphic_score": None,  # 0..100
            "graphic": False,
            "top_class": None,
        }
        if not self.available:
            return report

        frames = extract_frames(video_path, num_frames, work_dir,
                                width=self.input_size, height=self.input_size)
        frame_scores = []
        for i, fp in enumerate(frames):
            pred = self.predict_frame(fp)
            if pred is None:
                continue
            graphic = sum(pred.get(c, 0.0) for c in self.graphic_classes)
            top = max(pred, key=pred.get)
            frame_scores.append({
                "frame": i,
                "graphic_prob": round(graphic * 100.0, 1),
                "top_class": top,
                "top_prob": round(pred[top] * 100.0, 1),
                "scores": {k: round(v * 100.0, 1) for k, v in pred.items()},
            })
            report["frames"].append(frame_scores[-1])

        if frame_scores:
            report["graphic_score"] = round(
                max(f["graphic_prob"] for f in frame_scores), 1)
            report["graphic"] = report["graphic_score"] >= 50.0
            report["top_class"] = max(frame_scores, key=lambda f: f["graphic_prob"])["top_class"]
        # cleanup extracted frames
        for fp in frames:
            try:
                os.remove(fp)
            except Exception:
                pass
        return report


def download_model(target_path=None, url=None):
    """Download the small classifier model. Returns the path or raises."""
    target = target_path or default_model_path()
    url = url or DEFAULT_MODEL_URL
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.exists(target) and os.path.getsize(target) > 0:
        print("Visual model already present: {}".format(target))
        return target
    print("Downloading {} from {}...".format(MODEL_HINT, url))
    tmp = target + ".part"
    try:
        with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as f:
            f.write(resp.read())
        os.replace(tmp, target)
    except Exception as e:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        raise RuntimeError("visual model download failed: {}".format(e)) from None
    print("Saved visual model to {}".format(target))
    return target


def make_classifier(model_path=None):
    """Build a classifier or return None when the model file is absent.

    Use this in pipeline code so missing models are a silent no-op:
        classifier = visual_check.make_classifier(path)
        if classifier is not None:
            report = classifier.analyze_video(clip)
    """
    path = model_path or default_model_path()
    if not os.path.exists(path):
        return None
    return NudeNetClassifier(path)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ViralCutter visual check (ONNX).")
    parser.add_argument("--video", help="Video clip to analyze")
    parser.add_argument("--model", default=None, help="ONNX model path")
    parser.add_argument("--download", action="store_true",
                        help="Download the default visual model and exit")
    parser.add_argument("--frames", type=int, default=4, help="Frames to sample")
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    args = parser.parse_args()

    if args.download:
        download_model(args.model)
        return 0

    if not args.video:
        parser.error("--video is required unless --download is used")

    clf = NudeNetClassifier(args.model) if (args.model or os.path.exists(default_model_path())) else None
    if clf is None:
        print("No visual model. Run: python -m scripts.visual_check --download")
        return 2
    report = clf.analyze_video(args.video, num_frames=args.frames)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("model: {}".format(report["model"]))
        print("graphic_score: {}  graphic: {}".format(
            report["graphic_score"], report["graphic"]))
        for f in report["frames"]:
            print("  frame {}: graphic={} top={}({}%)".format(
                f["frame"], f["graphic_prob"], f["top_class"], f["top_prob"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
