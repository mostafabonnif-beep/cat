"""Transcript-timed automatic sound effects.

The feature is local and opt-in: it looks for user-provided assets in an SFX
directory and never downloads audio. Missing assets result in a clean skip.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

SFX_EXTENSIONS = (".wav", ".mp3", ".m4a", ".ogg")
EVENT_TYPES = {
    "wow": "pop", "amazing": "pop", "shocking": "impact", "insane": "impact",
    "secret": "whoosh", "look": "whoosh", "watch": "whoosh", "stop": "impact",
    "danger": "impact", "مفاجأة": "pop", "مهم": "pop", "شاهد": "whoosh",
    "سر": "whoosh", "خطير": "impact", "توقف": "impact",
}


def plan_sfx(words: list[dict[str, Any]], *, max_events: int = 8,
             min_gap: float = 0.7) -> list[dict[str, Any]]:
    """Build a de-duplicated list of timed SFX events."""
    events = []
    for word in words or []:
        text = str(word.get("word", "")).lower()
        text = re.sub(r"[\\W_]+", "", text, flags=re.UNICODE)
        effect = EVENT_TYPES.get(text)
        if not effect:
            continue
        try:
            start = max(0.0, float(word["start"]))
        except (KeyError, TypeError, ValueError):
            continue
        if events and start - events[-1]["start"] < min_gap:
            continue
        events.append({"start": round(start, 3), "effect": effect, "word": text})
        if len(events) >= max(1, int(max_events)):
            break
    return events


def find_asset(sfx_dir: Optional[str], effect: str) -> Optional[str]:
    if not sfx_dir:
        return None
    root = Path(sfx_dir)
    for extension in SFX_EXTENSIONS:
        candidate = root / f"{effect}{extension}"
        if candidate.exists():
            return str(candidate)
    return None


def apply_auto_sfx(video_path: str, output_path: str, events: list[dict[str, Any]],
                   sfx_dir: Optional[str], *, volume: float = 0.22,
                   ffmpeg: str = "ffmpeg", dry_run: bool = False) -> dict[str, Any]:
    """Mix available SFX assets into the original audio at word timestamps."""
    if not os.path.exists(video_path):
        return {"ok": False, "error": "video_missing", "events": []}
    volume = max(0.02, min(float(volume), 1.0))
    selected = []
    missing = []
    for event in events or []:
        asset = find_asset(sfx_dir, event.get("effect", ""))
        if asset:
            selected.append((event, asset))
        else:
            missing.append(event.get("effect"))
    if not selected:
        if video_path != output_path:
            shutil.copy2(video_path, output_path)
        return {"ok": True, "count": 0, "events": [], "missing_assets": sorted(set(missing))}

    inputs = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", video_path]
    for _event, asset in selected:
        inputs.extend(["-i", asset])
    filters = []
    mix_labels = ["0:a"]
    for index, (event, _asset) in enumerate(selected, start=1):
        delay = max(0, int(float(event.get("start", 0)) * 1000))
        label = f"sfx{index}"
        filters.append(
            f"[{index}:a]adelay={delay}|{delay},volume={volume:.3f}[{label}]"
        )
        mix_labels.append(f"[{label}]")
    filters.append(
        "{}amix=inputs={}:duration=first:dropout_transition=2[aout]".format(
            "".join(f"[{label}]" for label in mix_labels), len(mix_labels),
        )
    )
    command = inputs + ["-filter_complex", ";".join(filters), "-map", "0:v:0",
                        "-map", "[aout]", "-c:v", "copy", "-c:a", "aac",
                        "-b:a", "192k", "-movflags", "+faststart", output_path]
    if dry_run:
        return {"ok": True, "dry_run": True, "count": len(selected),
                "events": [event for event, _asset in selected], "cmd": command}
    if shutil.which(ffmpeg) is None:
        return {"ok": False, "error": "ffmpeg_not_found", "events": []}
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=900)
        return {"ok": True, "count": len(selected),
                "events": [event for event, _asset in selected],
                "missing_assets": sorted(set(missing)), "output": output_path}
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        if os.path.exists(video_path) and video_path != output_path:
            shutil.copy2(video_path, output_path)
        return {"ok": False, "error": str(exc), "events": []}


def process_file(video_path: str, words: list[dict[str, Any]], output_path: str,
                 sfx_dir: Optional[str], *, volume: float = 0.22,
                 max_events: int = 8) -> dict[str, Any]:
    events = plan_sfx(words, max_events=max_events)
    report = apply_auto_sfx(video_path, output_path, events, sfx_dir, volume=volume)
    report["video"] = os.path.basename(video_path)
    return report
