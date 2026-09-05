
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


def test_find_visual_matches_excludes_current_project(tmp_path, monkeypatch):
    registry = tmp_path / "ledger.sqlite3"
    previous = tmp_path / "previous"
    current = tmp_path / "current"
    previous.mkdir()
    current.mkdir()
    previous_video = previous / "previous.mp4"
    current_video = current / "current.mp4"
    previous_video.write_bytes(b"video")
    current_video.write_bytes(b"video")
    from scripts import content_guard
    monkeypatch.setattr(content_guard, "_visual_fingerprint_for", lambda _path: {"hashes": "1|2|3"})
    monkeypatch.setattr("scripts.originality.video_fingerprint", lambda _path: [1, 2, 3])
    assert content_ledger.record_clip_audit(str(previous), 0, {"overall": "low"}, str(previous_video), registry_path=str(registry))
    assert content_ledger.find_visual_matches(str(current), str(current_video), registry_path=str(registry))
