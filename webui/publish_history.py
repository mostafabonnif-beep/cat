"""Append-only, secret-free publish history for long-running projects."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone

HISTORY_NAME = "publish_history.jsonl"
SUCCESS_STATUSES = {"uploaded", "scheduled"}


def file_fingerprint(video_path, chunk_size=1024 * 1024):
    """Return a content fingerprint without exposing file contents in logs."""
    if not video_path or not os.path.isfile(video_path):
        return None
    digest = hashlib.sha256()
    try:
        with open(video_path, "rb") as stream:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        return None
    return "sha256:" + digest.hexdigest()


def find_success(project_path, *, platform, video_path):
    """Return a prior successful publish event for this exact file, if any."""
    fingerprint = file_fingerprint(video_path)
    if not fingerprint:
        return None
    for event in load(project_path, limit=1000):
        if (event.get("platform") == platform
                and event.get("file_fingerprint") == fingerprint
                and event.get("status") in SUCCESS_STATUSES):
            return event
    return None


def record(project_path, *, platform, video_path, title, result=None, error=None,
           privacy_status=None, publish_at=None):
    if not project_path or not os.path.isdir(project_path):
        return False
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "platform": platform,
        "video": os.path.basename(video_path or ""),
        "title": (title or "")[:200],
        "status": (result or {}).get("status") if isinstance(result, dict) else "failed" if error else "unknown",
        "privacy_status": privacy_status,
        "publish_at": publish_at,
        "file_fingerprint": file_fingerprint(video_path),
    }
    if isinstance(result, dict):
        for key in ("video_id", "url"):
            if result.get(key):
                event[key] = result[key]
    if error:
        event["error"] = str(error)[:1000]
    target = os.path.join(project_path, HISTORY_NAME)
    directory = os.path.dirname(target)
    fd, tmp_name = tempfile.mkstemp(prefix=".publish-history-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            if os.path.exists(target):
                with open(target, "r", encoding="utf-8", errors="replace") as old:
                    stream.write(old.read())
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, target)
        return True
    finally:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def load(project_path, limit=100):
    path = os.path.join(project_path, HISTORY_NAME)
    if not os.path.isfile(path):
        return []
    rows = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except OSError:
        return []
    return rows[-max(1, int(limit)) :]
