# -*- coding: utf-8 -*-
"""Tests for the v7.20 live-stream downloader (wait-until-ended)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from scripts import download_live as dl


class TestClassify:
    def test_ended_info(self):
        info = {"is_live": False, "was_live": True, "live_status": "was_live",
                "id": "abc", "title": "VOD", "url": "x"}
        s = dl._classify_info(info, "url")
        assert s["status"] == "ended"

    def test_live_info(self):
        s = dl._classify_info({"is_live": True, "live_status": "is_live"}, "url")
        assert s["status"] == "live"

    def test_upcoming_info(self):
        s = dl._classify_info({"is_upcoming": True, "live_status": "is_upcoming"}, "url")
        assert s["status"] == "upcoming"

    def test_not_live_info(self):
        s = dl._classify_info({}, "url")
        assert s["status"] == "not_live"

    def test_error_upcoming_message(self):
        s = dl._classify_error(
            Exception("ERROR: This live event will begin in a few moments."), "url")
        assert s["status"] == "upcoming"

    def test_error_unknown_message(self):
        s = dl._classify_error(Exception("Video unavailable"), "url")
        assert s["status"] == "unknown"


class TestWaitUntilEnded:
    def _make_fetch(self, states):
        """Return a fetch_status replacement that plays back a state list."""
        it = iter(states)

        def fake_fetch(url, cookies_from_browser=None, cookies_file=None):
            try:
                return {"status": next(it), "url": url}
            except StopIteration:
                return {"status": "ended", "url": url}
        return fake_fetch

    def test_waits_until_ended(self, monkeypatch):
        seen = []
        monkeypatch.setattr(dl, "fetch_status",
                            self._make_fetch(["live", "live", "ended"]))
        monkeypatch.setattr(dl.time, "sleep", lambda s: seen.append(s))
        result = dl.wait_until_ended("url", poll_seconds=5, max_wait_seconds=1000)
        assert result["status"] == "ended"
        assert seen == [5, 5]

    def test_upcoming_then_ended(self, monkeypatch):
        monkeypatch.setattr(dl, "fetch_status",
                            self._make_fetch(["upcoming", "live", "ended"]))
        monkeypatch.setattr(dl.time, "sleep", lambda s: None)
        result = dl.wait_until_ended("url", poll_seconds=2, max_wait_seconds=1000)
        assert result["status"] == "ended"

    def test_timeout_raises(self, monkeypatch):
        monkeypatch.setattr(dl, "fetch_status", self._make_fetch(["live"] * 50))
        monkeypatch.setattr(dl.time, "sleep", lambda s: None)
        clock = {"t": 0.0}
        monkeypatch.setattr(dl.time, "monotonic", lambda: clock["t"])
        with pytest.raises(TimeoutError):
            dl.wait_until_ended("url", poll_seconds=2, max_wait_seconds=5,
                                sleep=lambda s: clock.update(t=clock["t"] + s))

    def test_progress_callback(self, monkeypatch):
        events = []
        monkeypatch.setattr(dl, "fetch_status", self._make_fetch(["live", "ended"]))
        monkeypatch.setattr(dl.time, "sleep", lambda s: None)
        dl.wait_until_ended("url", poll_seconds=1, max_wait_seconds=100,
                            progress=events.append)
        assert len(events) >= 2
        assert events[-1]["status"] == "ended"

    def test_error_backoff(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(dl, "fetch_status",
                            self._make_fetch(["unknown", "unknown", "ended"]))
        monkeypatch.setattr(dl.time, "sleep", lambda s: sleeps.append(s))
        dl.wait_until_ended("url", poll_seconds=30, max_wait_seconds=100000)
        # first error: backoff 30*2=60 ; second: 30*4=120
        assert sleeps == [60, 120]


class TestDownloadWhenLiveEnds:
    def test_downloads_after_end(self, monkeypatch):
        monkeypatch.setattr(dl, "wait_until_ended",
                            lambda *a, **k: {"status": "ended"})
        calls = {}
        monkeypatch.setattr(
            dl.download_video, "download",
            lambda *a, **k: calls.update({"called": True, "kwargs": k}) or ("out.mp4", "proj"))
        path, folder = dl.download_when_live_ends("url", base_root="X")
        assert calls["called"] is True
        assert path == "out.mp4"


class TestCLI:
    def test_check_exit_zero(self, monkeypatch):
        monkeypatch.setattr(dl, "fetch_status",
                            lambda *a, **k: {"status": "ended", "id": "x"})
        assert dl.main(["url", "--check"]) == 0

    def test_timeout_exit_one(self, monkeypatch):
        def boom(*a, **k):
            raise TimeoutError("timed out")
        monkeypatch.setattr(dl, "download_when_live_ends", boom)
        assert dl.main(["url", "--max-wait", "1"]) == 1

    def test_success_exit_zero(self, monkeypatch):
        monkeypatch.setattr(dl, "download_when_live_ends",
                            lambda *a, **k: ("out.mp4", "proj"))
        assert dl.main(["url"]) == 0


class TestPipelineWiring:
    def test_pipeline_adds_live_wait_flag(self):
        from webui.pipeline import build_command
        cmd = build_command(
            "python main_improved.py", ["--url", "https://youtube.com/live/x"],
            segments=3, live_wait_minutes=120,
        )
        assert "--live-wait" in cmd
        assert "120" in cmd

    def test_pipeline_omits_live_wait_when_zero(self):
        from webui.pipeline import build_command
        cmd = build_command(
            "python main_improved.py", ["--url", "https://youtube.com/x"],
            segments=3, live_wait_minutes=0,
        )
        assert "--live-wait" not in cmd

    def test_main_parser_has_live_wait(self):
        import os
        code = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "main_improved.py"), encoding="utf-8").read()
        assert '"--live-wait"' in code
