import json
from datetime import datetime, timezone

from scripts import performance_loop


def _history(tmp_path, rows):
    (tmp_path / "publish_history.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_publish_hour_extracts_local_hour():
    moment = datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc)
    value = performance_loop._publish_hour(moment.isoformat())
    assert 0 <= value <= 23


def test_publish_hour_handles_bad_values():
    assert performance_loop._publish_hour("") is None
    assert performance_loop._publish_hour(None) is None
    assert performance_loop._publish_hour("not-a-date") is None


def test_publish_hour_joins_features(tmp_path, monkeypatch):
    (tmp_path / "publish_history.jsonl").write_text(json.dumps({
        "video_id": "abc", "status": "uploaded", "video": "clip_001.mp4",
        "title": "T", "platform": "youtube",
        "timestamp": "2026-08-01T18:00:00Z",
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(performance_loop, "_fetch_real_metrics",
                        lambda ids: {"abc": {"views": 10, "avg_view_duration": 5.0,
                                             "likes": 0, "shares": 0}})
    report = performance_loop.analyze(str(tmp_path), fetch_live=True)
    clip = report["clips"][0]
    assert isinstance(clip["features"].get("publish_hour"), int)
