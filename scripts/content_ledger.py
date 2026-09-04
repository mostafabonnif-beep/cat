"""Local SQLite evidence ledger for provenance, safety, and originality checks."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from typing import Any

REGISTRY_FILENAME = ".oussama_content_registry.sqlite3"


def _registry_path(project_folder: str, registry_path: str | None = None) -> str:
    if registry_path:
        return os.path.abspath(os.fspath(registry_path))
    configured = os.getenv("OUSSAMA_CONTENT_REGISTRY", "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    project = os.path.abspath(os.fspath(project_folder))
    parent = os.path.dirname(project)
    if os.path.basename(parent).lower() in {"virals", "oussama", "projects"}:
        return os.path.join(parent, REGISTRY_FILENAME)
    return os.path.join(project, REGISTRY_FILENAME)


def _connect(project_folder: str, registry_path: str | None = None) -> sqlite3.Connection:
    path = _registry_path(project_folder, registry_path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS clip_audits (
            audit_key TEXT PRIMARY KEY,
            project_path TEXT NOT NULL,
            clip_index INTEGER,
            platform TEXT NOT NULL,
            source_identity TEXT,
            source_start REAL,
            source_end REAL,
            fingerprint TEXT,
            visual_fingerprint TEXT,
            verdict TEXT NOT NULL,
            overall_score REAL,
            created_at TEXT NOT NULL,
            report_json TEXT NOT NULL
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_clip_audits_source "
        "ON clip_audits(source_identity, platform, created_at)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS safety_findings (
            finding_key TEXT PRIMARY KEY,
            project_path TEXT NOT NULL,
            clip_index INTEGER,
            platform TEXT NOT NULL,
            severity TEXT NOT NULL,
            code TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at TEXT NOT NULL,
            report_json TEXT NOT NULL
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_safety_findings_platform "
        "ON safety_findings(platform, severity, created_at)"
    )
    connection.commit()
    return connection


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def record_clip_audit(project_folder: str, clip_index: int | None,
                      report: dict[str, Any], video_path: str | None = None,
                      platform: str = "youtube",
                      registry_path: str | None = None) -> bool:
    from scripts import content_guard

    project_folder = os.path.abspath(os.fspath(project_folder))
    platform = str(platform or "youtube").strip().lower()
    source = content_guard.source_identity(project_folder)
    start, end = content_guard._time_window(project_folder, clip_index)
    fingerprint = content_guard.file_fingerprint(video_path)
    visual = content_guard._visual_fingerprint_for(video_path) if video_path else None
    verdict = str(report.get("overall", "unknown"))
    overall_score = report.get("overall_score")
    raw_key = "|".join(str(value or "") for value in (
        project_folder, clip_index, platform, fingerprint, source, start, end))
    audit_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    connection = _connect(project_folder, registry_path)
    try:
        connection.execute(
            """INSERT OR REPLACE INTO clip_audits
            (audit_key, project_path, clip_index, platform, source_identity,
             source_start, source_end, fingerprint, visual_fingerprint, verdict,
             overall_score, created_at, report_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (audit_key, project_folder, clip_index, platform, source, start, end,
             fingerprint, (visual or {}).get("hashes"), verdict, overall_score,
             _now(), json.dumps(report, ensure_ascii=False)),
        )
        connection.commit()
        return True
    finally:
        connection.close()


def record_safety_report(project_folder: str, report: dict[str, Any],
                         registry_path: str | None = None) -> int:
    project_folder = os.path.abspath(os.fspath(project_folder))
    platform = str(report.get("platform", "youtube") or "youtube").strip().lower()
    inserted = 0
    connection = _connect(project_folder, registry_path)
    try:
        for item in report.get("blocked_segments", []) or []:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            for reason in item.get("reasons", []) or []:
                if not isinstance(reason, dict):
                    continue
                code = str(reason.get("code", "unknown"))
                detail = str(reason.get("detail", ""))[:2000]
                severity = str(reason.get("severity", "high") or "high").strip().lower()
                raw_key = "|".join(str(value or "") for value in (
                    project_folder, index, platform, code, detail))
                finding_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
                connection.execute(
                    """INSERT OR REPLACE INTO safety_findings
                    (finding_key, project_path, clip_index, platform, severity,
                     code, detail, created_at, report_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (finding_key, project_folder, index, platform, severity, code,
                     detail, _now(), json.dumps(item, ensure_ascii=False)),
                )
                inserted += 1
        connection.commit()
        return inserted
    finally:
        connection.close()


def find_visual_matches(project_folder: str, video_path: str | None,
                       platform: str = "youtube", threshold: float = 0.80,
                       registry_path: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """Find near-duplicate rendered clips from other projects in the ledger."""
    if not video_path or not os.path.isfile(video_path):
        return []
    from scripts.originality import _similarity_between, video_fingerprint

    candidate = video_fingerprint(video_path)
    if not candidate:
        return []
    current_project = os.path.abspath(os.fspath(project_folder))
    connection = _connect(project_folder, registry_path)
    try:
        rows = connection.execute(
            "SELECT * FROM clip_audits WHERE platform=? ORDER BY created_at DESC LIMIT 500",
            (str(platform or "youtube").strip().lower(),),
        ).fetchall()
        matches = []
        for row in rows:
            if os.path.abspath(row["project_path"]) == current_project:
                continue
            raw = row["visual_fingerprint"]
            if not raw:
                continue
            try:
                stored = [int(value) for value in str(raw).split("|") if value]
            except ValueError:
                continue
            similarity = _similarity_between(candidate, stored)
            if similarity >= float(threshold):
                item = dict(row)
                item["similarity"] = similarity
                matches.append(item)
        matches.sort(key=lambda item: (item["similarity"], item.get("created_at", "")), reverse=True)
        return matches[:max(1, int(limit))]
    finally:
        connection.close()


def ledger_summary(project_folder: str, registry_path: str | None = None):
    connection = _connect(project_folder, registry_path)
    try:
        result = {"database": _registry_path(project_folder, registry_path)}
        for table in ("clip_audits", "safety_findings"):
            row = connection.execute("SELECT COUNT(*) AS count FROM " + table).fetchone()
            result[table] = int(row["count"] if row else 0)
        return result
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Local OUSSAMA provenance ledger")
    parser.add_argument("--project", required=True)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)
    if not args.summary:
        parser.error("--summary is required")
    print(json.dumps(ledger_summary(args.project), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
