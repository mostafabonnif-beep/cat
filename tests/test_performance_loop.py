import json

from scripts import performance_loop


def _history(tmp_path, rows):
    (tmp_path / "publish_history.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_empty_history_reports_nothing_linked(tmp_path):
    report = performance_loop.analyze(str(tmp_path), fetch_live=False)
    assert report["published_count"] == 0
    assert report["insights"]


def test_links_published_events_with_scorecard_features(tmp_path, monkeypatch):
    (tmp_path / "risk_scorecard.json").write_text(json.dumps({
        "segments": [{"index": 2, "duration": 45, "hook_strength": 0.8}],
    }), encoding="utf-8")
    _history(tmp_path, [
        {"video_id": "abc", "status": "uploaded", "video": "clip_002.mp4",
         "title": "T2", "platform": "youtube", "timestamp": "2026-08-01T18:00:00Z"},
        {"video_id": "", "status": "uploaded", "video": "no_id.mp4"},
        {"video_id": "xyz", "status": "failed", "video": "clip_003.mp4"},
    ])
    monkeypatch.setattr(performance_loop, "_fetch_real_metrics",
                        lambda ids: {"abc": {"views": 120, "avg_view_duration": 33.0,
                                             "likes": 5, "shares": 1}})
    report = performance_loop.analyze(str(tmp_path), fetch_live=True)
    assert report["published_count"] == 1
    assert report["with_metrics"] == 1
    clip = report["clips"][0]
    assert clip["video_id"] == "abc"
    assert clip["features"]["duration"] == 45
    assert any("correlation" in i or "no feature" in i or "meaningful" in i
               for i in report["insights"])


def test_insights_reported_when_metrics_missing(tmp_path, monkeypatch):
    _history(tmp_path, [{"video_id": "abc", "status": "uploaded",
                         "video": "clip_001.mp4", "title": "T",
                         "platform": "youtube", "timestamp": "2026-08-01T18:00:00Z"}])
    monkeypatch.setattr(performance_loop, "_fetch_real_metrics", lambda ids: {})
    report = performance_loop.analyze(str(tmp_path), fetch_live=True)
    assert report["published_count"] == 1
    assert report["with_metrics"] == 0
    assert report["insights"]
