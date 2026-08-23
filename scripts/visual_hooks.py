"""Automatic visual hooks for short-form retention.

This stage is intentionally subtle: it adds a short brightness/contrast lift
and a thin accent frame at strong transcript moments. Punch-zoom remains a
separate stage, so users can enable either effect independently.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, Optional

HOOK_WORDS = {
    "wow", "stop", "wait", "look", "watch", "listen", "secret", "truth",
    "never", "always", "danger", "breaking", "shocking", "amazing", "insane",
    "مهم", "توقف", "انتظر", "شاهد", "اسمع", "سر", "الحقيقة", "خطير", "مفاجأة",
    "لا", "أبداً", "دائما", "دائمًا", "حصري", "تحذير",
}


def _word_text(word: dict[str, Any]) -> str:
    text = str(word.get("word", word.get("text", ""))).lower()
    return re.sub(r"[\\W_]+", "", text, flags=re.UNICODE)


def load_words(path: Optional[str]) -> list[dict[str, Any]]:
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    words = []
    for segment in data.get("segments", []) if isinstance(data, dict) else []:
        for word in segment.get("words", []) or []:
            try:
                start, end = float(word["start"]), float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if end > start:
                words.append({"word": _word_text(word), "start": start, "end": end})
    return sorted(words, key=lambda item: item["start"])


def plan_visual_hooks(words: list[dict[str, Any]], *, max_hooks: int = 8,
                      min_gap: float = 1.2, opening_window: float = 2.5) -> list[dict[str, Any]]:
    """Return scored hook windows from word timings."""
    candidates = []
    for index, word in enumerate(words or []):
        text = _word_text(word)
        score = 0.0
        reasons = []
        if text in HOOK_WORDS:
            score += 3.0
            reasons.append("hook_word")
        if word["start"] <= opening_window:
            score += 1.5
            reasons.append("opening")
        if len(text) >= 8:
            score += 0.5
            reasons.append("long_word")
        if score <= 0:
            continue
        start = max(0.0, word["start"] - 0.08)
        end = word["end"] + 0.32
        candidates.append({
            "start": round(start, 3), "end": round(end, 3),
            "score": round(score, 2), "word": text,
            "reason": reasons, "word_index": index,
        })
    candidates.sort(key=lambda item: (-item["score"], item["start"]))
    selected = []
    for candidate in candidates:
        if any(candidate["start"] < old["end"] + min_gap and candidate["end"] > old["start"] for old in selected):
            continue
        selected.append(candidate)
        if len(selected) >= max(1, int(max_hooks)):
            break
    return sorted(selected, key=lambda item: item["start"])


def _filter_for_hooks(hooks: list[dict[str, Any]], accent: str = "0x00d9ff") -> str:
    parts = []
    for hook in hooks:
        start, end = hook["start"], hook["end"]
        parts.append(
            "eq=brightness=0.035:contrast=1.04:enable='between(t,{},{})'".format(start, end)
        )
        parts.append(
            "drawbox=x=0:y=0:w=iw:h=ih:color={}:t=14:enable='between(t,{},{})'".format(
                accent, start, end,
            )
        )
    return ",".join(parts)


def apply_visual_hooks(video_path: str, out_path: str, hooks: list[dict[str, Any]],
                       *, accent: str = "0x00d9ff", ffmpeg: str = "ffmpeg",
                       dry_run: bool = False) -> dict[str, Any]:
    if not os.path.exists(video_path):
        return {"ok": False, "error": "video_missing", "hooks": hooks or []}
    if not hooks:
        if video_path != out_path:
            shutil.copy2(video_path, out_path)
        return {"ok": True, "count": 0, "hooks": []}
    vf = _filter_for_hooks(hooks, accent=accent)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", video_path,
           "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
           "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", out_path]
    if dry_run:
        return {"ok": True, "dry_run": True, "count": len(hooks), "hooks": hooks, "cmd": cmd}
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=900)
        return {"ok": True, "count": len(hooks), "hooks": hooks, "output": out_path}
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        if os.path.exists(video_path) and video_path != out_path:
            shutil.copy2(video_path, out_path)
        return {"ok": False, "count": 0, "hooks": hooks, "error": str(exc)}


def process_file(video_path: str, subs_json: Optional[str] = None, out_path: Optional[str] = None,
                 *, max_hooks: int = 8, accent: str = "0x00d9ff") -> dict[str, Any]:
    out_path = out_path or video_path
    words = load_words(subs_json)
    hooks = plan_visual_hooks(words, max_hooks=max_hooks)
    report = apply_visual_hooks(video_path, out_path, hooks, accent=accent)
    report["video"] = os.path.basename(video_path)
    return report
