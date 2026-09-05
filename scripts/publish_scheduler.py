# -*- coding: utf-8 -*-
"""
Publish scheduler (v7.22) — جدولة النشر التلقائي للمقاطع القصيرة.

Combines three layers:

1. **Best-time calculator** — built-in conservative engagement windows
   (``scripts/seo_titles.best_time_windows``) plus the user's own preferred
   hours; produces concrete ISO slots for the next N days.
2. **Batch plan generator** — given M clips and K slots, produces an even
   spread: each clip gets a publish_at; when there are more clips than
   slots, additional slots are synthesized inside the user's window.
3. **Daemon uploader** — waits until each scheduled slot, then calls the
   existing ``publish_panel.stream_upload_batch`` (dry-run supported), so
   clips are released on YouTube exactly at the planned times.

YouTube scheduling requires ``privacy_status=private`` for publish_at; the
uploader already enforces that. Dry-run mode prints the plan without
touching the network.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import seo_titles  # noqa: E402

PLAN_NAME = "publish_schedule.json"


def load_measured_hours(project_folder: str | None) -> list[int]:
    """Read performance_insights.json best_hours (empty when unavailable)."""
    if not project_folder:
        return []
    path = os.path.join(os.path.abspath(os.fspath(project_folder)),
                        "performance_insights.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    hours = data.get("best_hours") if isinstance(data, dict) else None
    if not isinstance(hours, list):
        return []
    return sorted({int(h) % 24 for h in hours if isinstance(h, (int, float))})


def build_plan(video_paths: list[str], *, platform: str = "youtube",
               start_at: str | None = None, slots_per_day: int = 1,
               days: int = 7, user_hours: list[int] | None = None,
               timezone_offset_hours: float = 0.0,
               measured_hours: list[int] | None = None) -> dict[str, Any]:
    """Build an even publish plan for the given clips.

    Returns {"plan": [{video_path, publish_at, day, hour}], ...}. Slots are
    generated inside best windows (or the user's explicit hours). When
    measured_hours is given (from performance_insights.json), those win over
    the generic platform windows. When clips
    exceed generated slots, the plan cycles through the window hours again
    on following days until every clip is placed.
    """
    videos = [os.path.abspath(os.fspath(p)) for p in (video_paths or [])
              if p and os.path.isfile(p)]
    if not videos:
        return {"ok": False, "error": "no valid video files", "plan": []}

    data = seo_titles.BEST_TIMES_BY_PLATFORM.get(
        platform, seo_titles.BEST_TIMES_BY_PLATFORM["youtube"])
    if user_hours:
        weekday_hours = sorted({int(h) % 24 for h in user_hours})
        data = {"weekday": [(h, h + 1) for h in weekday_hours],
                "weekend": [(h, h + 1) for h in weekday_hours]}
    elif measured_hours:
        measured = sorted({int(h) % 24 for h in measured_hours})
        data = {"weekday": [(h, h + 1) for h in measured],
                "weekend": [(h, h + 1) for h in measured]}

    start = dt.datetime.now(dt.timezone.utc)
    if start_at:
        try:
            start = dt.datetime.fromisoformat(
                str(start_at).strip().replace("Z", "+00:00"))
            if start.tzinfo is None:
                start = start.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            return {"ok": False, "error": "invalid start_at", "plan": []}

    slots: list[dt.datetime] = []
    # generate up to `days` days of slots, then keep going until every clip fits
    day_span = max(days, (len(videos) // max(1, slots_per_day)) + 1)
    for day_offset in range(day_span):
        day = start + dt.timedelta(days=day_offset)
        is_weekend = day.weekday() >= 5
        windows = data.get("weekend" if is_weekend else "weekday", [])
        for _start_hour, _end_hour in windows:
            for hour in range(_start_hour, _end_hour):
                slot = day.replace(hour=hour % 24, minute=0, second=0, microsecond=0)
                if slot <= start:
                    continue
                slots.append(slot)
                if len(slots) >= len(videos):
                    break
            if len(slots) >= len(videos):
                break
        if len(slots) >= len(videos):
            break

    if not slots:
        return {"ok": False, "error": "no slots generated", "plan": []}

    plan = []
    for idx, video in enumerate(videos):
        slot = slots[min(idx, len(slots) - 1)]
        plan.append({
            "video_path": video,
            "video_name": os.path.basename(video),
            "publish_at": slot.isoformat(),
            "day": slot.date().isoformat(),
            "hour": slot.hour,
            "platform": platform,
        })

    return {
        "ok": True,
        "platform": platform,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "timezone_offset_hours": timezone_offset_hours,
        "count": len(plan),
        "plan": plan,
    }


def save_plan(plan: dict[str, Any], project_folder: str) -> str:
    path = os.path.join(project_folder, PLAN_NAME)
    os.makedirs(project_folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    return path


def _wait_until(target: dt.datetime, sleep=None):
    if sleep is None:
        sleep = time.sleep
    while True:
        now = dt.datetime.now(dt.timezone.utc)
        if now >= target:
            return
        sleep(min(60, max(1, (target - now).total_seconds())))


def run_daemon(plan: dict[str, Any], *, dry_run: bool = True,
               privacy: str = "private", interval_minutes: int = 60,
               progress=None, sleep=None) -> dict[str, Any]:
    """Execute a plan: wait for each slot, then upload that clip.

    Returns a summary dict. With dry_run=True nothing is uploaded — the
    scheduler prints what it WOULD do at each time (safe to test).
    """
    if not plan.get("ok"):
        return {"ok": False, "error": plan.get("error", "invalid plan")}
    items = plan.get("plan", [])
    results = []
    for item in items:
        target = dt.datetime.fromisoformat(item["publish_at"])
        if target.tzinfo is None:
            target = target.replace(tzinfo=dt.timezone.utc)
        if progress:
            progress("scheduled", item["video_name"], item["publish_at"])
        if not dry_run:
            _wait_until(target, sleep=sleep)
        try:
            from webui import publish_panel
            updates = list(publish_panel.stream_upload_batch(
                os.path.dirname(item["video_path"]), item.get("platform", "youtube"),
                [item["video_path"]], dry_run, "warn",
                privacy_status=privacy,
                publish_at=item["publish_at"] if not dry_run else None,
                schedule_interval_minutes=interval_minutes,
                require_existing_auth=not dry_run,
            ))
            status = "dry_run" if dry_run else "uploaded"
            results.append({"video": item["video_name"], "status": status,
                            "publish_at": item["publish_at"],
                            "detail": "; ".join(str(u) for u in updates[-3:])})
            if progress:
                progress("done", item["video_name"], status)
        except Exception as exc:
            results.append({"video": item["video_name"], "status": "failed",
                            "publish_at": item["publish_at"],
                            "detail": str(exc)[:300]})
            if progress:
                progress("failed", item["video_name"], str(exc)[:150])
    return {"ok": True, "dry_run": dry_run, "results": results}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", help="JSON plan file to execute (run_daemon)")
    parser.add_argument("--videos", nargs="*", default=[],
                        help="video files to schedule (build_plan)")
    parser.add_argument("--project", default=None,
                        help="project folder for the plan file")
    parser.add_argument("--platform", default="youtube",
                        choices=["youtube", "tiktok", "reels"])
    parser.add_argument("--start-at", default=None,
                        help="first slot ISO time (default: now)")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--user-hours", type=int, nargs="*", default=None,
                        help="preferred hours (0-23); overrides built-in windows")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan / simulate execution (default for --plan)")
    parser.add_argument("--privacy", default="private",
                        choices=["private", "unlisted"])
    args = parser.parse_args(argv)

    if args.plan:
        with open(args.plan, "r", encoding="utf-8") as f:
            plan = json.load(f)
        summary = run_daemon(plan, dry_run=args.dry_run, privacy=args.privacy)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if not args.videos:
        print("provide --videos or --plan")
        return 2

    measured = load_measured_hours(args.project) if not args.user_hours else []
    if measured:
        print("[scheduler] using measured best hours from insights: {}".format(measured))
    plan = build_plan(args.videos, platform=args.platform,
                      start_at=args.start_at, days=args.days,
                      user_hours=args.user_hours, measured_hours=measured)
    if args.project:
        path = save_plan(plan, args.project)
        print("[scheduler] plan saved: {}".format(path))
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0 if plan.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
