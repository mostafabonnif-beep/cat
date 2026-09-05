"""Record human review decisions so every future run learns from them locally."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from scripts import content_ledger

DECISIONS = {"approve", "reject", "edit", "mute"}
DB_FILENAME = content_ledger.REGISTRY_FILENAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _connect(project_folder: str, registry_path: str | None = None) -> sqlite3.Connection:
    path = content_ledger._registry_path(project_folder, registry_path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("""CREATE TABLE IF NOT EXISTS review_decisions (
        decision_key TEXT PRIMARY KEY,
        project_path TEXT NOT NULL,
        clip_index INTEGER,
        platform TEXT NOT NULL,
        decision TEXT NOT NULL,
        severity TEXT,
        reasons_json TEXT NOT NULL,
        note TEXT,
        reviewer TEXT NOT NULL DEFAULT 'owner',
        created_at TEXT NOT NULL
    )""")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_review_decisions_platform ON review_decisions(platform, decision, created_at)")
    connection.commit()
    return connection


def record_decision(project_folder: str, clip_index: int | None, decision: str,
                    *, platform: str = "youtube", severity: str | None = None,
                    reasons: list[dict[str, Any]] | None = None, note: str = "",
                    reviewer: str = "owner", registry_path: str | None = None) -> bool:
    decision = str(decision or "").strip().lower()
    if decision not in DECISIONS:
        raise ValueError("decision must be one of: {}".format(", ".join(sorted(DECISIONS))))
    raw_key = "|".join(str(v or "") for v in (
        os.path.abspath(project_folder), clip_index, platform, decision))
    connection = _connect(project_folder, registry_path)
    try:
        connection.execute(
            """INSERT OR REPLACE INTO review_decisions
            (decision_key, project_path, clip_index, platform, decision, severity,
             reasons_json, note, reviewer, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (hashlib.sha256(raw_key.encode("utf-8")).hexdigest(),
             os.path.abspath(project_folder), clip_index,
             str(platform or "youtube").strip().lower(), decision, severity,
             json.dumps(reasons or [], ensure_ascii=False), str(note or "")[:1000],
             str(reviewer or "owner")[:100], _now()))
        connection.commit()
        return True
    finally:
        connection.close()


def load_decisions(project_folder: str, registry_path: str | None = None) -> list[dict[str, Any]]:
    connection = _connect(project_folder, registry_path)
    try:
        rows = connection.execute(
            "SELECT * FROM review_decisions ORDER BY created_at DESC").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["reasons"] = json.loads(item.pop("reasons_json", "[]"))
            except (TypeError, ValueError, json.JSONDecodeError):
                item["reasons"] = []
            result.append(item)
        return result
    finally:
        connection.close()


def learned_rules(project_folder: str, registry_path: str | None = None) -> dict[str, Any]:
    """Derive strict review cues from past human decisions."""
    rules: dict[str, Any] = {"reject_terms": {}, "approve_terms": {}, "decision_counts": {}}
    for entry in load_decisions(project_folder, registry_path):
        decision = entry.get("decision", "")
        rules["decision_counts"][decision] = rules["decision_counts"].get(decision, 0) + 1
        bucket = rules["reject_terms"] if decision == "reject" else rules["approve_terms"] if decision == "approve" else None
        if bucket is None:
            continue
        for reason in entry.get("reasons", []) or []:
            code = str(reason.get("code", reason.get("term", "unknown")))
            bucket[code] = bucket.get(code, 0) + 1
    return rules


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Record and inspect human review decisions")
    parser.add_argument("--project", required=True)
    parser.add_argument("--index", type=int, default=None)
    parser.add_argument("--decision", required=True, choices=sorted(DECISIONS))
    parser.add_argument("--platform", default="youtube")
    parser.add_argument("--note", default="")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    if args.list:
        print(json.dumps(learned_rules(args.project), ensure_ascii=False, indent=2))
        return 0
    record_decision(args.project, args.index, args.decision,
                    platform=args.platform, note=args.note)
    print(json.dumps({"recorded": args.decision, "index": args.index}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
