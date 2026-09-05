import json

from scripts import review_queue


def test_review_queue_aggregates_automated_findings(tmp_path):
    (tmp_path / "risk_scorecard.json").write_text(json.dumps({
        "segments": [{"index": 0, "title": "Test", "overall_score": 80, "overall": "high", "start_time": 1, "end_time": 4, "axes": {}}]
    }), encoding="utf-8")
    report = review_queue.build_queue(str(tmp_path))
    assert report["total"] == 1
    assert report["high"] == 1
    assert (tmp_path / "review_queue.json").exists()
    assert (tmp_path / "review_queue.html").exists()
