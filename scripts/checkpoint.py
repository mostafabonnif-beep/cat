# -*- coding: utf-8 -*-
"""Crash-safe, backward-compatible stage checkpoints for ViralCutter.

The legacy ``checkpoint.json`` shape (``stages: {name: true}``) remains valid.
The richer metadata records active stage, attempts, failures and resume history
without exposing API keys or changing the pipeline's public API.
"""

from __future__ import annotations

import json
import os
import tempfile
import time

CHECKPOINT_FILENAME = "checkpoint.json"
STAGES = [
    "download", "transcribe", "segments", "safety", "cut",
    "edit", "polish", "subtitles", "scorecard", "music_check", "done",
]


def checkpoint_path(project_folder):
    return os.path.join(project_folder, CHECKPOINT_FILENAME)


def _blank_checkpoint():
    return {
        "version": 2,
        "stages": {},
        "history": [],
        "active_stage": None,
        "last_error": None,
        "updated": None,
    }


def load_checkpoint(project_folder):
    path = checkpoint_path(project_folder)
    if not os.path.exists(path):
        return _blank_checkpoint()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("stages"), dict):
            return _blank_checkpoint()
        base = _blank_checkpoint()
        base.update(data)
        base["history"] = data.get("history") if isinstance(data.get("history"), list) else []
        return base
    except Exception:
        return _blank_checkpoint()


def _atomic_write(project_folder, data):
    os.makedirs(project_folder, exist_ok=True)
    path = checkpoint_path(project_folder)
    fd, tmp = tempfile.mkstemp(dir=project_folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def save_checkpoint(project_folder, stages, **metadata):
    """Persist stages and optional lifecycle metadata atomically.

    Existing callers may continue passing only ``stages``. Metadata is merged
    with the existing record so a mark operation does not erase history.
    """
    data = load_checkpoint(project_folder)
    data["version"] = max(2, int(data.get("version") or 0))
    data["stages"] = dict(stages or {})
    data.update(metadata)
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    data["history"] = list(data.get("history") or [])[-100:]
    try:
        _atomic_write(project_folder, data)
    except Exception:
        pass  # best-effort only; a broken checkpoint must not crash the pipeline


def _validate_stage(stage):
    if stage not in STAGES:
        raise ValueError("unknown checkpoint stage: {}".format(stage))


def _event(data, stage, status, **details):
    history = list(data.get("history") or [])
    event = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stage": stage,
        "status": status,
    }
    event.update({key: value for key, value in details.items() if value is not None})
    history.append(event)
    data["history"] = history[-100:]


def is_done(project_folder, stage):
    """True when the stage completed in a previous or current run."""
    _validate_stage(stage)
    return load_checkpoint(project_folder)["stages"].get(stage) is True


def mark_started(project_folder, stage):
    _validate_stage(stage)
    data = load_checkpoint(project_folder)
    attempts = sum(
        1 for item in data.get("history", [])
        if isinstance(item, dict) and item.get("stage") == stage
        and item.get("status") == "running"
    ) + 1
    data["stages"].pop(stage, None)
    data["active_stage"] = stage
    data["last_error"] = None
    _event(data, stage, "running", attempt=attempts)
    save_checkpoint(project_folder, data["stages"],
                    history=data["history"], active_stage=stage, last_error=None)


def mark_done(project_folder, stage):
    _validate_stage(stage)
    data = load_checkpoint(project_folder)
    data["stages"][stage] = True
    data["active_stage"] = None
    data["last_error"] = None
    _event(data, stage, "success")
    save_checkpoint(project_folder, data["stages"],
                    history=data["history"], active_stage=None, last_error=None)


def mark_failed(project_folder, stage, error):
    _validate_stage(stage)
    data = load_checkpoint(project_folder)
    data["stages"].pop(stage, None)
    data["active_stage"] = None
    data["last_error"] = {
        "stage": stage,
        "type": type(error).__name__,
        "message": str(error)[:4000],
    }
    _event(data, stage, "failed", error=str(error)[:1000])
    save_checkpoint(project_folder, data["stages"],
                    history=data["history"], active_stage=None,
                    last_error=data["last_error"])


def clear(project_folder, stage=None):
    """Remove one stage (or all) from the checkpoint."""
    if stage is not None:
        _validate_stage(stage)
    data = load_checkpoint(project_folder)
    if stage is None:
        data["stages"] = {}
        _event(data, "__all__", "cleared")
    else:
        data["stages"].pop(stage, None)
        _event(data, stage, "cleared")
    data["active_stage"] = None
    data["last_error"] = None
    save_checkpoint(project_folder, data["stages"],
                    history=data["history"], active_stage=None, last_error=None)


def list_pending(project_folder):
    """Stages not yet done, in canonical order."""
    done = load_checkpoint(project_folder)["stages"]
    return [s for s in STAGES if done.get(s) is not True]


class StageTracker:
    """Context manager that records lifecycle state around each stage."""

    def __init__(self, project_folder, enabled=True):
        self.project_folder = project_folder
        self.enabled = enabled
        self._completed_this_run = []
        self._skipped = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False  # never swallow exceptions

    def run(self, stage, fn, *args, force=False, **kwargs):
        """Run fn unless stage is done; record start, success, and failure."""
        _validate_stage(stage)
        if not self.enabled:
            return fn(*args, **kwargs)
        if not force and is_done(self.project_folder, stage):
            print("[checkpoint] skipping completed stage '{}'".format(stage))
            self._skipped.append(stage)
            return None
        mark_started(self.project_folder, stage)
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            mark_failed(self.project_folder, stage, exc)
            raise
        mark_done(self.project_folder, stage)
        self._completed_this_run.append(stage)
        return result

    def resume_info(self):
        data = load_checkpoint(self.project_folder)
        return {
            "skipped": list(self._skipped),
            "completed": list(self._completed_this_run),
            "pending": list_pending(self.project_folder),
            "active_stage": data.get("active_stage"),
            "last_error": data.get("last_error"),
            "history": data.get("history", [])[-20:],
        }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="OUSSAMA Cutter checkpoint tool.")
    parser.add_argument("--project", required=True, help="project folder")
    parser.add_argument("--status", action="store_true", help="show stage status")
    parser.add_argument("--mark", default=None, help="mark a stage done (e.g. cut)")
    parser.add_argument("--clear", nargs="?", const="__all__", default=None,
                        help="clear one stage or all (no value)")
    args = parser.parse_args()
    if args.status:
        data = load_checkpoint(args.project)
        for stage in STAGES:
            print("{}: {}".format(stage, "done" if is_done(args.project, stage) else "pending"))
        if data.get("active_stage"):
            print("active_stage: {}".format(data["active_stage"]))
        if data.get("last_error"):
            print("last_error: {}".format(data["last_error"].get("message", "")))
    elif args.mark:
        mark_done(args.project, args.mark)
        print("marked '{}' done".format(args.mark))
    elif args.clear is not None:
        clear(args.project, None if args.clear == "__all__" else args.clear)
        print("cleared checkpoint{}".format("" if args.clear == "__all__" else " for " + args.clear))
    else:
        print("pending: {}".format(", ".join(list_pending(args.project))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
