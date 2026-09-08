# -*- coding: utf-8 -*-
"""v7.33.1 — YouTubeUploader.attach_thumbnail (thumbnails.set)."""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.upload_gate import YouTubeUploader


def _inject_googleapiclient(monkeypatch):
    """Fake googleapiclient modules so attach_thumbnail runs offline."""
    calls = {"media_body": None, "video_id": None}

    class FakeMediaFileUpload:
        def __init__(self, path, mimetype=None):
            self.path = path

    class FakeRequest:
        def __init__(self, video_id, media_body):
            calls["video_id"] = video_id
            calls["media_body"] = media_body

        def execute(self):
            return {"kind": "youtube#thumbnailSetResponse",
                    "items": [{"videoId": calls["video_id"]}]}

    class FakeThumbnails:
        def set(self, videoId=None, media_body=None):
            return FakeRequest(videoId, media_body)

    class FakeService:
        def thumbnails(self):
            return FakeThumbnails()

    def fake_build(*args, **kwargs):
        return FakeService()

    google = types.ModuleType("googleapiclient")
    discovery = types.ModuleType("googleapiclient.discovery")
    http = types.ModuleType("googleapiclient.http")
    discovery.build = fake_build
    http.MediaFileUpload = FakeMediaFileUpload
    monkeypatch.setitem(sys.modules, "googleapiclient", google)
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", discovery)
    monkeypatch.setitem(sys.modules, "googleapiclient.http", http)
    return calls


def test_attach_thumbnail_calls_thumbnails_set(tmp_path, monkeypatch):
    calls = _inject_googleapiclient(monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    thumb = project / "thumb.png"
    thumb.write_bytes(b"png-bytes")

    uploader = YouTubeUploader(str(project))
    monkeypatch.setattr(uploader, "_load_or_create_token", lambda: object())
    result = uploader.attach_thumbnail("ABC123", str(thumb))
    assert calls["video_id"] == "ABC123"
    assert calls["media_body"].path == str(thumb)


def test_attach_thumbnail_requires_existing_file(tmp_path, monkeypatch):
    calls = _inject_googleapiclient(monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()
    uploader = YouTubeUploader(str(project))
    monkeypatch.setattr(uploader, "_load_or_create_token", lambda: object())
    import pytest
    with pytest.raises(ValueError):
        uploader.attach_thumbnail("ABC123", os.path.join(str(project), "missing.png"))
    assert calls["video_id"] is None
