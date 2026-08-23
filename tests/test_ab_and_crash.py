# -*- coding: utf-8 -*-
"""Tests for A/B titles (Roadmap 5.3) and the crash reporter (4.5)."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# NOTE: create_viral_segments replaces sys.stdout at import time (Windows
# encoding guard), which breaks pytest's capture when imported at module
# top-level → import it lazily inside the tests.
from scripts import crash_report


class TestABTitles:
    def _cvs(self):
        from scripts import create_viral_segments as _cvs
        return _cvs

    def test_segment_titles_dedup(self):
        cvs = self._cvs()
        titles = cvs.segment_titles({"title": "Main", "alt_titles": ["A", "B", "A"]})
        assert titles == ["A", "B", "Main"]

    def test_segment_titles_fallback(self):
        cvs = self._cvs()
        assert cvs.segment_titles({"title": "Only"}) == ["Only"]
        assert cvs.segment_titles({}) == ["Viral Segment"]

    def test_segment_captions(self):
        cvs = self._cvs()
        caps = cvs.segment_captions({"caption": "C1", "alt_captions": ["X"]})
        assert caps == ["X", "C1"]

    def test_prompt_template_requests_alt_titles(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts", "create_viral_segments.py"),
                   encoding="utf-8").read()
        assert "alt_titles" in src
        assert "alt_captions" in src

    def test_prompt_file_mentions_ab(self):
        prompt = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "prompt.txt"), encoding="utf-8").read()
        assert "A/B" in prompt
        assert "alt_titles" in prompt


class TestCrashReport:
    def test_log_crash_sanitizes_paths(self, tmp_path):
        log = str(tmp_path / "crash.log")
        exc = RuntimeError("failed at /home/user/secret/project/input.mp4")
        entry = crash_report.log_crash("cut", exc, log_path=log)
        assert os.path.exists(log)
        assert "/home/user/secret/project/input.mp4" not in entry["error"]
        assert "<PATH>" in entry["error"] or "<HOME>" in entry["error"]
        assert entry["stage"] == "cut"
        assert entry["error_type"] == "RuntimeError"

    def test_no_send_without_optin(self, tmp_path, monkeypatch):
        monkeypatch.delenv("VIRALCUTTER_CRASH_REPORT", raising=False)
        monkeypatch.delenv("VIRALCUTTER_CRASH_ENDPOINT", raising=False)
        entry = crash_report.report("x", ValueError("boom"),
                                    log_path=str(tmp_path / "c.log"))
        assert entry["error_type"] == "ValueError"

    def test_send_happy_path(self, tmp_path, monkeypatch):
        sent = {}

        class FakeResp:
            status = 200

        def fake_urlopen(req, timeout=5):
            sent["data"] = json.loads(req.data)
            return FakeResp()

        monkeypatch.setenv("VIRALCUTTER_CRASH_REPORT", "1")
        monkeypatch.setenv("VIRALCUTTER_CRASH_ENDPOINT", "https://collector.invalid/x")
        monkeypatch.setattr(crash_report.urllib.request, "urlopen", fake_urlopen)
        crash_report.report("transcribe", RuntimeError("boom"),
                            log_path=str(tmp_path / "c.log"))
        assert sent["data"]["stage"] == "transcribe"

    def test_send_failure_is_silent(self, tmp_path, monkeypatch):
        def boom(*a, **k):
            raise OSError("network")

        monkeypatch.setenv("VIRALCUTTER_CRASH_REPORT", "1")
        monkeypatch.setenv("VIRALCUTTER_CRASH_ENDPOINT", "https://x.invalid/y")
        monkeypatch.setattr(crash_report.urllib.request, "urlopen", boom)
        # must not raise
        entry = crash_report.report("x", ValueError("z"),
                                    log_path=str(tmp_path / "c.log"))
        assert entry["error_type"] == "ValueError"


class TestTitleLanguage:
    """Arabic titles feature (v6.6) — language_instruction helper."""

    def test_ar_instruction(self):
        from scripts.create_viral_segments import language_instruction
        ins = language_instruction("ar")
        assert "LANGUAGE RULE" in ins and "Arabic" in ins

    def test_auto_no_instruction(self):
        from scripts.create_viral_segments import language_instruction
        assert language_instruction("auto") == ""
        assert language_instruction(None) == ""
        assert language_instruction("") == ""

    def test_unknown_falls_back_auto(self):
        from scripts.create_viral_segments import language_instruction
        assert language_instruction("xx") == ""

    def test_webui_command_passes_flag(self):
        from webui.pipeline import build_command
        cmd = build_command("main.py", ["--url", "x"], segments=3, title_language="ar")
        assert "--title-language" in cmd
        assert cmd[cmd.index("--title-language") + 1] == "ar"

    def test_webui_auto_omits_flag(self):
        from webui.pipeline import build_command
        cmd = build_command("main.py", ["--url", "x"], segments=3, title_language="auto")
        assert "--title-language" not in cmd


class TestAVSyncFix:
    """edit_video mux must keep audio in sync (v6.6)."""

    def test_finalize_uses_shortest_and_aresample(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts", "edit_video.py"), encoding="utf-8").read()
        assert "-shortest" in src
        assert "aresample=async=1" in src
