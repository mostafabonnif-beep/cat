# -*- coding: utf-8 -*-
"""v7.33.1 — publish flow: best-time helper, thumbnail attach, history extras."""

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from webui import publish_history, publish_panel as pp


def _project(tmp_path):
    project = tmp_path / "proj"
    (project / "final").mkdir(parents=True)
    (project / "final" / "000_clip.mp4").write_bytes(b"x" * 64)
    (project / "viral_segments.txt").write_text(json.dumps({
        "segments": [{"title": "Title One", "caption": "cap 1", "start_time": 0,
                      "end_time": 5, "hashtags": ["viral", "story"],
                      "topic": "money", "angle": "lesson",
                      "hook_type": "question"}]}), encoding="utf-8")
    return str(project)


class TestBestTime:
    def test_next_best_publish_at_is_future_iso(self):
        slot = pp.next_best_publish_at("youtube")
        assert slot is not None
        parsed = datetime.datetime.fromisoformat(slot)
        assert parsed.tzinfo is not None
        assert parsed > datetime.datetime.now(datetime.timezone.utc)

    def test_next_best_publish_at_tiktok_and_unknown(self):
        assert pp.next_best_publish_at("tiktok") is not None
        assert pp.next_best_publish_at("reels") is not None
        assert pp.next_best_publish_at("") is not None  # defaults to youtube


class TestThumbnails:
    def test_existing_thumbnail_is_preferred(self, tmp_path):
        project = _project(tmp_path)
        thumb_dir = os.path.join(project, "thumbnails")
        os.makedirs(thumb_dir)
        existing = os.path.join(thumb_dir, "000_clip.png")
        with open(existing, "wb") as stream:
            stream.write(b"png-bytes")
        clip = os.path.join(project, "final", "000_clip.mp4")
        assert pp._thumbnail_for_clip(project, clip) == existing

    def test_index_based_thumbnail_found(self, tmp_path):
        project = _project(tmp_path)
        thumb_dir = os.path.join(project, "thumbnails")
        os.makedirs(thumb_dir)
        existing = os.path.join(thumb_dir, "000_thumbnail.jpg")
        with open(existing, "wb") as stream:
            stream.write(b"jpg")
        clip = os.path.join(project, "final", "000_clip.mp4")
        assert pp._thumbnail_for_clip(project, clip) == existing

    def test_auto_generation_when_none_exists(self, tmp_path, monkeypatch):
        project = _project(tmp_path)
        clip = os.path.join(project, "final", "000_clip.mp4")
        generated = os.path.join(project, "thumbnails", "000_clip.png")
        captured = {}

        def fake_generate(source, *, title="", out="thumbnail.png",
                          at_seconds=0.0, accent="#FFD400"):
            captured["title"] = title
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as stream:
                stream.write(b"png")
            return {"ok": True, "out": out, "size": (1280, 720)}

        import scripts.thumbnail_generator as tg
        monkeypatch.setattr(tg, "generate_thumbnail", fake_generate)
        assert pp._thumbnail_for_clip(project, clip, "Title One") == generated
        assert captured["title"] == "Title One"

    def test_generation_disabled_by_env(self, tmp_path, monkeypatch):
        project = _project(tmp_path)
        monkeypatch.setenv("VIRALCUTTER_UPLOAD_THUMBNAIL", "0")
        clip = os.path.join(project, "final", "000_clip.mp4")
        assert pp._thumbnail_for_clip(project, clip) is None
        assert not os.path.exists(os.path.join(project, "thumbnails"))

    def test_generation_failure_returns_none(self, tmp_path, monkeypatch):
        project = _project(tmp_path)
        clip = os.path.join(project, "final", "000_clip.mp4")

        def failing_generate(*a, **k):
            return {"ok": False, "error": "no frame"}

        import scripts.thumbnail_generator as tg
        monkeypatch.setattr(tg, "generate_thumbnail", failing_generate)
        assert pp._thumbnail_for_clip(project, clip) is None


class TestWorkerThumbnailAttach:
    def test_thumbnail_attached_and_recorded(self, tmp_path, monkeypatch):
        project = _project(tmp_path)
        monkeypatch.setattr(pp, "_audio_qc_upload_allowed", lambda *a: (True, ""))
        clip = os.path.join(project, "final", "000_clip.mp4")
        thumb_dir = os.path.join(project, "thumbnails")
        os.makedirs(thumb_dir)
        thumb = os.path.join(thumb_dir, "000_clip.png")
        with open(thumb, "wb") as stream:
            stream.write(b"png-bytes")
        captured = {}

        class FakeUploader:
            def __init__(self, *a, **k):
                pass

            def upload(self, video_path, title, caption, hashtags, index=None):
                return {"status": "uploaded", "platform": "youtube",
                        "video_id": "ABC123"}

            def attach_thumbnail(self, video_id, thumbnail_path):
                captured["video_id"] = video_id
                captured["thumbnail"] = thumbnail_path

        import scripts.upload_gate as ug
        monkeypatch.setattr(ug, "UPLOADERS",
                            {"youtube": lambda *a, **k: FakeUploader(*a, **k)})
        lines = list(pp.stream_upload(project, "youtube", clip, "Title One",
                                      "cap", ["#viral"], False, "warn"))
        out = "\n".join(lines)
        assert captured.get("video_id") == "ABC123"
        assert captured.get("thumbnail") == thumb
        assert "صورة مصغرة مرفوعة" in out
        # History event carries hashtags + editorial markers for the
        # performance-learning loop.
        events = publish_history.load(project)
        latest = events[-1]
        assert latest["hashtags"] == "#viral"
        assert latest["topic"] == "money"
        assert latest["angle"] == "lesson"
        assert latest["hook_type"] == "question"

    def test_thumbnail_failure_never_blocks_publish(self, tmp_path, monkeypatch):
        project = _project(tmp_path)
        monkeypatch.setattr(pp, "_audio_qc_upload_allowed", lambda *a: (True, ""))
        clip = os.path.join(project, "final", "000_clip.mp4")
        thumb_dir = os.path.join(project, "thumbnails")
        os.makedirs(thumb_dir)
        thumb = os.path.join(thumb_dir, "000_clip.png")
        with open(thumb, "wb") as stream:
            stream.write(b"png")

        class FakeUploader:
            def __init__(self, *a, **k):
                pass

            def upload(self, *a, **k):
                return {"status": "uploaded", "platform": "youtube",
                        "video_id": "XYZ"}

            def attach_thumbnail(self, *a, **k):
                raise RuntimeError("thumbnail API rejected")

        import scripts.upload_gate as ug
        monkeypatch.setattr(ug, "UPLOADERS",
                            {"youtube": lambda *a, **k: FakeUploader(*a, **k)})
        updates = list(pp.stream_upload(project, "youtube", clip, "T", "C", [],
                                        False, "warn"))
        out = "\n".join(updates)
        assert "تعذّر رفع الصورة المصغرة" in out
        assert "Upload finished." in out
        # Video still counted as uploaded.
        events = publish_history.load(project)
        assert events[-1]["status"] == "uploaded"


class TestHistoryExtras:
    def test_record_extra_fields(self, tmp_path):
        project = _project(tmp_path)
        clip = os.path.join(project, "final", "000_clip.mp4")
        publish_history.record(
            project, platform="youtube", video_path=clip, title="T",
            result={"status": "uploaded", "video_id": "V1"},
            extra={"hashtags": "a,b", "topic": "money", "angle": "lesson",
                   "hook_type": "question", "variant_of": "000_clip.mp4"})
        events = publish_history.load(project)
        latest = events[-1]
        assert latest["hashtags"] == "a,b"
        assert latest["topic"] == "money"
        assert latest["angle"] == "lesson"
        assert latest["hook_type"] == "question"
        assert latest["variant_of"] == "000_clip.mp4"

    def test_record_without_extra_stays_clean(self, tmp_path):
        project = _project(tmp_path)
        clip = os.path.join(project, "final", "000_clip.mp4")
        publish_history.record(project, platform="tiktok", video_path=clip,
                               title="T", result={"status": "uploaded"})
        events = publish_history.load(project)
        assert "hashtags" not in events[-1]
