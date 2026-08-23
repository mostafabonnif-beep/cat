"""Safe project backup and restore helpers.

Backups are ZIP files created without OAuth tokens, client secrets, caches or
Python bytecode. Restore always creates a new sibling project and never
silently overwrites an existing project.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

EXCLUDED_NAMES = {
    "token.json", "client_secrets.json", "credentials.json",
    ".batch_queue.json", ".DS_Store",
}
EXCLUDED_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".runtime-tmp", ".installer-tmp"}
MEDIA_SUFFIXES = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".mp3", ".wav", ".flac", ".aac"}


def _safe_backup_name(project_path):
    name = Path(project_path).name or "project"
    safe = "".join(ch if ch.isalnum() or ch in " _-" else "_" for ch in name).strip() or "project"
    return safe[:80]


def _should_include(path, include_media):
    path = Path(path)
    if path.name in EXCLUDED_NAMES or path.suffix.lower() in {".pyc", ".tmp"}:
        return False
    if not include_media and path.suffix.lower() in MEDIA_SUFFIXES:
        return False
    return True


def create_backup(project_path, destination_dir=None, include_media=False):
    """Create a secret-free ZIP backup and return its absolute path."""
    project = Path(project_path).resolve()
    if not project.is_dir():
        raise FileNotFoundError("project folder not found: {}".format(project))
    destination = Path(destination_dir or project.parent / ".backups").expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    target = destination / "{}_{}.zip".format(_safe_backup_name(project), stamp)
    fd, temp_name = tempfile.mkstemp(prefix=".backup-", suffix=".tmp", dir=str(destination))
    os.close(fd)
    manifest = {
        "format": "oussama-cutter-backup",
        "version": 1,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "project_name": project.name,
        "include_media": bool(include_media),
    }
    count = 0
    try:
        with zipfile.ZipFile(temp_name, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr("backup_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for root, dirs, files in os.walk(project):
                dirs[:] = [name for name in dirs if name not in EXCLUDED_DIRS and not name.startswith(".")]
                for filename in files:
                    path = Path(root) / filename
                    if not _should_include(path, include_media):
                        continue
                    relative = path.relative_to(project).as_posix()
                    archive.write(path, "project/" + relative)
                    count += 1
        os.replace(temp_name, target)
        return {"path": str(target), "files": count, "include_media": bool(include_media)}
    finally:
        if os.path.exists(temp_name):
            try:
                os.remove(temp_name)
            except OSError:
                pass


def inspect_backup(backup_path):
    """Validate a backup and return its manifest and member count."""
    backup = Path(backup_path).expanduser().resolve()
    if not zipfile.is_zipfile(backup):
        raise ValueError("not a valid OUSSAMA Cutter ZIP backup")
    with zipfile.ZipFile(backup, "r") as archive:
        names = archive.namelist()
        if "backup_manifest.json" not in names:
            raise ValueError("backup manifest is missing")
        manifest = json.loads(archive.read("backup_manifest.json").decode("utf-8"))
        if manifest.get("format") != "oussama-cutter-backup":
            raise ValueError("unsupported backup format")
        unsafe = [name for name in names if name.startswith("/") or ".." in Path(name).parts]
        if unsafe:
            raise ValueError("backup contains unsafe paths")
        return {"manifest": manifest, "files": len([name for name in names if name.startswith("project/")])}


def restore_backup(backup_path, destination_root):
    """Restore to a new, collision-safe project directory."""
    info = inspect_backup(backup_path)
    root = Path(destination_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    base = _safe_backup_name(info["manifest"].get("project_name") or "restored") + "_restored"
    target = root / base
    suffix = 1
    while target.exists():
        suffix += 1
        target = root / (base + "_{}".format(suffix))
    temp_target = Path(tempfile.mkdtemp(prefix=".restore-", dir=str(root)))
    try:
        with zipfile.ZipFile(Path(backup_path).expanduser().resolve(), "r") as archive:
            for name in archive.namelist():
                if not name.startswith("project/"):
                    continue
                relative = Path(name[len("project/"):])
                if not relative.parts or ".." in relative.parts:
                    raise ValueError("backup contains unsafe paths")
                out = temp_target.joinpath(*relative.parts).resolve()
                if os.path.commonpath([str(temp_target.resolve()), str(out)]) != str(temp_target.resolve()):
                    raise ValueError("backup path escapes destination")
                out.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, open(out, "wb") as target_file:
                    shutil.copyfileobj(source, target_file)
        os.replace(temp_target, target)
        return str(target)
    finally:
        if temp_target.exists():
            shutil.rmtree(temp_target, ignore_errors=True)
