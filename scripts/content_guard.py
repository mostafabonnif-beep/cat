"""Automatic provenance and duplicate-content guard for OUSSAMA Cutter.

The guard is intentionally local and conservative.  It does not claim to
predict YouTube enforcement; it prevents the application from publishing the
same rendered file or the same source time-window again on the same platform,
and it records enough provenance to explain every decision.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any

REGISTRY_FILENAME = ".oussama_content_registry.sqlite3"
REPORT_FILENAME = "content_guard_report.json"
SUCCESS_STATUSES = {"uploaded", "scheduled"}
SCHEMA_VERSION = 2
MAX_SOURCE_PUBLISHES_PER_DAY = 8
OVERLAP_BLOCK_RATIO = 0.85
FINGERPRINT_NAME = "visual_fingerprint"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalise_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"\s+", " ", text)


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def file_fingerprint(path: str | None, chunk_size: int = 1024 * 1024) -> str | None:
    """Return a content hash without storing media bytes."""
    if not path or not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while chunk := handle.read(chunk_size):
                digest.update(chunk)
    except OSError:
        return None
    return "sha256:" + digest.hexdigest()


def _registry_path(project_folder: str, registry_path: str | None = None) -> str:
    if registry_path:
        return os.path.abspath(os.fspath(registry_path))
    configured = os.getenv("OUSSAMA_CONTENT_REGISTRY", "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    project = os.path.abspath(os.fspath(project_folder))
    parent = os.path.dirname(project)
    # Production projects live under D:\\SS\\VIRALS (or an equivalent
    # `VIRALS` root). Keep ad-hoc/legacy projects isolated unless the user
    # explicitly configures a shared registry; this also prevents unrelated
    # temporary projects from influencing one another.
    if os.path.basename(parent).lower() in {"virals", "oussama", "projects"}:
        return os.path.join(parent, REGISTRY_FILENAME)
    return os.path.join(project, REGISTRY_FILENAME)


def _connect(project_folder: str, registry_path: str | None = None) -> sqlite3.Connection:
    path = _registry_path(project_folder, registry_path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS published_content (
            record_key TEXT PRIMARY KEY,
            fingerprint TEXT,
            source_identity TEXT,
            source_start REAL,
            source_end REAL,
            project_path TEXT NOT NULL,
            platform TEXT NOT NULL,
            status TEXT NOT NULL,
            title TEXT,
            video_name TEXT,
            created_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_published_fingerprint "
        "ON published_content(fingerprint, platform, status)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_published_source "
        "ON published_content(source_identity, platform, status, created_at)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS channel_incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            kind TEXT NOT NULL,
            detail TEXT NOT NULL,
            locked INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_channel_incidents "
        "ON channel_incidents(platform, locked, created_at)"
    )
    connection.commit()
    return connection


def _load_manifest(project_folder: str) -> dict[str, Any]:
    path = os.path.join(project_folder, "project_manifest.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def source_identity(project_folder: str) -> str | None:
    """Resolve a stable source identity from manifest, URL, or local input."""
    manifest = _load_manifest(project_folder)
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    for key in ("path", "local_path", "original_path"):
        candidate = source.get(key)
        if isinstance(candidate, str) and os.path.isfile(candidate):
            fingerprint = file_fingerprint(candidate)
            if fingerprint:
                return fingerprint
    for key in ("url", "video_url", "source_url"):
        candidate = source.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return "url:" + hashlib.sha256(_normalise_text(candidate).encode("utf-8")).hexdigest()
    for name in ("input.mp4", "input_video.mp4"):
        candidate = os.path.join(project_folder, name)
        fingerprint = file_fingerprint(candidate)
        if fingerprint:
            return fingerprint
    return None


def _segment_data(project_folder: str, index: int | None, segment: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(segment, dict):
        return segment
    if index is None:
        return {}
    path = os.path.join(project_folder, "viral_segments.txt")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        segments = data.get("segments", []) if isinstance(data, dict) else []
        return segments[index] if isinstance(segments, list) and 0 <= index < len(segments) and isinstance(segments[index], dict) else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError, IndexError):
        return {}


def _time_window(project_folder: str, index: int | None, segment: dict[str, Any] | None = None) -> tuple[float | None, float | None]:
    item = _segment_data(project_folder, index, segment)
    try:
        start = float(item.get("start_time"))
        end = float(item.get("end_time"))
        if end >= start:
            return round(start, 3), round(end, 3)
    except (TypeError, ValueError):
        pass
    return None, None


def _record_key(platform: str, fingerprint: str | None, source: str | None,
                start: float | None, end: float | None, project: str) -> str:
    raw = "|".join(str(value or "") for value in (
        platform, fingerprint, source, start, end, os.path.abspath(project)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _visual_fingerprint_for(video_path):
    """Best-effort perceptual fingerprint of a rendered clip (v7.18)."""
    try:
        from scripts.originality import fingerprint_key, video_fingerprint
        hashes = video_fingerprint(video_path)
        key = fingerprint_key(hashes)
        if hashes and key:
            # Store the raw hashes so similarity can be computed later.
            return {"key": key, "hashes": "|".join(str(h) for h in hashes)}
    except Exception:
        pass
    return None


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    try:
        value["metadata"] = json.loads(value.pop("metadata_json", "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        value["metadata"] = {}
    return value


def _overlap_ratio(start_a: float | None, end_a: float | None,
                   start_b: float | None, end_b: float | None) -> float:
    if None in (start_a, end_a, start_b, end_b):
        return 0.0
    duration_a = max(0.001, float(end_a) - float(start_a))
    duration_b = max(0.001, float(end_b) - float(start_b))
    intersection = max(0.0, min(float(end_a), float(end_b)) - max(float(start_a), float(start_b)))
    return intersection / min(duration_a, duration_b)


_POLICY_ERROR_MARKERS = (
    "community guidelines", "hate speech", "spam policy", "policy violation",
    "channel suspended", "channel terminated", "account terminated",
    "strike", "إرشادات المنتدى", "الكراهية", "إنذار", "تعليق القناة",
    "إغلاق القناة",
)


def channel_status(project_folder: str, platform: str = "youtube",
                   registry_path: str | None = None) -> dict[str, Any]:
    """Return the local automatic circuit-breaker state for a platform."""
    connection = _connect(project_folder, registry_path)
    try:
        rows = connection.execute(
            "SELECT * FROM channel_incidents WHERE platform=? ORDER BY created_at DESC",
            (_normalise_text(platform) or "youtube",),
        ).fetchall()
        incidents = [_row_dict(row) for row in rows]
        return {
            "locked": any(bool(row.get("locked")) for row in incidents),
            "incidents": incidents[:20],
            "count": len(incidents),
        }
    finally:
        connection.close()


def record_channel_incident(project_folder: str, platform: str, kind: str,
                            detail: str, *, lock: bool = False,
                            registry_path: str | None = None) -> bool:
    """Persist a platform incident and optionally lock future automation."""
    connection = _connect(project_folder, registry_path)
    try:
        connection.execute(
            "INSERT INTO channel_incidents(platform, kind, detail, locked, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (_normalise_text(platform) or "youtube", _normalise_text(kind) or "unknown",
             str(detail or "")[:2000], int(bool(lock)), _now()),
        )
        connection.commit()
        return True
    finally:
        connection.close()


def acknowledge_channel_risk(project_folder: str, platform: str = "youtube",
                               registry_path: str | None = None) -> int:
    """Unlock only after an explicit local acknowledgement by the owner."""
    connection = _connect(project_folder, registry_path)
    try:
        cursor = connection.execute(
            "UPDATE channel_incidents SET locked=0 WHERE platform=? AND locked=1",
            (_normalise_text(platform) or "youtube",),
        )
        connection.commit()
        return int(cursor.rowcount or 0)
    finally:
        connection.close()


def record_platform_error(project_folder: str, platform: str, error: Any,
                          registry_path: str | None = None) -> bool:
    """Lock automation only for explicit policy/strike/channel-state errors."""
    detail = str(error or "")
    lowered = detail.lower()
    if not any(marker.lower() in lowered for marker in _POLICY_ERROR_MARKERS):
        return False
    return record_channel_incident(
        project_folder, platform, "platform_policy_error", detail,
        lock=True, registry_path=registry_path)


def assess_clip(project_folder: str, index: int | None = None, *, title: str = "",
                video_path: str | None = None, platform: str = "youtube",
                segment: dict[str, Any] | None = None,
                registry_path: str | None = None,
                perceptual: bool = True) -> dict[str, Any]:
    """Return a deterministic, local decision for a clip.

    The result is JSON-safe and has ``allowed``, ``action``, ``reasons`` and
    ``evidence`` keys.  A successful prior upload of the same file or source
    window is a hard block.  A source that has already produced too many
    successful uploads in 24 hours is also blocked as a production circuit
    breaker, preventing accidental mass publication.

    ``perceptual`` (default True) additionally compares the rendered clip
    against previously published clips of the same source using visual
    fingerprints — catching re-encoded / lightly-edited duplicates that an
    exact file hash would miss (v7.18).
    """
    project_folder = os.path.abspath(os.fspath(project_folder))
    platform = _normalise_text(platform) or "youtube"
    fingerprint = file_fingerprint(video_path)
    source = source_identity(project_folder)
    start, end = _time_window(project_folder, index, segment)
    reasons: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {
        "database": _registry_path(project_folder, registry_path),
        "fingerprint": fingerprint,
        "source_identity": source,
        "source_start": start,
        "source_end": end,
    }
    connection = _connect(project_folder, registry_path)
    try:
        state = channel_status(project_folder, platform, registry_path)
        evidence["channel"] = {
            "locked": state["locked"],
            "incident_count": state["count"],
        }
        if state["locked"]:
            reasons.append({
                "source": "content_guard",
                "code": "channel_circuit_breaker",
                "severity": "high",
                "detail": "تم إيقاف الرفع الآلي: سجل القناة يحتوي حادثة سياسة/إنذار؛ راجعها قبل المتابعة.",
            })
        exact = []
        if fingerprint:
            exact = connection.execute(
                "SELECT * FROM published_content WHERE fingerprint=? AND platform=? "
                "AND status IN ('uploaded','scheduled') ORDER BY created_at DESC",
                (fingerprint, platform),
            ).fetchall()
        if exact:
            previous = _row_dict(exact[0])
            reasons.append({
                "source": "content_guard",
                "code": "exact_file_already_published",
                "severity": "high",
                "detail": "نفس ملف الفيديو نُشر سابقاً على {} ({}).".format(
                    platform, previous.get("video_name") or "سجل سابق"),
            })
            evidence["exact_match"] = previous

        if source and start is not None and end is not None:
            rows = connection.execute(
                "SELECT * FROM published_content WHERE source_identity=? AND platform=? "
                "AND status IN ('uploaded','scheduled') ORDER BY created_at DESC",
                (source, platform),
            ).fetchall()
            overlaps = []
            for row in rows:
                old = _row_dict(row)
                ratio = _overlap_ratio(start, end, old.get("source_start"), old.get("source_end"))
                if ratio >= OVERLAP_BLOCK_RATIO:
                    old["overlap_ratio"] = round(ratio, 3)
                    overlaps.append(old)
            if overlaps:
                reasons.append({
                    "source": "content_guard",
                    "code": "source_window_already_published",
                    "severity": "high",
                    "detail": "نافذة المصدر نفسها نُشرت سابقاً؛ تم منع النسخة المكررة تلقائياً.",
                })
                evidence["source_overlap_match"] = overlaps[0]

            # v7.18: perceptual near-duplicate detection — catches the same
            # visual content re-encoded, renamed, or lightly edited.
            if perceptual and video_path and rows:
                try:
                    from scripts.originality import assess_against_registry
                    candidates = [_row_dict(row) for row in rows]
                    visual = assess_against_registry(
                        project_folder, video_path, candidates)
                    evidence["perceptual"] = visual
                    if visual.get("verdict") == "duplicate":
                        reasons.append({
                            "source": "content_guard",
                            "code": "perceptual_near_duplicate",
                            "severity": "high",
                            "detail": "محتوى بصري مطابق تقريباً لمقطع سبق نشره ({}, تشابه {}%)؛ يُمنع التكرار حفاظاً على القناة.".format(
                                (visual.get("matched") or {}).get("video_name", "سجل سابق"),
                                int((visual.get("similarity") or 0) * 100)),
                        })
                        evidence["perceptual_match"] = visual.get("matched")
                except Exception:
                    pass

            since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM published_content WHERE source_identity=? "
                "AND platform=? AND status IN ('uploaded','scheduled') AND created_at>=?",
                (source, platform, since),
            ).fetchone()
            count = int(row["count"] if row else 0)
            evidence["source_publishes_last_24h"] = count
            if count >= MAX_SOURCE_PUBLISHES_PER_DAY:
                reasons.append({
                    "source": "content_guard",
                    "code": "source_publish_rate_limit",
                    "severity": "high",
                    "detail": "تم إيقاف النشر الآلي: المصدر تجاوز حد {} مقاطع خلال 24 ساعة.".format(
                        MAX_SOURCE_PUBLISHES_PER_DAY),
                })
    finally:
        connection.close()
    action = "block" if reasons else "allow"
    return {
        "allowed": not reasons,
        "action": action,
        "reasons": reasons,
        "evidence": evidence,
        "policy_version": SCHEMA_VERSION,
        "title_key": _sha256_bytes(_normalise_text(title).encode("utf-8")) if title else None,
    }


def _write_json_atomic(path: str, value: dict[str, Any]) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".content-guard-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass


def filter_segments(project_folder: str, segments: list[dict[str, Any]] | None,
                    *, platform: str = "youtube", registry_path: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Remove duplicate and policy-ambiguous candidates before export.

    ``review`` is treated as a block here on purpose: the fully automatic mode
    must not send context-dependent hate/threat language to YouTube. The report
    records the reason so the user can re-edit the source or subtitle text.
    """
    kept: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    transcript = []
    try:
        from scripts.safety_filter import load_transcript, segment_text
        from scripts.semantic_safety import analyze_text
        transcript = load_transcript(project_folder)
    except Exception:
        segment_text = None
        analyze_text = None
    for index, segment in enumerate(segments or []):
        verdict = assess_clip(project_folder, index, title=segment.get("title", ""),
                              platform=platform, segment=segment,
                              registry_path=registry_path)
        start, end = _time_window(project_folder, index, segment)
        batch_duplicate = None
        for previous in kept:
            previous_start, previous_end = _time_window(
                project_folder, None, previous)
            if _overlap_ratio(start, end, previous_start, previous_end) >= OVERLAP_BLOCK_RATIO:
                batch_duplicate = previous
                break
        if batch_duplicate is not None:
            verdict["reasons"].append({
                "source": "content_guard",
                "code": "batch_duplicate_window",
                "severity": "high",
                "detail": "نافذة هذا المرشح تتداخل بشدة مع مرشح آخر في الدفعة؛ أُبقي مرشحاً واحداً فقط.",
            })
            verdict["evidence"]["batch_duplicate_of"] = batch_duplicate.get("title", "")
            verdict["allowed"] = False
        if segment_text is not None and analyze_text is not None:
            text = segment_text(segment, transcript)
            semantic = analyze_text(" ".join([
                text, str(segment.get("title", "") or ""),
                str(segment.get("caption", "") or ""),
                str(segment.get("reasoning", "") or ""),
            ]))
            if semantic.get("action") in {"block", "review"}:
                verdict["reasons"].append({
                    "source": "content_guard",
                    "code": "semantic_policy_{}".format(semantic.get("action")),
                    "severity": "high",
                    "detail": "تم إيقاف المرشح آلياً: {}".format(
                        semantic.get("explanation", "يتطلب السياق مراجعة")),
                })
                verdict["evidence"]["semantic"] = semantic
                verdict["allowed"] = False
        if verdict["allowed"]:
            kept.append(segment)
        else:
            blocked.append({"index": index, "title": segment.get("title", ""),
                            "reasons": verdict["reasons"], "evidence": verdict["evidence"]})
    report = {
        "generated_at": _now(),
        "policy_version": SCHEMA_VERSION,
        "platform": platform,
        "total": len(segments or []),
        "kept": len(kept),
        "blocked": len(blocked),
        "blocked_segments": blocked,
        "database": _registry_path(project_folder, registry_path),
    }
    _write_json_atomic(os.path.join(project_folder, REPORT_FILENAME), report)
    try:
        from scripts import content_ledger
        content_ledger.record_safety_report(project_folder, report, registry_path)
    except Exception:
        pass
    return kept, report


def record_publish(project_folder: str, platform: str, video_path: str,
                   title: str = "", status: str = "uploaded", index: int | None = None,
                   result: dict[str, Any] | None = None,
                   registry_path: str | None = None) -> bool:
    """Record only a successful/scheduled publish; Dry Run is never registered."""
    status = _normalise_text(status)
    if status not in SUCCESS_STATUSES:
        return False
    source = source_identity(project_folder)
    start, end = _time_window(project_folder, index)
    fingerprint = file_fingerprint(video_path)
    key = _record_key(platform, fingerprint, source, start, end, project_folder)
    metadata = dict(result or {})
    metadata.pop("access_token", None)
    metadata.pop("refresh_token", None)
    # v7.18: attach the perceptual fingerprint so future clips of the same
    # source can be checked for near-duplicates even after re-encoding.
    visual = _visual_fingerprint_for(video_path)
    if visual:
        metadata[FINGERPRINT_NAME] = visual["hashes"]
    connection = _connect(project_folder, registry_path)
    try:
        connection.execute(
            """INSERT OR REPLACE INTO published_content
            (record_key, fingerprint, source_identity, source_start, source_end,
             project_path, platform, status, title, video_name, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, fingerprint, source, start, end, os.path.abspath(project_folder),
             _normalise_text(platform) or "youtube", status, str(title or "")[:200],
             os.path.basename(video_path or ""), _now(),
             json.dumps(metadata, ensure_ascii=False)),
        )
        connection.commit()
        return True
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Automatic OUSSAMA content provenance guard")
    parser.add_argument("--project", required=True)
    parser.add_argument("--index", type=int, default=None)
    parser.add_argument("--video", default=None)
    parser.add_argument("--platform", default="youtube")
    parser.add_argument("--title", default="")
    parser.add_argument(
        "--acknowledge-channel-risk", action="store_true",
        help="Explicitly unlock a locally recorded channel policy incident after review",
    )
    args = parser.parse_args(argv)
    if args.acknowledge_channel_risk:
        count = acknowledge_channel_risk(args.project, args.platform)
        print("تمت إزالة قفل {} حادثة بعد الإقرار المحلي؛ راجع YouTube Studio قبل الرفع.".format(count))
        return 0
    verdict = assess_clip(args.project, args.index, title=args.title,
                          video_path=args.video, platform=args.platform)
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0 if verdict["allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
