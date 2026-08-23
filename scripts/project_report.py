"""Build a shareable production-readiness report for one OUSSAMA Cutter project.

The report is local-only and never includes OAuth tokens, API keys, or source
media contents. It consolidates the artifacts already produced by the pipeline
so a creator can audit a project before publishing or hand it to an editor.
"""

import argparse
import datetime as _dt
import html
import json
import os
from typing import Any, Dict, List, Optional

from scripts import content_guard
from scripts.checkpoint import STAGES, load_checkpoint
from scripts.media_validation import validate_media_file

REPORT_NAME = "project_report.json"
HTML_REPORT_NAME = "project_report.html"


def _load_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _file_entry(path: str) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "name": os.path.basename(path),
        "path": path,
        "exists": os.path.exists(path),
    }
    if entry["exists"]:
        try:
            stat = os.stat(path)
            entry["bytes"] = stat.st_size
            entry["modified_at"] = _dt.datetime.fromtimestamp(
                stat.st_mtime, tz=_dt.timezone.utc
            ).isoformat()
        except OSError:
            entry["bytes"] = 0
    else:
        entry["bytes"] = 0
    return entry


def _read_publish_history(path: str) -> Dict[str, Any]:
    entries: List[dict] = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        value = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(value, dict):
                        entries.append(value)
        except OSError:
            pass
    statuses: Dict[str, int] = {}
    for entry in entries:
        status = str(entry.get("status", "unknown"))
        statuses[status] = statuses.get(status, 0) + 1
    return {"total": len(entries), "statuses": statuses, "last": entries[-1] if entries else None}


def _safety_summary(data: Optional[dict]) -> Dict[str, Any]:
    data = data or {}
    segments = data.get("segments") if isinstance(data.get("segments"), list) else []
    counts = {"allow": 0, "review": 0, "block": 0, "blocked": 0, "manual_review": 0}
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        status = str(segment.get("status", "")).lower()
        action = str((segment.get("semantic") or {}).get("action", "")).lower()
        if action in {"allow", "review", "block"}:
            counts[action] += 1
        if status in {"blocked", "semantic_blocked", "ai_blocked", "censor"}:
            counts["blocked"] += 1
        if status == "manual_review" or action == "review":
            counts["manual_review"] += 1
    return {
        "total": len(segments),
        "counts": counts,
        "legacy_blocked": len(data.get("blocked", [])) if isinstance(data.get("blocked"), list) else 0,
    }


def _media_summary(project_folder: str) -> Dict[str, Any]:
    final_dir = os.path.join(project_folder, "final")
    files = []
    if os.path.isdir(final_dir):
        for name in sorted(os.listdir(final_dir)):
            path = os.path.join(final_dir, name)
            if os.path.isfile(path) and name.lower().endswith((".mp4", ".mov", ".mkv", ".webm")):
                validation = validate_media_file(path)
                files.append({
                    "name": name,
                    "bytes": os.path.getsize(path),
                    "ok": bool(validation.get("ok")),
                    "duration": validation.get("duration"),
                    "errors": validation.get("errors", []),
                })
    return {
        "directory": final_dir,
        "count": len(files),
        "valid": sum(1 for item in files if item["ok"]),
        "invalid": sum(1 for item in files if not item["ok"]),
        "files": files,
    }


def _polish_summary(data: Optional[dict]) -> Dict[str, Any]:
    summary = data.get("summary") if isinstance(data, dict) else {}
    summary = summary if isinstance(summary, dict) else {}
    return {
        "present": bool(data),
        "total": int(summary.get("total", 0) or 0),
        "enhanced": int(summary.get("enhanced", 0) or 0),
        "partial": int(summary.get("partial", 0) or 0),
        "fallback": int(summary.get("fallback", 0) or 0),
        "failed": int(summary.get("failed", 0) or 0),
        "degraded": int(summary.get("degraded", 0) or 0),
    }


def _tracking_summary(data: Optional[dict]) -> Dict[str, Any]:
    data = data if isinstance(data, dict) else {}
    return {
        "present": bool(data),
        "backend": data.get("backend"),
        "requested_active_speaker": bool(data.get("requested_active_speaker", False)),
        "active_speaker_applied": bool(data.get("active_speaker_applied", False)),
        "face_tracking_applied": bool(data.get("face_tracking_applied", False)),
        "status": data.get("status"),
        "warning": data.get("warning"),
    }


def _batch_publish_summary(data: Optional[dict]) -> Dict[str, Any]:
    summary = data.get("summary") if isinstance(data, dict) else {}
    summary = summary if isinstance(summary, dict) else {}
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    return {
        "present": bool(data),
        "total": int(summary.get("total", 0) or 0),
        "successful": int(summary.get("successful", 0) or 0),
        "failed": int(summary.get("failed", 0) or 0),
        "blocked": int(summary.get("blocked", 0) or 0),
        "skipped_duplicate": int(summary.get("skipped_duplicate", 0) or 0),
        "counts": counts,
    }


def build_report(project_folder: str) -> Dict[str, Any]:
    """Return a JSON-safe project audit without making network calls."""
    project_folder = os.path.abspath(os.path.expanduser(str(project_folder)))
    manifest = _load_json(os.path.join(project_folder, "project_manifest.json")) or {
        "name": os.path.basename(project_folder),
        "status": "legacy",
    }
    checkpoint = load_checkpoint(project_folder)
    stages = checkpoint.get("stages", {})
    safety = _safety_summary(_load_json(os.path.join(project_folder, "safety_report.json")))
    scorecard = _load_json(os.path.join(project_folder, "risk_scorecard.json")) or {}
    risk_summary = scorecard.get("summary") if isinstance(scorecard.get("summary"), dict) else {}
    content_guard_report = _load_json(os.path.join(project_folder, "content_guard_report.json")) or {}
    try:
        channel = content_guard.channel_status(project_folder, "youtube")
    except Exception:
        channel = {"locked": False, "incidents": [], "count": 0}
    publish = _read_publish_history(os.path.join(project_folder, "publish_history.jsonl"))
    polish_data = _load_json(os.path.join(project_folder, "polish_report.json"))
    polish = _polish_summary(polish_data)
    batch_publish = _batch_publish_summary(
        _load_json(os.path.join(project_folder, "publish_batch_report.json")))
    tracking = _tracking_summary(
        _load_json(os.path.join(project_folder, "tracking_report.json")))
    media = _media_summary(project_folder)
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    source_path = source.get("path") or source.get("local_path") or source.get("original_path")
    source_info = {
        "type": source.get("type", "unknown"),
        "path": os.path.abspath(os.path.expanduser(source_path)) if isinstance(source_path, str) and source_path else None,
        "exists": bool(source_path and os.path.isfile(os.path.abspath(os.path.expanduser(source_path)))) if isinstance(source_path, str) else False,
        "managed": source.get("managed", True),
    }

    artifacts = [
        _file_entry(os.path.join(project_folder, name))
        for name in (
            "input.mp4", "viral_segments.txt", "safety_report.json",
            "risk_scorecard.json", "content_guard_report.json", "publish_blocklist.json", "checkpoint.json",
            "polish_report.json", "publish_batch_report.json", "tracking_report.json", "cuts_manifest.json",
        )
    ]
    errors = []
    if not os.path.isdir(project_folder):
        errors.append("project folder does not exist")
    if media["invalid"]:
        errors.append("one or more rendered files failed media validation")
    if safety["counts"]["blocked"] or safety["counts"]["manual_review"]:
        errors.append("safety review is required before publishing")
    if int(risk_summary.get("blocked_for_publish", 0) or 0) > 0:
        errors.append("risk scorecard contains clips blocked for publishing")
    if int(content_guard_report.get("blocked", 0) or 0) > 0:
        errors.append("content guard removed previously published or rate-limited candidates")
    if channel.get("locked"):
        errors.append("automatic YouTube publishing is locked after a recorded policy incident")
    if polish.get("present") and (polish["fallback"] or polish["failed"] or polish["degraded"]):
        errors.append("professional polish has fallback/degraded clips; review polish_report.json before real publishing")
    if batch_publish.get("present") and (batch_publish["failed"] or batch_publish["blocked"]):
        errors.append("the last publish batch has failed or blocked clips; retry only those after review")
    if tracking.get("present") and tracking.get("requested_active_speaker") and not tracking.get("active_speaker_applied"):
        errors.append("active speaker was requested but the selected backend only provided face tracking")

    pending = [stage for stage in STAGES if stages.get(stage) is not True]
    checkpoint_runtime = {
        "active_stage": checkpoint.get("active_stage"),
        "last_error": checkpoint.get("last_error"),
        "history": checkpoint.get("history", [])[-20:],
    }
    if checkpoint_runtime["active_stage"]:
        errors.append("pipeline is currently active at stage {}".format(checkpoint_runtime["active_stage"]))
    if checkpoint_runtime["last_error"]:
        errors.append("pipeline has a recorded failure; resume or retry the failed stage")
    ready = not errors and media["count"] > 0 and not pending
    return {
        "schema": 1,
        "generated_at": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        "project": {
            "name": manifest.get("name", os.path.basename(project_folder)),
            "path": project_folder,
            "status": manifest.get("status", "unknown"),
        },
        "readiness": {"ready_for_publish": ready, "errors": errors},
        "stages": {"completed": [stage for stage in STAGES if stages.get(stage) is True], "pending": pending},
        "checkpoint": checkpoint_runtime,
        "safety": safety,
        "risk": {"summary": risk_summary, "blocked": len(scorecard.get("blocked", [])) if isinstance(scorecard.get("blocked"), list) else 0},
        "content_guard": {
            "blocked": int(content_guard_report.get("blocked", 0) or 0),
            "kept": int(content_guard_report.get("kept", 0) or 0),
            "database": content_guard_report.get("database"),
            "channel": channel,
        },
        "media": media,
        "source": source_info,
        "publishing": {
            "history": publish,
            "last_batch": batch_publish,
        },
        "polish": polish,
        "tracking": tracking,
        "artifacts": artifacts,
    }


def _cell(value: Any) -> str:
    if isinstance(value, bool):
        value = "نعم" if value else "لا"
    return html.escape(str(value if value is not None else "—"))


def _tracking_rows(tracking: Dict[str, Any]) -> str:
    if not tracking.get("present"):
        return ""
    items = [
        ("الخلفية (Backend)", tracking.get("backend")),
        ("المتحدث النشط مطلوب", tracking.get("requested_active_speaker")),
        ("المتحدث النشط طُبّق", tracking.get("active_speaker_applied")),
        ("تمليس الكاميرا (Smoothing)", tracking.get("smoothing")),
        ("Headroom", tracking.get("headroom")),
        ("تحذير", tracking.get("warning")),
    ]
    rows = "".join(
        "<tr><td>{}</td><td>{}</td></tr>".format(_cell(k), _cell(v))
        for k, v in items if v is not None)
    return "<div class='card'><h2>التتبع (Tracking)</h2><table>{}</table></div>".format(rows)


def _originality_rows(report: Dict[str, Any]) -> str:
    content_guard = report.get("content_guard", {}) or {}
    channel = content_guard.get("channel", {}) or {}
    rows = [
        ("مقاطع محجوبة تلقائياً", content_guard.get("blocked")),
        ("مقاطع مُبقاة", content_guard.get("kept")),
        ("حالة القناة (قاطع الدائرة)", "مقفول" if channel.get("locked") else "مفتوح"),
        ("حوادث مسجلة", channel.get("count")),
    ]
    table = "".join("<tr><td>{}</td><td>{}</td></tr>".format(_cell(k), _cell(v)) for k, v in rows)
    return "<div class='card'><h2>حماية المحتوى المكرر (Originality)</h2><table>{}</table></div>".format(table)


def _publishing_rows(report: Dict[str, Any]) -> str:
    publishing = report.get("publishing", {}) or {}
    history = publishing.get("history", {}) or {}
    last_batch = publishing.get("last_batch", {}) or {}
    items = [
        ("نجاحات النشر", history.get("successful")),
        ("إخفاقات النشر", history.get("failed")),
        ("محجوبة قبل الرفع", history.get("blocked")),
        ("آخر دفعة — نجحت", last_batch.get("uploaded")),
        ("آخر دفعة — فشلت", last_batch.get("failed")),
        ("آخر دفعة — محجوبة", last_batch.get("blocked")),
    ]
    rows = "".join(
        "<tr><td>{}</td><td>{}</td></tr>".format(_cell(k), _cell(v))
        for k, v in items if v is not None)
    return "<div class='card'><h2>النشر (Publishing)</h2><table>{}</table></div>".format(rows)


def render_html(report: Dict[str, Any]) -> str:
    project = report.get("project", {})
    readiness = report.get("readiness", {})
    safety = report.get("safety", {})
    media = report.get("media", {})
    risk = report.get("risk", {})
    tracking = report.get("tracking", {}) or {}
    status = "جاهز للنشر" if readiness.get("ready_for_publish") else "يتطلب مراجعة"
    error_rows = "".join("<li>{}</li>".format(_cell(error)) for error in readiness.get("errors", []))
    media_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            _cell(item.get("name")), _cell(item.get("duration")), _cell(item.get("ok")),
            _cell("; ".join(item.get("errors", []))),
        )
        for item in media.get("files", [])
    )
    return """<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>تقرير مشروع OUSSAMA Cutter</title>
<style>body{{font-family:Arial,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;background:#111827;color:#e5e7eb}}h1,h2{{color:#fbbf24}}.card{{background:#1f2937;border:1px solid #374151;border-radius:10px;padding:1rem;margin:1rem 0}}table{{width:100%;border-collapse:collapse}}td,th{{border-bottom:1px solid #374151;padding:.55rem;text-align:right}}.ok{{color:#34d399}}.bad{{color:#f87171}}code{{color:#93c5fd}}</style></head>
<body><h1>تقرير جاهزية مشروع OUSSAMA Cutter</h1>
<div class="card"><h2>القرار</h2><p class="{cls}"><strong>{status}</strong></p><p>المشروع: <code>{project}</code></p></div>
<div class="card"><h2>ملخص السلامة والمخاطر</h2><table><tr><th>البند</th><th>القيمة</th></tr><tr><td>مقاطع السلامة المفحوصة</td><td>{safety_total}</td></tr><tr><td>محجوبة</td><td>{blocked}</td></tr><tr><td>تحتاج مراجعة يدوية</td><td>{review}</td></tr><tr><td>حظر بطاقة المخاطر</td><td>{risk_blocked}</td></tr></table></div>
<div class="card"><h2>الملفات النهائية</h2><p>الإجمالي: {media_count} — الصالح: {media_valid} — غير الصالح: {media_invalid}</p><table><tr><th>الملف</th><th>المدة</th><th>صالح</th><th>الملاحظات</th></tr>{media_rows}</table></div>
<div class="card"><h2>المشكلات التي تمنع النشر</h2><ul>{errors}</ul></div>
{tracking_html}
{originality_html}
{publishing_html}
</body></html>""".format(
        cls="ok" if readiness.get("ready_for_publish") else "bad",
        status=_cell(status), project=_cell(project.get("path")),
        safety_total=_cell(safety.get("total", 0)), blocked=_cell((safety.get("counts") or {}).get("blocked", 0)),
        review=_cell((safety.get("counts") or {}).get("manual_review", 0)),
        risk_blocked=_cell(risk.get("blocked", 0)), media_count=_cell(media.get("count", 0)),
        media_valid=_cell(media.get("valid", 0)), media_invalid=_cell(media.get("invalid", 0)),
        media_rows=media_rows or "<tr><td colspan='4'>لا توجد ملفات نهائية</td></tr>",
        errors=error_rows or "<li class='ok'>لا توجد مشكلات مسجلة</li>",
        tracking_html=_tracking_rows(tracking),
        originality_html=_originality_rows(report),
        publishing_html=_publishing_rows(report),
    )


def write_report(project_folder: str, html_report: bool = False) -> Dict[str, Any]:
    report = build_report(project_folder)
    with open(os.path.join(project_folder, REPORT_NAME), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    if html_report:
        with open(os.path.join(project_folder, HTML_REPORT_NAME), "w", encoding="utf-8") as handle:
            handle.write(render_html(report))
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create a local OUSSAMA Cutter project readiness report.")
    parser.add_argument("--project", required=True, help="Project folder")
    parser.add_argument("--html", action="store_true", help="Also write project_report.html")
    parser.add_argument("--json", action="store_true", help="Print the JSON report")
    args = parser.parse_args(argv)
    report = write_report(args.project, html_report=args.html)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("Project report → {}".format(os.path.join(args.project, REPORT_NAME)))
        if args.html:
            print("HTML report → {}".format(os.path.join(args.project, HTML_REPORT_NAME)))
        print("Ready for publish: {}".format(report["readiness"]["ready_for_publish"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
