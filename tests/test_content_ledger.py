import json

from scripts import content_ledger


def test_ledger_records_safety_findings_and_summary(tmp_path):
    report = {
        "platform": "youtube",
        "blocked_segments": [{
            "index": 2,
            "reasons": [{"code": "hate_speech", "severity": "high", "detail": "review"}],
        }],
    }
    assert content_ledger.record_safety_report(str(tmp_path), report) == 1
    assert content_ledger.record_safety_report(str(tmp_path), report) == 1
    summary = content_ledger.ledger_summary(str(tmp_path))
    assert summary["safety_findings"] == 1
    assert summary["clip_audits"] == 0
    assert summary["database"].endswith(".oussama_content_registry.sqlite3")


def test_ledger_records_clip_audit_without_media(tmp_path):
    audit = {"index": 0, "overall": "low", "overall_score": 5, "axes": {}}
    assert content_ledger.record_clip_audit(str(tmp_path), 0, audit) is True
    summary = content_ledger.ledger_summary(str(tmp_path))
    assert summary["clip_audits"] == 1
    with open(summary["database"], "rb") as handle:
        assert handle.read(16) == b"SQLite format 3\x00"
