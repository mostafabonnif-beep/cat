# -*- coding: utf-8 -*-
"""Tests for v7.25: pipeline runner + Telegram process/upload commands."""

import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import pipeline_runner as pr


class TestURLValidation:
    def test_valid_youtube(self):
        assert pr.is_youtube_url("https://youtube.com/watch?v=abc123")
        assert pr.is_youtube_url("https://www.youtube.com/watch?v=abc123")
        assert pr.is_youtube_url("https://youtu.be/abc123")
        assert pr.is_youtube_url("https://youtube.com/live/gowpuk4jk5U")
        assert pr.is_youtube_url("https://youtube.com/shorts/abc123")

    def test_invalid(self):
        assert not pr.is_youtube_url("https://example.com/watch?v=x")
        assert not pr.is_youtube_url("not a url")
        assert not pr.is_youtube_url("https://youtube.com/")
        assert not pr.is_youtube_url("")

    def test_http_also_accepted(self):
        assert pr.is_youtube_url("http://youtube.com/watch?v=abc")


class TestLock:
    def test_lock_acquire_release(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pr, "LOCK_PATH", str(tmp_path / "lock"))
        assert pr._acquire_lock() is True
        assert pr._acquire_lock(timeout=0.2) is False  # already held
        pr._release_lock()
        assert pr._acquire_lock() is True
        pr._release_lock()

    def test_stale_lock_reclaimed(self, tmp_path, monkeypatch):
        lock = tmp_path / "lock"
        lock.write_text("123")
        import time as _t
        old = _t.time() - 7 * 3600
        os.utime(lock, (old, old))
        monkeypatch.setattr(pr, "LOCK_PATH", str(lock))
        assert pr._acquire_lock() is True
        pr._release_lock()


class TestRunPipeline:
    def test_invalid_url(self):
        result = pr.run_pipeline("https://example.com/x")
        assert result.ok is False
        assert "يوتيوب" in result.message

    def test_pipeline_success(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pr, "_acquire_lock", lambda *a, **k: True)
        monkeypatch.setattr(pr, "_release_lock", lambda: None)
        project = tmp_path / "proj"
        (project / "final").mkdir(parents=True)
        (project / "final" / "001_clip.mp4").write_bytes(b"x")

        class FakeProc:
            returncode = 0
            stdout = "project_folder: {}".format(project)
            stderr = ""

        monkeypatch.setattr(pr.subprocess, "run", lambda *a, **k: FakeProc())
        result = pr.run_pipeline("https://youtube.com/watch?v=abc")
        assert result.ok is True
        assert result.project_folder == str(project)
        assert len(result.clips) == 1

    def test_pipeline_failure(self, monkeypatch):
        monkeypatch.setattr(pr, "_acquire_lock", lambda *a, **k: True)
        monkeypatch.setattr(pr, "_release_lock", lambda: None)

        class FakeProc:
            returncode = 1
            stdout = "boom"
            stderr = "error detail"

        monkeypatch.setattr(pr.subprocess, "run", lambda *a, **k: FakeProc())
        result = pr.run_pipeline("https://youtube.com/watch?v=abc")
        assert result.ok is False


class TestFindHelpers:
    def test_find_project_folder_from_logs(self, tmp_path):
        proj = tmp_path / "myproj"
        proj.mkdir()
        found = pr._find_project_folder("x", "project folder: {}".format(proj))
        assert found == str(proj)

    def test_find_project_newest_virals(self, tmp_path, monkeypatch):
        older = tmp_path / "older"
        newer = tmp_path / "newer"
        older.mkdir()
        newer.mkdir()
        import time as _t
        os.utime(older, (_t.time() - 100, _t.time() - 100))
        monkeypatch.setenv("VIRALCUTTER_VIRALS_DIR", str(tmp_path))
        found = pr._find_project_folder("no-logs", "")
        assert found == str(newer)

    def test_find_clips_order(self, tmp_path):
        (tmp_path / "final").mkdir()
        (tmp_path / "cuts").mkdir()
        (tmp_path / "final" / "b.mp4").write_bytes(b"x")
        (tmp_path / "cuts" / "a.mp4").write_bytes(b"x")
        (tmp_path / "cuts" / "note.txt").write_text("x")
        clips = pr._find_clips(str(tmp_path))
        assert len(clips) == 2
        assert all(c.endswith(".mp4") for c in clips)


class TestUploadProject:
    def test_missing_project(self, tmp_path):
        result = pr.upload_project(str(tmp_path / "nope"), dry_run=True)
        assert result.ok is False

    def test_no_clips(self, tmp_path):
        proj = tmp_path / "p"
        proj.mkdir()
        result = pr.upload_project(str(proj), dry_run=True)
        assert result.ok is False

    def test_dry_run_lists_clips(self, tmp_path):
        proj = tmp_path / "p"
        (proj / "final").mkdir(parents=True)
        (proj / "final" / "clip.mp4").write_bytes(b"x")
        result = pr.upload_project(str(proj), dry_run=True)
        assert result.ok is True
        assert "clip.mp4" in result.message
        assert "confirm_upload" in result.message

    def test_real_upload_calls_batch(self, tmp_path, monkeypatch):
        proj = tmp_path / "p"
        (proj / "final").mkdir(parents=True)
        (proj / "final" / "clip.mp4").write_bytes(b"x")
        monkeypatch.setattr(pr, "_acquire_lock", lambda *a, **k: True)
        monkeypatch.setattr(pr, "_release_lock", lambda: None)

        calls = {}
        fake_panel = types.SimpleNamespace(
            stream_upload_batch=lambda *a, **k: calls.update(kwargs=k) or ["uploaded: ok", "✅"])
        monkeypatch.setitem(sys.modules, "webui", types.ModuleType("webui"))
        monkeypatch.setitem(sys.modules, "webui.publish_panel", fake_panel)
        result = pr.upload_project(str(proj), dry_run=False, privacy="private")
        assert result.ok is True
        assert calls["kwargs"]["privacy_status"] == "private"
        assert calls["kwargs"]["require_existing_auth"] is True


class TestTelegramHandlers:
    def _handlers(self):
        from webui import telegram_control as tc
        queue = types.SimpleNamespace(
            paused=False, state_warning="",
            active=lambda: [], snapshot=lambda *a, **k: {},
            pause_all=lambda: None, resume_all=lambda: None,
            retry_failed=lambda: [], get=lambda jid: None,
            cancel=lambda jid: "cancelled",
            TERMINAL_STATES={"succeeded", "failed", "cancelled"})
        return tc.build_queue_handlers(queue, project_root=None)

    def test_help_lists_new_commands(self):
        from webui import telegram_control as tc
        router = tc.TelegramCommandRouter(
            tc.TelegramAPI("123456:ABCdefGHIjklMNOpqrsTUVwxyz"),
            ["111"], self._handlers())
        help_text = router._help()
        assert "/process" in help_text
        assert "/confirm_upload" in help_text

    def test_process_rejects_non_youtube(self):
        handlers = self._handlers()
        from webui import telegram_control as tc
        ctx = tc.CommandContext(chat_id="111", username="u", message_id="1")
        reply = handlers["process"](ctx, "https://example.com/x")
        assert "يوتيوب" in reply

    def test_process_starts_thread(self):
        handlers = self._handlers()
        from webui import telegram_control as tc
        ctx = tc.CommandContext(chat_id="111", username="u", message_id="1")
        reply = handlers["process"](ctx, "https://youtube.com/watch?v=abc")
        assert "بدأت" in reply

    def test_confirm_upload_without_upload_first(self):
        handlers = self._handlers()
        from webui import telegram_control as tc
        ctx = tc.CommandContext(chat_id="111", username="u", message_id="1")
        reply = handlers["confirm_upload"](ctx, "someproj")
        assert "أولاً" in reply or "غير موجود" in reply


class TestFullFlow:
    def test_process_handler_replies_and_sends(self, monkeypatch):
        from webui import telegram_control as tc

        sent = []

        class FakeAPI:
            def __init__(self):
                pass

            def send_message(self, chat_id, text):
                sent.append((chat_id, text))
                return {"ok": True}

        fake_api = FakeAPI()
        handlers = self._handlers()

        # Patch pipeline_runner.run_pipeline inside the handler closure
        import scripts.pipeline_runner as pr
        monkeypatch.setattr(pr, "run_pipeline",
                            lambda *a, **k: pr.RunResult(ok=True, message="✅ تم", clips=[]))
        # The handler imports run_pipeline lazily; patch the module attribute
        monkeypatch.setattr(
            "scripts.pipeline_runner.run_pipeline",
            lambda *a, **k: pr.RunResult(ok=True, message="✅ تم", clips=[]))

        # Rebuild handlers with our fake API: easiest is to exercise via a router
        router = tc.TelegramCommandRouter(fake_api, ["111"], handlers)
        # simulate a /process message from chat 111
        update = {
            "message": {
                "chat": {"id": 111},
                "from": {"username": "u"},
                "message_id": "42",
                "text": "/process https://youtube.com/watch?v=abc",
            }
        }
        import threading
        monkeypatch.setattr(threading, "Thread",
                            lambda target=None, daemon=None, **k: (
                                target() if target else None) or type("T", (), {
                                    "start": lambda self: None})())
        # Threads are daemon & run immediately in test via fake above
        handled = router.handle_update(update)
        assert handled is True

    def _handlers(self):
        from webui import telegram_control as tc
        queue = types.SimpleNamespace(
            paused=False, state_warning="",
            active=lambda: [], snapshot=lambda *a, **k: {},
            pause_all=lambda: None, resume_all=lambda: None,
            retry_failed=lambda: [], get=lambda jid: None,
            cancel=lambda jid: "cancelled",
            TERMINAL_STATES={"succeeded", "failed", "cancelled"})
        return tc.build_queue_handlers(queue, project_root=None)
