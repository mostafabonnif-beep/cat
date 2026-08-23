"""Tests for the performance analytics module (scripts/analytics.py, Roadmap 5.4)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import analytics


def test_date_range_shape():
    start, end = analytics._date_range(28)
    assert start < end
    assert len(start) == 10 and len(end) == 10  # YYYY-MM-DD


def test_format_summary():
    s = {"days": 28, "start": "2026-07-13", "end": "2026-08-09",
         "metrics": {"views": 1234567, "estimatedMinutesWatched": 98765,
                     "averageViewDuration": 23.4, "likes": 12345,
                     "comments": 678, "shares": 99, "subscribersGained": 4321}}
    out = analytics.format_summary(s)
    assert "1.2M" in out
    assert "98.8k" in out
    assert "23.4s" in out


def test_format_top():
    t = {"days": 28, "top": [
        {"video_id": "x", "title": "My Short", "views": 500000,
         "watch_minutes": 12000.0, "avg_duration_s": 21.5}]}
    out = analytics.format_top(t)
    assert "My Short" in out
    assert "500.0k" in out


def test_format_trends_empty():
    out = analytics.format_trends({"days": 28, "points": []})
    assert "no data yet" in out


def test_build_report():
    r = analytics.build_report(summary={"a": 1}, top={"b": 2})
    assert r["summary"] == {"a": 1}
    assert r["top_videos"] == {"b": 2}
    assert "trends" not in r


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return self._data


class FakeAnalyticsService:
    def __init__(self, resp):
        self._resp = resp

    def reports(self):
        return self

    def query(self, **kwargs):
        self.kwargs = kwargs
        return FakeResponse(self._resp)


def test_fetch_summary_builds_metrics():
    svc = FakeAnalyticsService({
        "columnHeaders": [{"name": "views"}, {"name": "likes"}],
        "rows": [[100, 5]],
    })
    summary = analytics.fetch_summary(svc, days=28)
    assert summary["metrics"]["views"] == 100
    assert summary["metrics"]["likes"] == 5
    assert svc.kwargs["metrics"].startswith("views,")
    assert svc.kwargs["ids"] == "channel==MINE"


def test_fetch_top_videos_empty():
    svc = FakeAnalyticsService({"rows": []})
    top = analytics.fetch_top_videos(svc, None, days=7, limit=10)
    assert top["top"] == []


def test_fetch_top_videos_with_titles():
    svc = FakeAnalyticsService({
        "columnHeaders": [{"name": "video"}, {"name": "views"},
                          {"name": "estimatedMinutesWatched"},
                          {"name": "averageViewDuration"}],
        "rows": [["vid1", 200, 300, 15.0]],
    })

    class FakeYT:
        def videos(self):
            return self

        def list(self, **kwargs):
            assert kwargs["part"] == "snippet"
            return FakeResponse({"items": [
                {"id": "vid1", "snippet": {"title": "Great Short"}}]})

    top = analytics.fetch_top_videos(svc, FakeYT(), days=7, limit=5)
    assert top["top"][0]["title"] == "Great Short"
    assert top["top"][0]["views"] == 200


def test_fetch_trends():
    svc = FakeAnalyticsService({
        "columnHeaders": [{"name": "day"}, {"name": "views"},
                          {"name": "estimatedMinutesWatched"}],
        "rows": [["2026-08-01", 10, 20.0], ["2026-08-02", 30, 60.0]],
    })
    trends = analytics.fetch_trends(svc, days=7)
    assert len(trends["points"]) == 2
    assert trends["points"][1]["views"] == 30
    assert trends["points"][0]["date"] == "2026-08-01"


def test_load_credentials_missing_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("YT_CLIENT_SECRETS_FILE", str(tmp_path / "nope.json"))
    monkeypatch.setenv(analytics.TOKEN_ENV, str(tmp_path / "nope_token.json"))
    with pytest.raises(RuntimeError) as ei:
        analytics.load_credentials()
    assert "OAuth" in str(ei.value)


def test_main_check_missing_secrets(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("YT_CLIENT_SECRETS_FILE", str(tmp_path / "nope.json"))
    monkeypatch.setenv(analytics.TOKEN_ENV, str(tmp_path / "nope_token.json"))
    assert analytics.main(["--check"]) == 1
    out = capsys.readouterr().out
    assert "client_secrets" in out


def test_main_no_command_prints_help(capsys):
    assert analytics.main([]) == 0
