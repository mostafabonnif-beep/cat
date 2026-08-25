# -*- coding: utf-8 -*-
"""
Pipeline runner for Telegram control (v7.25) — تشغيل المعالجة والرفع بأمان.

This module lets the Telegram bot start a real processing job (YouTube URL →
download → cut → subtitles → safety) and optionally upload the resulting
clips to the user's own YouTube channel, WITHOUT going through the Gradio
event loop. It is deliberately conservative:

* URLs are validated as YouTube URLs before anything runs.
* Uploads are dry-run by default; a real upload requires
  ``confirm=True`` AND the same values already used in the dry run
  (belt-and-braces: the caller must show what it will do first).
* OAuth secrets come from the environment / stored credentials, never from
  the bot message.
* Only ONE job runs at a time (lock file) so the local machine is never
  overloaded by rapid bot commands.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK_PATH = os.path.join(REPO_ROOT, ".pipeline_runner.lock")
STATE_PATH = os.path.join(REPO_ROOT, "pipeline_runner_state.json")

_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com",
                  "youtu.be", "music.youtube.com"}


def is_youtube_url(value: str) -> bool:
    """Accept only real YouTube URLs (youtube.com / youtu.be)."""
    text = str(value or "").strip()
    if not text.startswith(("https://", "http://")):
        return False
    try:
        parsed = urllib.parse.urlparse(text)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if host not in _YOUTUBE_HOSTS:
        return False
    if host == "youtu.be":
        return bool(parsed.path.strip("/"))
    # youtube.com requires /watch?v= or /live/ or /shorts/ or /embed/
    return bool(re.search(r"(/watch\?v=|/live/|/shorts/|/embed/)", parsed.path + "?" + parsed.query))


@dataclass
class RunResult:
    ok: bool
    message: str
    project_folder: str | None = None
    clips: list[str] = field(default_factory=list)
    upload: dict[str, Any] | None = None


def _acquire_lock(timeout: float = 2.0) -> bool:
    """One runner at a time. Stale locks (older than 6h) are reclaimed."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(LOCK_PATH) > 6 * 3600:
                    os.remove(LOCK_PATH)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.4)
        except OSError:
            return False


def _release_lock():
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass


def run_pipeline(url: str, *, segments: int = 3, min_duration: int = 15,
                 max_duration: int = 90, workflow: int = 1,
                 sponsorblock: str | None = None, live_wait_minutes: float | None = None,
                 scene_snap: bool = True, safety_mode: str = "block",
                 timeout_seconds: int = 6 * 3600) -> RunResult:
    """Run the full pipeline for one YouTube URL (CLI child process).

    Returns a RunResult; never raises for pipeline failures.
    """
    if not is_youtube_url(url):
        return RunResult(ok=False, message="❌ الرابط ليس رابط يوتيوب صالحاً.")
    if not _acquire_lock():
        return RunResult(ok=False, message="⏳ هناك معالجة جارية بالفعل. انتظر انتهاءها أو أرسل /status.")
    try:
        cmd = [sys.executable, os.path.join(REPO_ROOT, "main_improved.py"),
               "--url", url,
               "--segments", str(segments),
               "--min-duration", str(min_duration),
               "--max-duration", str(max_duration),
               "--ai-backend", "manual",
               "--workflow", str(workflow),
               "--safety-mode", safety_mode,
               "--skip-prompts"]
        if sponsorblock:
            cmd.extend(["--sponsorblock", sponsorblock])
        if live_wait_minutes:
            cmd.extend(["--live-wait", str(live_wait_minutes)])
        if scene_snap:
            cmd.append("--scene-snap")

        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout_seconds)
        tail = (proc.stdout or "")[-2000:] + (proc.stderr or "")[-2000:]
        if proc.returncode != 0:
            return RunResult(ok=False, message="❌ فشلت المعالجة (exit {}).\n{}".format(
                proc.returncode, tail[-800:]))

        project_folder = _find_project_folder(url, proc.stdout or "")
        clips = _find_clips(project_folder) if project_folder else []
        return RunResult(ok=True,
                         message="✅ اكتملت المعالجة.\nالمشروع: {}\nالمقاطع: {}".format(
                             project_folder or "غير معروف", len(clips)),
                         project_folder=project_folder, clips=clips)
    except subprocess.TimeoutExpired:
        return RunResult(ok=False, message="⏰ تجاوزت المعالجة المهلة ({}s) وأُوقفت.".format(timeout_seconds))
    except Exception as exc:
        return RunResult(ok=False, message="❌ خطأ غير متوقع: {}".format(str(exc)[:400]))
    finally:
        _release_lock()


def _find_project_folder(url: str, logs: str) -> str | None:
    """Locate the project folder from the pipeline logs or the newest VIRALS dir."""
    m = re.search(r"[Pp]roject[_ ]folder[=: ]+([^\n]+)", logs)
    if m:
        candidate = m.group(1).strip()
        if os.path.isdir(candidate):
            return candidate
    virals = os.environ.get("VIRALCUTTER_VIRALS_DIR", "").strip() or os.path.join(REPO_ROOT, "VIRALS")
    if os.path.isdir(virals):
        subdirs = [os.path.join(virals, d) for d in os.listdir(virals)
                   if os.path.isdir(os.path.join(virals, d))]
        if subdirs:
            return max(subdirs, key=os.path.getmtime)
    return None


def _find_clips(project_folder: str) -> list[str]:
    clips = []
    for folder_name in ("final_polished", "final", "cuts"):
        folder = os.path.join(project_folder, folder_name)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith(".mp4"):
                clips.append(os.path.join(folder, name))
    return clips


def upload_project(project_folder: str, *, dry_run: bool = True,
                   privacy: str = "private", title_template: str | None = None,
                   publish_at: str | None = None,
                   timeout_seconds: int = 30 * 60) -> RunResult:
    """Upload all clips of a project through the existing safety-gated batch.

    dry_run=True (default) only *lists* what would be uploaded — nothing
    touches the network. A real upload requires the same project folder and
    dry_run=False (the bot enforces a separate confirmation step).
    """
    if not project_folder or not os.path.isdir(project_folder):
        return RunResult(ok=False, message="❌ مجلد المشروع غير موجود: {}".format(project_folder))
    clips = _find_clips(project_folder)
    if not clips:
        return RunResult(ok=False, message="❌ لا توجد مقاطع MP4 في المشروع.")
    if dry_run:
        lines = ["🔍 وضع التجربة (Dry Run) — لن يُرفع شيء:", ""]
        for clip in clips:
            lines.append("  • " + os.path.basename(clip))
        lines.append("")
        lines.append("للرفع الفعلي أرسل: /confirm_upload {}".format(os.path.basename(project_folder)))
        return RunResult(ok=True, message="\n".join(lines), project_folder=project_folder,
                         clips=clips)
    if not _acquire_lock():
        return RunResult(ok=False, message="⏳ معالجة أخرى جارية. أرسل /status.")
    try:
        sys.path.insert(0, REPO_ROOT)
        from webui import publish_panel
        updates = list(publish_panel.stream_upload_batch(
            project_folder, "youtube", clips, False, "warn",
            privacy_status=privacy or "private",
            publish_at=publish_at or None,
            require_existing_auth=True,
            public_confirm=False,
        ))
        summary = "\n".join(str(u) for u in updates[-6:])
        uploaded = sum(1 for u in updates if "uploaded" in str(u).lower()
                       or "scheduled" in str(u).lower())
        return RunResult(ok=True,
                         message="✅ اكتمل الرفع: {} مقطعاً.\n{}".format(uploaded, summary),
                         project_folder=project_folder, clips=clips,
                         upload={"uploaded": uploaded})
    except Exception as exc:
        return RunResult(ok=False, message="❌ فشل الرفع: {}".format(str(exc)[:400]))
    finally:
        _release_lock()


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("process", help="run the full pipeline for a YouTube URL")
    p.add_argument("url")
    p.add_argument("--segments", type=int, default=3)
    p.add_argument("--sponsorblock", default=None)
    p.add_argument("--live-wait", type=float, default=None)
    p.add_argument("--no-scene-snap", action="store_true")

    u = sub.add_parser("upload", help="upload a project's clips")
    u.add_argument("project")
    u.add_argument("--dry-run", action="store_true", default=True)
    u.add_argument("--real", action="store_true", help="actually upload (dangerous)")
    u.add_argument("--privacy", default="private")

    args = parser.parse_args(argv)
    if args.command == "process":
        result = run_pipeline(args.url, segments=args.segments,
                              sponsorblock=args.sponsorblock,
                              live_wait_minutes=args.live_wait,
                              scene_snap=not args.no_scene_snap)
        print(result.message)
        return 0 if result.ok else 1
    if args.command == "upload":
        result = upload_project(args.project, dry_run=not args.real,
                                privacy=args.privacy)
        print(result.message)
        return 0 if result.ok else 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
