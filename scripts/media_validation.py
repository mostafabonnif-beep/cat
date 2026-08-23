# -*- coding: utf-8 -*-
"""Lightweight ffprobe validation for rendered ViralCutter media."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

# Prefer the latest rendered layer. Intermediate cuts may intentionally be
# horizontal and must not fail validation for a final 9:16 export.
OUTPUT_DIRS = ("burned_sub", "reframed", "final_polished", "final", "cuts")


def probe_media(path, ffprobe=None):
    """Return normalized ffprobe data or a structured error."""
    path = os.path.abspath(str(path))
    ffprobe = ffprobe or shutil.which("ffprobe") or "ffprobe"
    if not os.path.isfile(path):
        return {"ok": False, "path": path, "error": "file not found"}
    cmd = [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_entries", "format=duration,size:stream=index,codec_type,codec_name,width,height,r_frame_rate",
        path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "path": path, "error": str(exc)}
    if proc.returncode != 0:
        return {"ok": False, "path": path, "error": (proc.stderr or "ffprobe failed")[-1000:]}
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        return {"ok": False, "path": path, "error": "invalid ffprobe JSON: {}".format(exc)}
    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {
        "ok": True,
        "path": path,
        "duration": duration,
        "size": int(fmt.get("size") or os.path.getsize(path)),
        "video": video,
        "audio": audio,
        "streams": streams,
    }


def _aspect_ratio_value(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        left, right = str(value).split(":", 1)
        return float(left) / float(right)
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def validate_media_file(path, min_duration=0.05, require_audio=False, expected_aspect=None, aspect_tolerance=0.03):
    report = probe_media(path)
    if not report.get("ok"):
        return report
    errors = []
    if report.get("size", 0) <= 0:
        errors.append("empty file")
    if not report.get("video"):
        errors.append("missing video stream")
    if report.get("duration", 0) < float(min_duration):
        errors.append("duration too short")
    if require_audio and not report.get("audio"):
        errors.append("missing audio stream")
    expected = _aspect_ratio_value(expected_aspect)
    video = report.get("video") or {}
    if expected and video.get("width") and video.get("height"):
        actual = float(video["width"]) / float(video["height"])
        if abs(actual - expected) > float(aspect_tolerance):
            errors.append("unexpected aspect ratio: {:.3f} (expected {:.3f})".format(actual, expected))
    report["ok"] = not errors
    report["errors"] = errors
    return report


def output_files(project_folder):
    """List rendered MP4 files while ignoring input, previews, and temp files."""
    project_folder = os.path.abspath(str(project_folder))
    for relative_dir in OUTPUT_DIRS:
        directory = os.path.join(project_folder, relative_dir)
        if not os.path.isdir(directory):
            continue
        found = []
        for root, _dirs, files in os.walk(directory):
            for filename in files:
                if not filename.lower().endswith((".mp4", ".mov", ".mkv")):
                    continue
                if filename.lower().startswith(("temp_", "preview_")):
                    continue
                found.append(os.path.join(root, filename))
        if found:
            return sorted(set(found))
    return []


def validate_project_outputs(project_folder, *, require_outputs=True, require_audio=False, expected_aspect=None):
    files = output_files(project_folder)
    if not files:
        return {"ok": not require_outputs, "outputs": [], "errors": ["no rendered media found"]}
    reports = [validate_media_file(path, require_audio=require_audio, expected_aspect=expected_aspect) for path in files]
    errors = []
    for report in reports:
        if not report.get("ok"):
            errors.append("{}: {}".format(os.path.basename(report["path"]), "; ".join(report.get("errors", [report.get("error", "invalid media")]))))
    return {"ok": not errors, "outputs": reports, "errors": errors}
