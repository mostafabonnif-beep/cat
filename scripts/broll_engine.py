"""Optional B-roll discovery and compositing for ViralCutter.

The engine is deliberately opt-in: without a provider key or a local asset it
only creates a deterministic plan and never performs network requests. Pexels
results retain attribution metadata so projects can show a credit link.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

try:
    import requests
except ImportError:  # pragma: no cover - requests is optional at import time
    requests = None

PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/v1/videos/search"
DEFAULT_TIMEOUT = 20
MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "how", "in", "is", "it", "of", "on", "or", "that", "the", "this",
    "to", "was", "we", "with", "you", "your", "عن", "على", "الى", "إلى",
    "في", "ما", "من", "هو", "هي", "هذا", "هذه", "ذلك", "تلك", "مع", "و", "يا",
}


def extract_keywords(text: str, max_terms: int = 3) -> list[str]:
    """Return stable, human-readable query terms from Arabic/Latin text."""
    tokens = re.findall(r"[\w\u0600-\u06ff]{3,}", str(text or "").lower(), re.UNICODE)
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for index, token in enumerate(tokens):
        if token.startswith("و") and len(token) > 4:
            token = token[1:]
        if token in _STOPWORDS or token.isdigit():
            continue
        counts[token] = counts.get(token, 0) + 1
        first_seen.setdefault(token, index)
    ranked = sorted(counts, key=lambda word: (-counts[word], first_seen[word], word))
    return ranked[: max(1, int(max_terms))]


def _query_from_segment(segment: dict[str, Any], language: str = "en") -> str:
    text = segment.get("text") or segment.get("caption") or segment.get("title") or ""
    keywords = extract_keywords(text)
    if keywords:
        return " ".join(keywords)
    return "abstract background" if language.startswith("en") else "خلفية توضيحية"


def build_broll_plan(segments: list[dict[str, Any]], language: str = "en") -> list[dict[str, Any]]:
    """Create a deterministic B-roll plan from timed transcript segments."""
    plan = []
    for index, segment in enumerate(segments or []):
        try:
            start = max(0.0, float(segment.get("start", 0)))
            end = max(start, float(segment.get("end", start)))
        except (TypeError, ValueError):
            continue
        if end - start < 0.5:
            continue
        query = _query_from_segment(segment, language=language)
        plan.append({
            "index": index,
            "start": start,
            "end": end,
            "query": query,
            "keywords": extract_keywords(segment.get("text", "")),
            "status": "needs_asset",
            "asset": None,
        })
    return plan


def _response_json(response: Any) -> dict[str, Any]:
    if hasattr(response, "raise_for_status"):
        response.raise_for_status()
    if hasattr(response, "json"):
        data = response.json()
    else:
        data = json.loads(response.text)
    if not isinstance(data, dict):
        raise ValueError("provider response must be a JSON object")
    return data


def search_pexels_videos(query: str, api_key: str, *, orientation: str = "portrait",
                         per_page: int = 5, page: int = 1,
                         timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Search Pexels videos without exposing the API key in logs or output."""
    if not api_key:
        return {"ok": False, "error": "missing_api_key", "items": []}
    if requests is None:
        return {"ok": False, "error": "requests_not_installed", "items": []}
    params = {
        "query": str(query).strip() or "abstract background",
        "orientation": orientation,
        "per_page": max(1, min(int(per_page), 80)),
        "page": max(1, int(page)),
    }
    try:
        response = requests.get(
            PEXELS_VIDEO_SEARCH_URL,
            headers={"Authorization": api_key},
            params=params,
            timeout=timeout,
        )
        data = _response_json(response)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "items": []}

    items = []
    for video in data.get("videos", []) or []:
        files = video.get("video_files", []) or []
        files = [item for item in files if item.get("link")]
        if not files:
            continue
        selected = min(files, key=lambda item: abs(float(item.get("width", 1080)) - 1080))
        user = video.get("user", {}) or {}
        items.append({
            "id": video.get("id"),
            "url": video.get("url"),
            "duration": video.get("duration"),
            "width": selected.get("width"),
            "height": selected.get("height"),
            "download_url": selected.get("link"),
            "photographer": user.get("name"),
            "photographer_url": user.get("url"),
            "provider": "pexels",
        })
    return {
        "ok": True,
        "items": items,
        "page": data.get("page", page),
        "total_results": data.get("total_results", 0),
        "rate_limit": {
            "limit": response.headers.get("X-Ratelimit-Limit"),
            "remaining": response.headers.get("X-Ratelimit-Remaining"),
            "reset": response.headers.get("X-Ratelimit-Reset"),
        },
    }


def download_asset(url: str, output_path: str, *, timeout: int = DEFAULT_TIMEOUT,
                   max_bytes: int = MAX_DOWNLOAD_BYTES) -> dict[str, Any]:
    """Download one HTTPS/HTTP asset with a size cap and atomic replacement."""
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {"ok": False, "error": "invalid_asset_url"}
    if requests is None:
        return {"ok": False, "error": "requests_not_installed"}
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".download")
    total = 0
    try:
        with requests.get(url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > max_bytes:
                return {"ok": False, "error": "asset_too_large"}
            with temp.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        return {"ok": False, "error": "asset_too_large"}
                    handle.write(chunk)
        os.replace(temp, target)
        return {"ok": True, "path": str(target), "bytes": total}
    except Exception as exc:
        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass
        return {"ok": False, "error": str(exc)}


def overlay_broll(video_path: str, broll_path: str, output_path: str, *, start: float = 0.0,
                  end: Optional[float] = None, opacity: float = 0.28,
                  ffmpeg: str = "ffmpeg", dry_run: bool = False) -> dict[str, Any]:
    """Overlay B-roll with a low-opacity portrait crop during a time window."""
    if not os.path.exists(video_path) or not os.path.exists(broll_path):
        return {"ok": False, "error": "video_or_broll_missing"}
    opacity = max(0.05, min(float(opacity), 0.85))
    enable = f"between(t,{max(0.0, float(start))},{float(end)})" if end is not None else f"gte(t,{max(0.0, float(start))})"
    filter_complex = (
        f"[1:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,format=rgba,colorchannelmixer=aa={opacity}[broll];"
        f"[0:v][broll]overlay=0:0:enable='{enable}':eof_action=pass[v]"
    )
    temp = output_path + ".broll_tmp.mp4"
    cmd = [ffmpeg, "-y", "-i", video_path, "-stream_loop", "-1", "-i", broll_path,
           "-filter_complex", filter_complex, "-map", "[v]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-c:a", "copy", "-shortest", "-movflags", "+faststart", temp]
    if dry_run:
        return {"ok": True, "dry_run": True, "cmd": cmd, "output": output_path}
    if shutil.which(ffmpeg) is None:
        return {"ok": False, "error": "ffmpeg_not_found"}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            return {"ok": False, "error": (proc.stderr or proc.stdout)[-1000:]}
        os.replace(temp, output_path)
        return {"ok": True, "output": output_path}
    except Exception as exc:
        try:
            os.remove(temp)
        except OSError:
            pass
        return {"ok": False, "error": str(exc)}


def save_plan(plan: list[dict[str, Any]], path: str) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    temp = str(path) + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(plan, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    return path
