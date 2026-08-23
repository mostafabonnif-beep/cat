# -*- coding: utf-8 -*-
"""Durable project metadata and event storage for ViralCutter.

A project is still a normal directory containing the existing ViralCutter
artifacts. This module adds a small, human-readable manifest and JSONL event
log without changing the legacy file layout, so old projects remain usable.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import tempfile
import time
import uuid

MANIFEST_NAME = "project_manifest.json"
EVENTS_NAME = "project_events.jsonl"
SCHEMA_VERSION = 1


def _now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def safe_project_name(name, fallback="project"):
    """Return a filesystem-safe project folder name while preserving Unicode."""
    raw = str(name or "").strip().replace("\\", "_").replace("/", "_")
    raw = "".join(ch for ch in raw if ch.isprintable() and ch not in '<>:\"|?*')
    raw = re.sub(r"\s+", " ", raw).strip(" .")
    if raw.upper() in {"CON", "PRN", "AUX", "NUL"}:
        raw = "_" + raw
    return (raw[:96] or fallback).strip(" .") or fallback


def _atomic_write_json(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def safe_project_path(root, project_name):
    root = os.path.abspath(str(root))
    candidate = os.path.abspath(os.path.join(root, str(project_name or "")))
    try:
        inside = os.path.commonpath([root, candidate]) == root
    except ValueError:
        inside = False
    if not inside:
        raise ValueError("project path escapes the projects root")
    return candidate


def manifest_path(project_dir):
    return os.path.join(os.path.abspath(str(project_dir)), MANIFEST_NAME)


def events_path(project_dir):
    return os.path.join(os.path.abspath(str(project_dir)), EVENTS_NAME)


def resolve_project_input(project_dir):
    """Resolve a project's source video without creating a duplicate copy.

    New local uploads store their absolute source path in the manifest. Legacy
    projects remain compatible through ``input.mp4`` inside the project folder.
    The returned path is absolute and points to an existing regular file.
    """
    project_dir = os.path.abspath(str(project_dir))

    # Backward compatibility: old projects physically stored input.mp4 here.
    legacy_input = os.path.join(project_dir, "input.mp4")
    if os.path.isfile(legacy_input):
        return legacy_input

    manifest = load_manifest(project_dir, create=False) or {}
    source = manifest.get("source") or {}
    candidates = []
    if isinstance(source, dict):
        for key in ("path", "local_path", "original_path"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())

    for candidate in candidates:
        path = os.path.abspath(os.path.expanduser(candidate))
        if os.path.isfile(path):
            return path
    return None


def default_manifest(project_dir, *, name=None, source=None, settings=None):
    project_dir = os.path.abspath(str(project_dir))
    now = _now()
    return {
        "schema": SCHEMA_VERSION,
        "id": uuid.uuid5(uuid.NAMESPACE_URL, "viralcutter:" + project_dir).hex,
        "name": safe_project_name(name or os.path.basename(project_dir)),
        "path": project_dir,
        "source": source or {},
        "settings": dict(settings or {}),
        "status": "created",
        "created_at": now,
        "updated_at": now,
        "outputs": [],
        "last_error": None,
    }


def load_manifest(project_dir, create=False, **kwargs):
    path = manifest_path(project_dir)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("manifest must be an object")
        base = default_manifest(project_dir, **kwargs)
        base.update(data)
        base["path"] = os.path.abspath(str(project_dir))
        return base
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        if not create:
            return None
        manifest = default_manifest(project_dir, **kwargs)
        _atomic_write_json(path, manifest)
        return manifest


def save_manifest(project_dir, manifest):
    data = dict(manifest or {})
    data.setdefault("schema", SCHEMA_VERSION)
    data.setdefault("id", uuid.uuid5(uuid.NAMESPACE_URL, "viralcutter:" + os.path.abspath(str(project_dir))).hex)
    data["path"] = os.path.abspath(str(project_dir))
    data["updated_at"] = _now()
    _atomic_write_json(manifest_path(project_dir), data)
    return data


def update_manifest(project_dir, **changes):
    current = load_manifest(project_dir, create=True)
    for key, value in changes.items():
        if key in {"source", "settings"} and isinstance(value, dict):
            current[key] = {**(current.get(key) or {}), **value}
        else:
            current[key] = value
    return save_manifest(project_dir, current)


def create_project(base_dir, name, *, source=None, settings=None, exist_ok=False):
    base_dir = os.path.abspath(str(base_dir))
    project_name = safe_project_name(name)
    project_dir = os.path.join(base_dir, project_name)
    if os.path.exists(project_dir) and not exist_ok:
        suffix = time.strftime("%Y%m%d_%H%M%S")
        project_name = safe_project_name(project_name + "_" + suffix)
        project_dir = os.path.join(base_dir, project_name)
    os.makedirs(project_dir, exist_ok=True)
    manifest = load_manifest(project_dir, create=True, name=project_name, source=source, settings=settings)
    append_event(project_dir, "created", {"source": source or {}})
    return project_dir, manifest


def append_event(project_dir, event, details=None):
    record = {
        "at": _now(),
        "event": str(event),
        "details": dict(details or {}),
    }
    path = events_path(project_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return record


def read_events(project_dir, limit=200):
    path = events_path(project_dir)
    if not os.path.exists(path):
        return []
    records = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        records.append(value)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return records[-max(0, int(limit or 0)):]


def _has_rendered_files(folder):
    if not os.path.isdir(folder):
        return False
    try:
        return any(
            entry.is_file() and entry.name.lower().endswith(".mp4")
            for entry in os.scandir(folder)
        )
    except OSError:
        return False


def list_projects(root=None):
    """List valid project directories newest first, including legacy projects."""
    root = os.path.abspath(str(root))
    if not os.path.isdir(root):
        return []
    records = []
    try:
        entries = list(os.scandir(root))
    except OSError:
        return []
    for entry in entries:
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        manifest = load_manifest(entry.path)
        if manifest is None:
            manifest = default_manifest(entry.path)
            manifest["status"] = "legacy"
            manifest["created_at"] = _dt.datetime.fromtimestamp(
                entry.stat().st_ctime, tz=_dt.timezone.utc).isoformat(timespec="seconds")
            manifest["updated_at"] = _dt.datetime.fromtimestamp(
                entry.stat().st_mtime, tz=_dt.timezone.utc).isoformat(timespec="seconds")
        manifest["has_input"] = bool(resolve_project_input(entry.path))
        manifest["has_outputs"] = any(
            _has_rendered_files(os.path.join(entry.path, folder))
            for folder in ("cuts", "final", "final_polished", "burned_sub")
        )
        records.append(manifest)
    records.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return records


def project_summary(project_dir):
    manifest = load_manifest(project_dir, create=True)
    events = read_events(project_dir, limit=1)
    manifest["last_event"] = events[-1] if events else None
    return manifest
