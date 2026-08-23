# -*- coding: utf-8 -*-
"""
Batch processor (v7.20) — معالجة قائمة روابط كاملة بأمر واحد.

Reads a text file (one YouTube URL per line) and runs the full pipeline for
each: download (with optional live-wait), cut, subtitles, safety, and
optional auto-upload. Designed for headless / scheduled use:

    python scripts/batch_process.py urls.txt --segments 4 --min 15 --max 90
    python scripts/batch_process.py urls.txt --live-wait 360 --upload --privacy unlisted

Options mirror main_improved.py; anything not exposed here can be passed
through with --extra "arg=value" (repeated).

Exit code: 0 if all URLs succeeded, 1 if any failed (report printed either
way to batch_report.json).
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

REPORT_NAME = "batch_report.json"


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_urls(path):
    urls = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            url = raw.strip()
            if url and not url.startswith("#"):
                urls.append(url)
    return urls


def build_command(url, args):
    """Assemble the main_improved.py CLI command for one URL."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cmd = [sys.executable, os.path.join(root, "main_improved.py"),
           "--url", url,
           "--segments", str(args.segments),
           "--min-duration", str(args.min_duration),
           "--max-duration", str(args.max_duration),
           "--ai-backend", args.ai_backend,
           "--workflow", str(args.workflow),
           "--skip-prompts"]
    if args.viral:
        cmd.append("--viral")
    if args.themes:
        cmd.extend(["--themes", args.themes])
    if args.live_wait:
        cmd.extend(["--live-wait", str(args.live_wait)])
    if args.sponsorblock:
        cmd.extend(["--sponsorblock", args.sponsorblock])
    if args.quality:
        cmd.extend(["--video-quality", args.quality])
    if args.safety_mode:
        cmd.extend(["--safety-mode", args.safety_mode])
    for pair in args.extra or []:
        if "=" in pair:
            key, value = pair.split("=", 1)
            cmd.extend(["--" + key, value])
    return cmd


def upload_project(project_folder, args):
    """Reuse the WebUI publish pipeline to upload all clips of a project.

    Returns a dict {ok, uploaded, dry_run, detail}. Never raises.
    """
    try:
        from webui import publish_panel
    except Exception as exc:
        return {"ok": False, "uploaded": 0, "dry_run": args.dry_run,
                "detail": "publish_panel unavailable: {}".format(exc)}
    final_folders = [os.path.join(project_folder, name)
                     for name in ("final_polished", "final", "cuts")
                     if os.path.isdir(os.path.join(project_folder, name))]
    clips = []
    for folder in final_folders:
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith(".mp4"):
                clips.append(os.path.join(folder, name))
    if not clips:
        return {"ok": False, "uploaded": 0, "dry_run": args.dry_run,
                "detail": "no mp4 clips found in {}".format(project_folder)}
    try:
        updates = list(publish_panel.stream_upload_batch(
            project_folder, "youtube", clips, bool(args.dry_run), "warn",
            privacy_status=args.privacy or "private",
            require_existing_auth=not args.dry_run,
        ))
    except Exception as exc:
        return {"ok": False, "uploaded": 0, "dry_run": args.dry_run,
                "detail": "upload batch failed: {}".format(str(exc)[:300])}
    uploaded = sum(1 for u in updates if "uploaded" in str(u).lower()
                   or "scheduled" in str(u).lower())
    return {"ok": True, "uploaded": uploaded, "dry_run": args.dry_run,
            "detail": "; ".join(str(u) for u in updates[-5:])}


def process_url(url, args, index, total):
    print("\n" + "=" * 60)
    print("[batch] ({}/{}) {}".format(index + 1, total, url))
    print("=" * 60)
    cmd = build_command(url, args)
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = round(time.time() - started, 1)
    ok = proc.returncode == 0
    print("[batch] {} in {:.1f}s (exit {})".format(
        "OK" if ok else "FAILED", elapsed, proc.returncode))
    if not ok:
        tail = (proc.stdout or "")[-800:] + (proc.stderr or "")[-800:]
        print("[batch] last output:\n" + tail[-1200:])

    upload = None
    if ok and args.upload:
        # find the project folder: main_improved prints the project path, or
        # fall back to the newest VIRALS subfolder
        project_folder = None
        import re as _re
        m = _re.search(r"project[_ ]folder[=: ]+([^\n]+)", (proc.stdout or "")[-4000:])
        if m:
            candidate = m.group(1).strip()
            if os.path.isdir(candidate):
                project_folder = candidate
        if project_folder is None:
            virals = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "VIRALS")
            if os.path.isdir(virals):
                subdirs = [os.path.join(virals, d) for d in os.listdir(virals)
                           if os.path.isdir(os.path.join(virals, d))]
                if subdirs:
                    project_folder = max(subdirs, key=os.path.getmtime)
        if project_folder:
            print("[batch] uploading clips from {}".format(project_folder))
            upload = upload_project(project_folder, args)
            print("[batch] upload: {}".format(json.dumps(upload, ensure_ascii=False)))
        else:
            upload = {"ok": False, "detail": "project folder not found"}

    return {
        "url": url,
        "ok": ok,
        "exit_code": proc.returncode,
        "elapsed_seconds": elapsed,
        "started_at": _now(),
        "upload": upload,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls_file", help="text file with one URL per line (# = comment)")
    parser.add_argument("--segments", type=int, default=3)
    parser.add_argument("--min-duration", type=int, default=15)
    parser.add_argument("--max-duration", type=int, default=90)
    parser.add_argument("--ai-backend", default="manual",
                        choices=["manual", "gemini", "g4f", "local"])
    parser.add_argument("--workflow", type=int, default=1,
                        choices=[1, 2, 3],
                        help="1=full, 2=cut only, 3=subtitles only")
    parser.add_argument("--viral", action="store_true")
    parser.add_argument("--themes", default=None)
    parser.add_argument("--live-wait", type=float, default=None, metavar="MIN",
                        help="wait for live streams to end (minutes)")
    parser.add_argument("--sponsorblock", default=None,
                        help="sponsor/intro/outro/... or 'all'")
    parser.add_argument("--quality", default=None,
                        choices=[None, "best", "1080p", "720p", "480p"])
    parser.add_argument("--safety-mode", default="block",
                        choices=["block", "flag", "off"])
    parser.add_argument("--upload", action="store_true",
                        help="auto-upload after each project finishes")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --upload: simulate, never publish")
    parser.add_argument("--privacy", default=None,
                        choices=["private", "unlisted", "public"])
    parser.add_argument("--extra", action="append", default=[],
                        metavar="key=value",
                        help="pass extra CLI flags to main_improved.py (repeatable)")
    parser.add_argument("--stop-on-error", action="store_true",
                        help="abort the batch on the first failure")
    args = parser.parse_args(argv)

    urls = _read_urls(args.urls_file)
    if not urls:
        print("[batch] no URLs found in {}".format(args.urls_file))
        return 1
    print("[batch] {} URL(s) to process".format(len(urls)))

    results = []
    for index, url in enumerate(urls):
        result = process_url(url, args, index, len(urls))
        results.append(result)
        if not result["ok"] and args.stop_on_error:
            print("[batch] stopping on error (--stop-on-error)")
            break

    ok_count = sum(1 for r in results if r["ok"])
    report = {
        "generated_at": _now(),
        "total": len(results),
        "ok": ok_count,
        "failed": len(results) - ok_count,
        "results": results,
    }
    path = os.path.join(os.getcwd(), REPORT_NAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\n[batch] done: {}/{} OK — report: {}".format(ok_count, len(results), path))
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
