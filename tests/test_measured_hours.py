import json

from scripts import performance_loop, publish_scheduler


def _write_insights(tmp_path, best_hours):
    (tmp_path / "performance_insights.json").write_text(
        json.dumps({"best_hours": best_hours}), encoding="utf-8")


def test_best_hours_ranks_by_mean_views_with_min_samples(tmp_path):
    measured = [
        {"features": {"publish_hour": 20}, "metrics": {"views": 100}},
        {"features": {"publish_hour": 20}, "metrics": {"views": 300}},
        {"features": {"publish_hour": 9}, "metrics": {"views": 1000}},
        {"features": {"publish_hour": 9}, "metrics": {"views": 1200}},
        {"features": {"publish_hour": 3}, "metrics": {"views": 5000}},
    ]
    assert performance_loop._best_hours(measured) == [9, 20]


def test_analyze_report_exposes_best_hours(tmp_path, monkeypatch):
    (tmp_path / "publish_history.jsonl").write_text(
        "\n".join(json.dumps({
            "status": "uploaded", "video_id": "v%d" % i,
            "publish_at": "2026-09-0%dT2%d:00:00+00:00" % (
                (i // 2) + 1, i % 2),
        }) for i in range(4)) + "\n", encoding="utf-8")
    monkeypatch.setattr(performance_loop, "_fetch_real_metrics", lambda ids: {
        "v0": {"views": 100}, "v1": {"views": 200},
        "v2": {"views": 900}, "v3": {"views": 1100},
    })
    report = performance_loop.analyze(str(tmp_path), fetch_live=True)
    assert report["best_hours"] == [21, 20]
    assert any("best publish hours" in i for i in report["insights"])


def test_scheduler_prefers_measured_hours_over_defaults(tmp_path):
    _write_insights(tmp_path, [6, 23])
    clips = []
    for i in range(2):
        f = tmp_path / ("c%d.mp4" % i)
        f.write_bytes(b"x")
        clips.append(str(f))
    measured = publish_scheduler.load_measured_hours(str(tmp_path))
    assert measured == [6, 23]
    plan = publish_scheduler.build_plan(clips, platform="youtube",
                                        measured_hours=measured)
    hours = [item["hour"] for item in plan["plan"]]
    assert hours and set(hours) <= {6, 23}


def test_scheduler_falls_back_without_insights(tmp_path):
    assert publish_scheduler.load_measured_hours(str(tmp_path)) == []
    assert publish_scheduler.load_measured_hours(None) == []
