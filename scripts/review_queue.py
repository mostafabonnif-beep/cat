"""Build a human-review queue from every automated safety and quality report."""
from __future__ import annotations

import html
import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any

QUEUE_FILENAME = "review_queue.json"
HTML_FILENAME = "review_queue.html"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load(project_folder: str, filename: str) -> dict[str, Any]:
    try:
        with open(os.path.join(project_folder, filename), "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _add(queue: dict[int, dict[str, Any]], index: Any, reason: str,
         severity: str = "medium", source: str = "automation", evidence: Any = None) -> None:
    try:
        key = int(index)
    except (TypeError, ValueError):
        key = -1
    item = queue.setdefault(key, {"index": key, "reasons": [], "evidence": [], "status": "pending"})
    reason = str(reason or "review required")
    if reason not in item["reasons"]:
        item["reasons"].append(reason)
    item["severity"] = "high" if severity == "high" or item.get("severity") == "high" else severity
    if source not in item.setdefault("sources", []):
        item["sources"].append(source)
    if evidence and evidence not in item["evidence"]:
        item["evidence"].append(evidence)


def build_queue(project_folder: str, *, write_html: bool = True) -> dict[str, Any]:
    """Aggregate unresolved findings without changing the publish decision."""
    scorecard = _load(project_folder, "risk_scorecard.json")
    safety = _load(project_folder, "safety_report.json")
    provenance = _load(project_folder, "provenance_report.json")
    ocr = _load(project_folder, "ocr_safety_report.json")
    audio = _load(project_folder, "audio_qc_report.json")
    queue: dict[int, dict[str, Any]] = {}

    for entry in scorecard.get("segments", []) or []:
        if not isinstance(entry, dict):
            continue
        axes = entry.get("axes") or {}
        if float(entry.get("overall_score", 0) or 0) >= 40:
            _add(queue, entry.get("index"), "risk score: {} ({})".format(entry.get("overall_score", 0), entry.get("overall", "unknown")), "high" if float(entry.get("overall_score", 0) or 0) >= 70 else "medium", "risk_scorecard", "risk_scorecard.json")
        for name in ("ocr", "provenance"):
            finding = axes.get(name) or {}
            if finding.get("action") in {"block", "review"}:
                _add(queue, entry.get("index"), "{}: {}".format(name, "; ".join(finding.get("reasons", []) or ["review required"])), "high" if finding.get("action") == "block" else "medium", name, "risk_scorecard.json")

    for entry in safety.get("segments", []) or safety.get("blocked_segments", []) or []:
        if isinstance(entry, dict):
            _add(queue, entry.get("index"), entry.get("reason") or entry.get("title") or "safety finding", "high", "safety", "safety_report.json")
    for entry in provenance.get("clips", []) or []:
        if isinstance(entry, dict) and entry.get("action") in {"block", "review"}:
            _add(queue, entry.get("index"), "; ".join(entry.get("reasons", []) or ["rights/transformation review"]), "high" if entry.get("action") == "block" else "medium", "provenance", "provenance_report.json")
    for entry in ocr.get("clips", []) or []:
        if isinstance(entry, dict) and entry.get("action") in {"block", "review"}:
            _add(queue, entry.get("index"), "OCR: " + "; ".join(entry.get("reasons", []) or ["on-screen text review"]), "high" if entry.get("action") == "block" else "medium", "ocr", "ocr_safety_report.json")
    if audio.get("status") in {"review", "block"}:
        for entry in audio.get("clips", []) or []:
            if isinstance(entry, dict):
                _add(queue, entry.get("index"), "audio QC requires review", "high" if audio.get("status") == "block" else "medium", "audio_qc", "audio_qc_report.json")

    try:
        from scripts import review_decisions
        decisions = {int(item["clip_index"]): item["decision"]
                     for item in review_decisions.load_decisions(project_folder)
                     if item.get("clip_index") is not None}
    except Exception:
        decisions = {}
    segments = {entry.get("index"): entry for entry in scorecard.get("segments", []) if isinstance(entry, dict)}
    clips = []
    for index, item in sorted(queue.items(), key=lambda pair: (-({"high": 2, "medium": 1, "low": 0}.get(pair[1].get("severity"), 0)), pair[0])):
        segment = segments.get(index, {})
        item["title"] = segment.get("title", "")
        item["start_time"] = segment.get("start_time")
        item["end_time"] = segment.get("end_time")
        prior = decisions.get(index)
        item["prior_decision"] = prior
        item["next_action"] = prior if prior in {"approve", "reject", "edit", "mute"} else "manual_review_before_publish"
        clips.append(item)
    report = {"generated_at": _now(), "project": os.path.abspath(project_folder), "total": len(clips), "high": sum(item.get("severity") == "high" for item in clips), "clips": clips}
    path = os.path.join(project_folder, QUEUE_FILENAME)
    fd, temporary = tempfile.mkstemp(prefix=".review-", suffix=".tmp", dir=project_folder)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)
    if write_html:
        rows = []
        for item in clips:
            prior = item.get("prior_decision")
            rows.append("<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                item["index"], html.escape(str(item.get("title", ""))), html.escape(str(item.get("severity", ""))),
                html.escape("; ".join(item.get("reasons", []))), html.escape(", ".join(item.get("sources", []))),
                html.escape(str(prior or "—"))))
        document = ("<!doctype html><meta charset='utf-8'><title>Review Queue</title>"
                    "<style>body{font-family:system-ui;max-width:1100px;margin:2rem auto}"
                    "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:.6rem;text-align:left}</style>"
                    "<h1>Automated Review Queue</h1><p>" + str(len(clips)) +
                    " clip(s) require review before publishing.</p><table><tr>"
                    "<th>#</th><th>Title</th><th>Severity</th><th>Reasons</th><th>Sources</th><th>Decision</th></tr>"
                    + "".join(rows) + "</table>")
        with open(os.path.join(project_folder, HTML_FILENAME), "w", encoding="utf-8") as handle:
            handle.write(document)
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Build the automated human-review queue")
    parser.add_argument("--project", required=True)
    args = parser.parse_args(argv)
    report = build_queue(args.project)
    print(json.dumps({"total": report["total"], "high": report["high"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
