"""Close the performance loop: link real YouTube outcomes to clip features.

Reads publish_history.jsonl (which clips were uploaded, with their video IDs
and the segment features frozen at publish time), merges in real YouTube
Analytics numbers, and produces `performance_insights.json` + a human-readable
console summary: which clip traits actually correlate with views and retention.
Pure stdlib + optional googleapiclient — never mutates any pipeline decision.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
from datetime import datetime, timezone
from typing import Any

HISTORY_NAME = "publish_history.jsonl"
REPORT_NAME = "performance_insights.json"

# Segment features copied into publish events (see publish_panel.record).
FEATURE_KEYS = (
    "duration", "hook_strength", "narrative_completeness", "clarity_score",
    "novelty_score", "selection_score", "title_quality_score",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_history(project_folder: str) -> list[dict[str, Any]]:
    path = os.path.join(project_folder, HISTORY_NAME)
    events = []
    if not os.path.exists(path):
        return events
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
                if isinstance(item, dict):
                    events.append(item)
    except OSError:
        return []
    return events


def _publish_hour(value: Any) -> int | None:
    """Local hour (0-23) of a publish timestamp, for schedule correlation."""
    try:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
        return int(moment.astimezone().hour)
    except (ValueError, TypeError, OverflowError):
        return None


def _published_videos(project_folder: str) -> list[dict[str, Any]]:
    """Published events enriched with segment features from the scorecard."""
    segments = {}
    try:
        with open(os.path.join(project_folder, "risk_scorecard.json"), "r", encoding="utf-8") as fh:
            scorecard = json.load(fh)
        for entry in scorecard.get("segments", []) or []:
            if isinstance(entry, dict):
                segments[entry.get("index")] = entry
    except Exception:
        pass
    published = []
    for event in _read_history(project_folder):
        video_id = event.get("video_id")
        if not video_id or event.get("status") not in {"uploaded", "scheduled"}:
            continue
        entry = {}
        match = re.search(r"#?(\d+)", str(event.get("video", "")))
        if match:
            entry = segments.get(int(match.group(1)), {}) or {}
        features = {key: entry.get(key) for key in FEATURE_KEYS if entry.get(key) is not None}
        hour = _publish_hour(event.get("publish_at") or event.get("timestamp"))
        if hour is not None:
            features["publish_hour"] = hour
        published.append({
            "video_id": video_id,
            "title": event.get("title", ""),
            "platform": event.get("platform", "youtube"),
            "published_at": event.get("timestamp"),
            "features": features,
        })
    return published


def _fetch_real_metrics(video_ids: list[str]) -> dict[str, dict[str, float]]:
    """Pull views/retention via scripts.analytics. Empty when unavailable."""
    try:
        from scripts import analytics as analytics_mod
        ya, yt = analytics_mod._build_services()
    except Exception:
        return {}
    metrics: dict[str, dict[str, float]] = {}
    start, end = analytics_mod._date_range(365)
    for index in range(0, len(video_ids), 40):
        chunk = video_ids[index:index + 40]
        filters = "video==" + ";".join(chunk)
        try:
            resp = ya.reports().query(
                ids="channel==MINE", startDate=start, endDate=end,
                metrics="views,averageViewDuration,likes,shares",
                dimensions="video", filters=filters,
                maxResults=40,
            ).execute()
        except Exception:
            continue
        for row in resp.get("rows", []) or []:
            vid, views, avg_dur, likes, shares = (list(row) + [None] * 5)[:5]
            metrics[vid] = {
                "views": int(views or 0),
                "avg_view_duration": round(float(avg_dur or 0), 1),
                "likes": int(likes or 0),
                "shares": int(shares or 0),
            }
    return metrics


def _corr(pairs: list[tuple[float, float]]) -> float | None:
    """Pearson correlation over complete feature/metric pairs."""
    points = [(x, y) for x, y in pairs if x is not None and y is not None]
    if len(points) < 3:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    denom = (sum(a * a for a in dx) * sum(b * b for b in dy)) ** 0.5
    if not denom:
        return None
    return round(sum(a * b for a, b in zip(dx, dy)) / denom, 3)


def _strength(value: float | None) -> str:
    if value is None:
        return "insufficient_data"
    magnitude = abs(value)
    if magnitude >= 0.7:
        return "strong"
    if magnitude >= 0.4:
        return "moderate"
    if magnitude >= 0.2:
        return "weak"
    return "negligible"


def analyze(project_folder: str, fetch_live: bool = True) -> dict[str, Any]:
    """Build the performance-loop report. Never raises."""
    project_folder = os.path.abspath(os.fspath(project_folder))
    published = _published_videos(project_folder)
    report: dict[str, Any] = {
        "generated_at": _now(),
        "project": project_folder,
        "published_count": len(published),
        "with_metrics": 0,
        "correlations": {},
        "insights": [],
        "clips": [],
    }
    if not published:
        report["insights"].append(
            "No successful uploads with video IDs found in publish_history.jsonl yet.")
        return report

    video_ids = [item["video_id"] for item in published]
    metrics = _fetch_real_metrics(video_ids) if fetch_live else {}
    for item in published:
        item["metrics"] = metrics.get(item["video_id"])
        report["clips"].append(item)
    measured = [item for item in published if item.get("metrics")]
    report["with_metrics"] = len(measured)
    if not measured:
        report["insights"].append(
            "Uploads found but YouTube Analytics returned no numbers yet "
            "(OAuth not configured, or videos are too recent).")
        return report

    correlations = {}
    for feature in FEATURE_KEYS:
        pairs = [(item["features"].get(feature), item["metrics"]["views"])
                 for item in measured]
        value = _corr(pairs)
        correlations[feature] = {"vs_views": value, "strength": _strength(value)}
    report["correlations"] = correlations

    for feature, result in sorted(
            correlations.items(), key=lambda kv: -(abs(kv[1]["vs_views"] or 0))):
        value = result["vs_views"]
        if value is None or result["strength"] in {"negligible", "insufficient_data"}:
            continue
        direction = "higher" if value > 0 else "lower"
        report["insights"].append(
            "{}: clips with {} {} {} get {} views "
            "(r={}, {} — correlation, not causation; {} samples).".format(
                result["strength"].upper(), feature, "↑" if value > 0 else "↓",
                "more" if value > 0 else "fewer", direction, value,
                result["strength"], len(measured)))
    if not report["insights"]:
        report["insights"].append(
            "Metrics present but no feature shows a meaningful correlation yet "
            "— publish more clips to build signal.")
    return report


def write_report(project_folder: str, report: dict[str, Any]) -> str:
    path = os.path.join(os.path.abspath(os.fspath(project_folder)), REPORT_NAME)
    fd, temporary = tempfile.mkstemp(prefix=".perf-", suffix=".tmp",
                                     dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Link real YouTube outcomes to clip features (performance loop)")
    parser.add_argument("--project", required=True)
    parser.add_argument("--offline", action="store_true",
                        help="Skip YouTube Analytics fetch; analyze local data only")
    args = parser.parse_args(argv)
    report = analyze(args.project, fetch_live=not args.offline)
    path = write_report(args.project, report)
    print("performance insights → {}".format(path))
    print("published: {} · with metrics: {}".format(
        report["published_count"], report["with_metrics"]))
    for insight in report["insights"]:
        print("  • {}".format(insight))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
