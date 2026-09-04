"""Optional OCR safety scan for text rendered inside video frames."""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from scripts.safety_filter import find_matches
from scripts.semantic_safety import analyze_text


def _binary(name: str = "tesseract") -> str | None:
    return shutil.which(name)


def _duration(video_path: str, ffprobe: str = "ffprobe") -> float:
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, check=True, timeout=20)
        return max(0.0, float(result.stdout.strip()))
    except Exception:
        return 0.0


def availability(tesseract: str = "tesseract") -> dict[str, Any]:
    binary = _binary(tesseract)
    if not binary:
        return {"available": False, "binary": None, "reason": "tesseract_not_installed"}
    try:
        result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return {"available": False, "binary": binary, "reason": "tesseract_unusable"}
    except Exception as exc:
        return {"available": False, "binary": binary, "reason": str(exc)[:200]}
    return {"available": True, "binary": binary, "reason": None}


def _frame_png(video_path: str, seconds: float, ffmpeg: str = "ffmpeg") -> bytes:
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(max(0.0, seconds)),
         "-i", video_path, "-frames:v", "1", "-vf", "scale=1280:-2",
         "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
        capture_output=True, timeout=45, check=True)
    return result.stdout


def _recognize(png: bytes, binary: str, language: str) -> str:
    result = subprocess.run(
        [binary, "stdin", "stdout", "--psm", "6", "-l", language],
        input=png, capture_output=True, text=False, timeout=45, check=True)
    return result.stdout.decode("utf-8", errors="replace").strip()


def _sample_times(duration: float, frames: int) -> list[float]:
    count = max(1, int(frames))
    if duration <= 0:
        return [0.0]
    return [duration * (index + 1) / (count + 1) for index in range(count)]


def analyze_video(video_path: str, *, frames: int = 4, language: str = "ara+eng",
                  ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe",
                  tesseract: str = "tesseract") -> dict[str, Any]:
    """OCR sampled frames and classify hateful/inciting text with local rules."""
    status = availability(tesseract)
    report: dict[str, Any] = {
        "available": status["available"],
        "binary": status["binary"],
        "status": "ready" if status["available"] else "unavailable",
        "reason": status["reason"],
        "language": language,
        "frames": [],
        "text": "",
        "matches": [],
        "semantic": {"action": "allow"},
        "action": "allow",
        "score": 0,
    }
    if not status["available"]:
        return report

    duration = _duration(video_path, ffprobe)
    texts = []
    for seconds in _sample_times(duration, frames):
        frame = {"seconds": round(seconds, 3), "text": "", "error": None}
        try:
            png = _frame_png(video_path, seconds, ffmpeg)
            try:
                text = _recognize(png, status["binary"], language)
            except Exception:
                text = _recognize(png, status["binary"], "eng") if language != "eng" else ""
            frame["text"] = text
            if text:
                texts.append(text)
        except Exception as exc:
            frame["error"] = str(exc)[:300]
        report["frames"].append(frame)

    text = " ".join(texts).strip()
    matches = find_matches(text, min_severity="low") if text else []
    semantic = analyze_text(text) if text else {"action": "allow"}
    severity_score = {"low": 25, "medium": 60, "high": 100}
    score = max([severity_score.get(item.get("severity", "high"), 100) for item in matches] or [0])
    score = max(score, {"allow": 0, "review": 70, "block": 100}.get(semantic.get("action"), 0))
    report.update({
        "status": "scanned",
        "text": text,
        "matches": matches,
        "semantic": semantic,
        "score": score,
        "action": "block" if score >= 100 else ("review" if score else "allow"),
    })
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="OCR safety scan for rendered video text")
    parser.add_argument("--video", required=True)
    parser.add_argument("--frames", type=int, default=4)
    parser.add_argument("--language", default="ara+eng")
    args = parser.parse_args(argv)
    report = analyze_video(args.video, frames=args.frames, language=args.language)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report.get("action") == "block" else 0


if __name__ == "__main__":
    raise SystemExit(main())
