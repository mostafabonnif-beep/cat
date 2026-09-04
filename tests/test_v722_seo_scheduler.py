# -*- coding: utf-8 -*-
"""Tests for v7.22: SEO titles engine + publish scheduler."""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import publish_scheduler, seo_titles


class TestScoreTitle:
    def test_empty(self):
        assert seo_titles.score_title("")["score"] == 0.0

    def test_good_title_scores_high(self):
        r = seo_titles.score_title("كيف تكسب المال من الإنترنت في 5 خطوات؟",
                                   ["كسب المال"])
        assert r["score"] > 40

    def test_all_caps_penalized(self):
        low = seo_titles.score_title("How to make money online in 5 steps")
        caps = seo_titles.score_title("HOW TO MAKE MONEY ONLINE IN 5 STEPS")
        assert caps["score"] < low["score"]

    def test_keyword_presence_helps(self):
        base = seo_titles.score_title("طريقة كسب المال بسهولة", [])
        with_kw = seo_titles.score_title("طريقة كسب المال بسهولة", ["كسب المال"])
        assert with_kw["score"] >= base["score"]

    def test_breakdown_keys(self):
        r = seo_titles.score_title("أفضل طريقة للربح")
        assert set(r["breakdown"]) == {"length", "hook", "keywords", "penalty"}


class TestGenerateTitles:
    def test_returns_ranked_unique(self):
        titles = seo_titles.generate_titles("الربح من الانترنت", count=6)
        assert 1 <= len(titles) <= 6
        keys = [t["title"] for t in titles]
        assert len(keys) == len(set(keys))
        scores = [t["score"] for t in titles]
        assert scores == sorted(scores, reverse=True)

    def test_empty_topic_fallback(self):
        titles = seo_titles.generate_titles("", count=3)
        assert titles  # uses the fallback token

    def test_deterministic_per_topic(self):
        a = seo_titles.generate_titles("الربح من الانترنت", count=4)
        b = seo_titles.generate_titles("الربح من الانترنت", count=4)
        assert [t["title"] for t in a] == [t["title"] for t in b]


class TestFetchSuggestions:
    def test_offline_returns_empty(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("no network")
        monkeypatch.setattr(seo_titles.urllib.request, "urlopen", boom)
        assert seo_titles.fetch_suggestions("كسب المال") == []

    def test_parses_response(self, monkeypatch):
        class FakeResp:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self, *a):
                return b'["a", ["first suggestion", "second suggestion"], []]'
        monkeypatch.setattr(seo_titles.urllib.request, "urlopen",
                            lambda *a, **k: FakeResp())
        out = seo_titles.fetch_suggestions("money", language="en")
        assert out == ["first suggestion", "second suggestion"]

    def test_window_google_ac_h_wrapper(self, monkeypatch):
        class FakeResp:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self, *a):
                return (b'window.google.ac.h(["make money",'
                        b'[["make money online",0,[512]],'
                        b'["make money with ai",0,[512]]]])')
        monkeypatch.setattr(seo_titles.urllib.request, "urlopen",
                            lambda *a, **k: FakeResp())
        out = seo_titles.fetch_suggestions("make money", language="en")
        assert out == ["make money online", "make money with ai"]

    def test_stringified_suggestion_items(self, monkeypatch):
        class FakeResp:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self, *a):
                return b'["q", ["[\'make money online\', 0, [512]]", "[\'make money from home\', 0, [512]]"], []]'
        monkeypatch.setattr(seo_titles.urllib.request, "urlopen",
                            lambda *a, **k: FakeResp())
        out = seo_titles.fetch_suggestions("make money", language="en")
        assert out == ["make money online", "make money from home"]

    def test_deduplicates(self, monkeypatch):
        class FakeResp:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self, *a):
                return b'["a", ["x", "x", "y"], []]'
        monkeypatch.setattr(seo_titles.urllib.request, "urlopen",
                            lambda *a, **k: FakeResp())
        assert seo_titles.fetch_suggestions("x") == ["x", "y"]


class TestBestTimes:
    def test_windows_per_platform(self):
        assert "weekday" in seo_titles.best_time_windows("youtube")
        assert "weekend" in seo_titles.best_time_windows("tiktok")
        assert seo_titles.best_time_windows("unknown")["platform"] == "youtube"

    def test_slots_are_future_iso(self):
        slots = seo_titles.suggest_next_slots("youtube", count=3)
        assert len(slots) == 3
        for s in slots:
            dt = datetime.fromisoformat(s)
            assert dt.tzinfo is not None
            assert dt > datetime.now(timezone.utc)


class TestBuildPlan:
    def _clips(self, tmp_path, n=3):
        paths = []
        for i in range(n):
            p = tmp_path / f"clip{i}.mp4"
            p.write_bytes(b"x")
            paths.append(str(p))
        return paths

    def test_plan_spreads_clips(self, tmp_path):
        plan = publish_scheduler.build_plan(
            self._clips(tmp_path), platform="youtube", days=7,
            start_at="2026-09-07T08:00:00+00:00")
        assert plan["ok"] is True
        assert plan["count"] == 3
        times = [datetime.fromisoformat(x["publish_at"]) for x in plan["plan"]]
        assert times[0] < times[1] < times[2]
        # all slots are future, hourly, and inside the evening window hours
        assert all(t > datetime.now(timezone.utc) for t in times)
        assert all(17 <= t.hour <= 20 for t in times)

    def test_user_hours_override(self, tmp_path):
        plan = publish_scheduler.build_plan(
            self._clips(tmp_path), platform="youtube", user_hours=[9], days=7)
        assert all(datetime.fromisoformat(x["publish_at"]).hour == 9
                   for x in plan["plan"])

    def test_no_files(self, tmp_path):
        plan = publish_scheduler.build_plan([], platform="youtube")
        assert plan["ok"] is False

    def test_save_plan(self, tmp_path):
        plan = publish_scheduler.build_plan(self._clips(tmp_path), days=3)
        path = publish_scheduler.save_plan(plan, str(tmp_path))
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            assert json.load(f)["count"] == 3


class TestRunDaemon:
    def test_dry_run_no_upload(self, tmp_path, monkeypatch):
        (tmp_path / "c.mp4").write_bytes(b"x")
        plan = publish_scheduler.build_plan(
            [str(tmp_path / "c.mp4")], days=2)
        import types
        fake_webui = types.ModuleType("webui")
        fake_webui.publish_panel = types.SimpleNamespace(
            stream_upload_batch=lambda *a, **k: ["dry-run ok"])
        monkeypatch.setitem(sys.modules, "webui", fake_webui)
        monkeypatch.setitem(sys.modules, "webui.publish_panel",
                            fake_webui.publish_panel)
        summary = publish_scheduler.run_daemon(plan, dry_run=True)
        assert summary["ok"] is True
        assert summary["dry_run"] is True
        assert summary["results"][0]["status"] == "dry_run"

    def test_invalid_plan(self):
        summary = publish_scheduler.run_daemon({"ok": False, "error": "x"},
                                               dry_run=True)
        assert summary["ok"] is False
