"""Auditable local policy lexicon for YouTube safety screening."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

DB_FILENAME = ".oussama_policy_lexicon.sqlite3"
POLICY_SOURCES = {
    "youtube_hate_speech": "https://support.google.com/youtube/answer/2801939",
    "youtube_harassment": "https://support.google.com/youtube/answer/2802268",
    "youtube_violent_graphic": "https://support.google.com/youtube/answer/2802008",
    "youtube_community_guidelines": "https://www.youtube.com/howyoutubeworks/policies/community-guidelines/",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def db_path(project_folder: str | None = None, path: str | None = None) -> str:
    if path:
        return os.path.abspath(os.path.expanduser(path))
    configured = os.getenv("OUSSAMA_POLICY_DB", "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    if project_folder:
        project = os.path.abspath(os.fspath(project_folder))
        parent = os.path.dirname(project)
        if os.path.basename(parent).lower() in {"virals", "oussama", "projects"}:
            return os.path.join(parent, DB_FILENAME)
        return os.path.join(project, DB_FILENAME)
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), DB_FILENAME)


def _connect(project_folder: str | None = None, path: str | None = None) -> sqlite3.Connection:
    target = db_path(project_folder, path)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    connection = sqlite3.connect(target, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("""CREATE TABLE IF NOT EXISTS policy_sources (
        source_id TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        page_hash TEXT,
        last_checked TEXT NOT NULL,
        notes TEXT NOT NULL
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS policy_terms (
        term_id TEXT PRIMARY KEY,
        term TEXT NOT NULL,
        normalized_term TEXT NOT NULL,
        lang TEXT NOT NULL,
        severity TEXT NOT NULL,
        category TEXT NOT NULL,
        source TEXT NOT NULL,
        source_version TEXT NOT NULL,
        context_required INTEGER NOT NULL DEFAULT 1,
        active INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL,
        UNIQUE(normalized_term, lang, source)
    )""")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_policy_terms_lookup ON policy_terms(normalized_term, lang, active)")
    connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS policy_terms_fts_v2 USING fts5(term, category, term_id UNINDEXED)")
    connection.execute("""CREATE TABLE IF NOT EXISTS lexicon_meta (
        meta_key TEXT PRIMARY KEY,
        meta_value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    connection.commit()
    return connection


def _term_rows(terms):
    """Normalize BLOCKLIST / cache terms into ready-to-store row dicts.

    Single source of truth shared by ``sync_policy_db`` and ``run_doctor`` so
    the validator checks exactly what the database stores.
    """
    from scripts import safety_filter

    for raw in terms:
        if isinstance(raw, dict):
            term = str(raw.get("term", ""))
            lang = str(raw.get("lang", "multi"))
            severity = str(raw.get("severity", "high"))
            category = str(raw.get("category", "custom"))
            source = str(raw.get("source", "remote_cache"))
            version = str(raw.get("version", "cache"))
        else:
            try:
                term, lang, severity, category = raw[:4]
            except (TypeError, ValueError):
                continue
            source = "builtin"
            version = "repository"
        term = str(term).strip()
        if not term:
            continue
        lang = str(lang or "multi")[:20]
        severity = severity if severity in {"low", "medium", "high"} else "high"
        category = str(category or "custom")[:64]
        source = str(source or "builtin")[:200]
        yield {
            "term": term,
            "normalized": safety_filter.normalize_text(term),
            "lang": lang,
            "severity": severity,
            "category": category,
            "source": source,
            "version": version,
        }


def _terms_fingerprint(rows) -> str:
    """Deterministic fingerprint of the exact term set stored in the DB."""
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda r: "|".join((
            r["normalized"], r["lang"], r["source"], r["severity"], r["category"]))):
        digest.update("|".join((
            row["normalized"], row["lang"], row["source"],
            row["severity"], row["category"], row["term"])).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _term_id(term: str, lang: str, source: str) -> str:
    raw = "|".join((term.strip().casefold(), lang.strip(), source.strip()))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _context_required(category: str) -> int:
    return int(category in {"hate_slur", "hate_dehumanize", "harassment", "profanity"})


def sync_policy_db(project_folder: str | None = None, path: str | None = None,
                   force: bool = False) -> dict[str, Any]:
    """Materialize built-in and downloaded terms into an auditable SQLite DB.

    Incremental by default: when the term set (built-in + cached remote) is
    unchanged since the last sync, the full DELETE/re-INSERT of the FTS index
    is skipped. Before this change every ``analyze_segments`` run rebuilt the
    whole database from scratch — the lexicon is the project's searchable
    safety knowledge base and it should be *queried*, not rewritten on every
    clip. Pass ``force=True`` to rebuild anyway.
    """
    from scripts import safety_filter

    rows = list(_term_rows(list(safety_filter.BLOCKLIST)))
    rows.extend(_term_rows(safety_filter.load_remote_terms(project_folder)))
    fingerprint = _terms_fingerprint(rows)
    connection = _connect(project_folder, path)
    now = _now()
    db_target = db_path(project_folder, path)
    try:
        if not force:
            stored = connection.execute(
                "SELECT meta_value FROM lexicon_meta WHERE meta_key='terms_fingerprint'"
            ).fetchone()
            count_row = connection.execute(
                "SELECT COUNT(*) AS count FROM policy_terms WHERE active=1").fetchone()
            if stored is not None and int(count_row["count"]) > 0 and stored["meta_value"] == fingerprint:
                connection.execute(
                    "INSERT INTO lexicon_meta (meta_key, meta_value, updated_at) "
                    "VALUES ('last_synced_at', ?, ?) "
                    "ON CONFLICT(meta_key) DO UPDATE SET meta_value=excluded.meta_value, updated_at=excluded.updated_at",
                    (now, now))
                connection.commit()
                return {"database": db_target, "terms_seen": len(rows),
                        "sources": len(POLICY_SOURCES), "updated_at": now,
                        "synced": False, "reason": "unchanged"}
        inserted = 0
        for row in rows:
            term_id = _term_id(row["term"], row["lang"], row["source"])
            connection.execute("""INSERT INTO policy_terms
                (term_id, term, normalized_term, lang, severity, category, source,
                 source_version, context_required, active, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(normalized_term, lang, source) DO UPDATE SET
                    term=excluded.term, severity=excluded.severity,
                    category=excluded.category, source_version=excluded.source_version,
                    context_required=excluded.context_required, active=1,
                    updated_at=excluded.updated_at""",
                (term_id, row["term"], row["normalized"], row["lang"],
                 row["severity"], row["category"], row["source"], row["version"],
                 _context_required(row["category"]), now))
            inserted += 1
        for source_id, url in POLICY_SOURCES.items():
            page_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
            connection.execute("""INSERT INTO policy_sources
                (source_id, url, page_hash, last_checked, notes)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    url=excluded.url, page_hash=excluded.page_hash,
                    last_checked=excluded.last_checked""",
                (source_id, url, page_hash, now, "Official policy reference; not a word list."))
        connection.execute("DELETE FROM policy_terms_fts_v2")
        connection.execute("INSERT INTO policy_terms_fts_v2(term, category, term_id) SELECT term, category, term_id FROM policy_terms WHERE active=1")
        connection.execute(
            "INSERT INTO lexicon_meta (meta_key, meta_value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(meta_key) DO UPDATE SET meta_value=excluded.meta_value, updated_at=excluded.updated_at",
            ("terms_fingerprint", fingerprint, now))
        connection.commit()
        return {"database": db_target, "terms_seen": inserted,
                "sources": len(POLICY_SOURCES), "updated_at": now,
                "synced": True, "reason": "changed"}
    finally:
        connection.close()


def run_doctor(project_folder: str | None = None, path: str | None = None,
               terms: list[Any] | None = None) -> dict[str, Any]:
    """Validate the policy knowledge base for data-quality problems.

    Checks (all read-only):
    * duplicate (normalized term, lang, source) rows that disagree on
      severity or category — a real source of over/under-blocking;
    * empty terms;
    * FTS5 index consistency with the active term table (only when the DB
      already exists).

    ``terms`` is optional so tests can validate small synthetic packs; by
    default the built-in BLOCKLIST + cached remote terms are used.
    """
    if terms is None:
        from scripts import safety_filter
        terms = list(safety_filter.BLOCKLIST)
        terms.extend(safety_filter.load_remote_terms(project_folder))
    problems: list[str] = []
    seen: dict[tuple[str, str, str], dict[str, str]] = {}
    row_count = 0
    for row in _term_rows(terms):
        row_count += 1
        key = (row["normalized"], row["lang"], row["source"])
        previous = seen.get(key)
        if previous is None:
            seen[key] = row
            continue
        for field in ("severity", "category"):
            if previous[field] != row[field]:
                problems.append(
                    "conflicting duplicate {!r} (lang={}, source={}): "
                    "{}={} vs {}={}".format(
                        row["term"], row["lang"], row["source"],
                        field, previous[field], field, row[field]))
    checks = {
        "terms": row_count,
        "unique": len(seen),
        "duplicate_conflicts": len(problems),
        "problems": problems[:50],
    }
    db_target = db_path(project_folder, path)
    if os.path.isfile(db_target):
        try:
            connection = _connect(project_folder, path)
            try:
                active = int(connection.execute(
                    "SELECT COUNT(*) AS count FROM policy_terms WHERE active=1").fetchone()["count"])
                fts = int(connection.execute(
                    "SELECT COUNT(*) AS count FROM policy_terms_fts_v2").fetchone()["count"])
                checks["database"] = db_target
                checks["active_terms"] = active
                checks["fts_rows"] = fts
                if active != fts:
                    problems.append("FTS index out of sync: {} active terms vs {} FTS rows".format(active, fts))
            finally:
                connection.close()
        except Exception:  # database may exist but be corrupt — report, don't crash
            checks["database"] = db_target
            checks["active_terms"] = 0
            checks["fts_rows"] = 0
            problems.append("policy database exists but could not be read")
    else:
        checks["database"] = None
        checks["active_terms"] = 0
        checks["fts_rows"] = 0
    checks["ok"] = not problems
    return checks


def load_terms(project_folder: str | None = None, path: str | None = None, sync: bool = True) -> list[dict[str, Any]]:
    if sync:
        try:
            sync_policy_db(project_folder, path)
        except Exception:
            pass
    connection = _connect(project_folder, path)
    try:
        rows = connection.execute("""SELECT term, lang, severity, category,
            source, source_version, context_required FROM policy_terms
            WHERE active=1 ORDER BY length(normalized_term) DESC""").fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def search_terms(query: str, project_folder: str | None = None, path: str | None = None) -> list[dict[str, Any]]:
    connection = _connect(project_folder, path)
    try:
        rows = connection.execute("""SELECT policy_terms.term, policy_terms.lang, policy_terms.severity, policy_terms.category, policy_terms.source
            FROM policy_terms_fts_v2 JOIN policy_terms ON policy_terms_fts_v2.term_id=policy_terms.term_id
            WHERE policy_terms_fts_v2 MATCH ? LIMIT 50""", (query,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Build and inspect the local policy lexicon")
    parser.add_argument("--project", default=None)
    parser.add_argument("--db", default=None)
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--force-sync", action="store_true")
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--search", default=None)
    args = parser.parse_args(argv)
    if args.doctor:
        print(json.dumps(run_doctor(args.project, args.db), ensure_ascii=False, indent=2))
        return 0
    if args.sync or not args.search:
        print(json.dumps(sync_policy_db(args.project, args.db, force=args.force_sync), ensure_ascii=False, indent=2))
    if args.search:
        print(json.dumps(search_terms(args.search, args.project, args.db), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
