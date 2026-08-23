# -*- coding: utf-8 -*-
"""Tests for the upload gate (forced refusal before publishing)."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import upload_gate as ug


def _write(project, name, data):
    with open(os.path.join(project, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class TestBlocklist:
    def test_clean_project_allows(self, tmp_path):
        verdict = ug.check_clip(str(tmp_path), 0, "Nice title", "Nice caption", ["shorts"])
        assert verdict["allowed"] is True
        assert verdict["reasons"] == []

    def test_blocked_clip_refused(self, tmp_path):
        _write(tmp_path, ug.PUBLISH_BLOCKLIST, {
            "blocked": [{"index": 0, "title": "Bad", "axes": {"reuse": {"score": 80}}}]})
        verdict = ug.check_clip(str(tmp_path), 0, "Title", "Caption", [])
        assert verdict["allowed"] is False
        assert any(r["source"] == "publish_blocklist" for r in verdict["reasons"])

    def test_other_index_not_blocked(self, tmp_path):
        _write(tmp_path, ug.PUBLISH_BLOCKLIST, {
            "blocked": [{"index": 1, "title": "Bad", "axes": {"reuse": {"score": 80}}}]})
        verdict = ug.check_clip(str(tmp_path), 0, "Title", "Caption", [])
        assert verdict["allowed"] is True

    def test_gate_upload_raises(self, tmp_path):
        _write(tmp_path, ug.PUBLISH_BLOCKLIST, {
            "blocked": [{"index": 0, "title": "Bad", "axes": {"reuse": {"score": 90}}}]})
        with pytest.raises(ug.UploadGateError) as ei:
            ug.gate_upload(str(tmp_path), 0, "Title", "Caption", [])
        assert any(r["severity"] == "high" for r in ei.value.reasons)


class TestSafetyReport:
    def test_safety_blocked_refused(self, tmp_path):
        _write(tmp_path, ug.SAFETY_REPORT, {
            "blocked": [{"index": 2, "reason": "hate speech (high)"}]})
        verdict = ug.check_clip(str(tmp_path), 2, "Title", "Caption", [])
        assert verdict["allowed"] is False
        assert any(r["source"] == "safety_report" for r in verdict["reasons"])


class TestMetadataGate:
    def test_medical_claim_blocks(self, tmp_path):
        verdict = ug.check_clip(str(tmp_path), 0, "This cures cancer", "", [])
        assert verdict["allowed"] is False
        assert any(r["source"] == "metadata_compliance" for r in verdict["reasons"])

    def test_clean_metadata_allows(self, tmp_path):
        verdict = ug.check_clip(str(tmp_path), 0, "Top 5 Tips", "Full video", ["shorts"])
        assert verdict["allowed"] is True


class TestUploaders:
    def test_uploader_blocks_before_any_sdk_call(self, tmp_path, monkeypatch):
        _write(tmp_path, ug.PUBLISH_BLOCKLIST, {
            "blocked": [{"index": 0, "title": "Bad", "axes": {"reuse": {"score": 85}}}]})
        uploader = ug.YouTubeUploader(str(tmp_path), dry_run=True)
        with pytest.raises(ug.UploadGateError):
            uploader.upload("clip.mp4", "Title", "Caption", [], index=0)

    def test_uploader_dry_run_allows_clean(self, tmp_path, capsys):
        uploader = ug.YouTubeUploader(str(tmp_path), dry_run=True)
        result = uploader.upload("clip.mp4", "Title", "Caption", ["shorts"], index=0)
        assert result["status"] == "dry-run"
        out = capsys.readouterr().out
        assert "DRY-RUN" in out

    def test_uploader_without_credentials_fails_loudly(self, tmp_path):
        uploader = ug.TikTokUploader(str(tmp_path), dry_run=False)
        with pytest.raises(RuntimeError, match="OAuth credentials"):
            uploader.upload("clip.mp4", "Title", "Caption", [], index=0)


class TestAudit:
    def test_audit_project(self, tmp_path):
        _write(tmp_path, ug.SCORECARD, {
            "segments": [
                {"index": 0, "title": "Clean"},
                {"index": 1, "title": "Dangerous cure"},
            ]})
        _write(tmp_path, ug.PUBLISH_BLOCKLIST, {
            "blocked": [{"index": 1, "title": "Dangerous cure", "axes": {"reuse": {"score": 75}}}]})
        allowed, blocked = ug.audit_project(str(tmp_path))
        assert allowed == [0]
        assert len(blocked) == 1
        assert blocked[0]["index"] == 1


class TestYouTubeUploaderReal:
    """YouTube OAuth uploader (Roadmap 2.2) — mocked API, real gate logic."""

    def test_missing_video_raises(self, tmp_path, monkeypatch):
        from scripts import upload_gate as ug
        monkeypatch.setenv("YT_CLIENT_SECRETS_FILE", str(tmp_path / "cs.json"))
        uploader = ug.YouTubeUploader(str(tmp_path), dry_run=False)
        with pytest.raises(FileNotFoundError):
            uploader.upload(str(tmp_path / "nope.mp4"), "T", "C", [], index=0)

    def test_missing_credentials_clear_error(self, tmp_path, monkeypatch):
        from scripts import upload_gate as ug
        monkeypatch.delenv("YT_CLIENT_SECRETS_FILE", raising=False)
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        uploader = ug.YouTubeUploader(str(tmp_path), dry_run=False)
        with pytest.raises(RuntimeError, match="OAuth credentials"):
            uploader.upload(str(video), "T", "C", ["shorts"], index=0)

    def test_upload_builds_request_and_returns_id(self, tmp_path, monkeypatch):
        import sys as _sys

        from scripts import upload_gate as ug

        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake video")

        captured = {}

        class FakeCreds:
            valid = True

        class FakeMedia:
            def __init__(self, path, chunksize, resumable):
                captured["media_path"] = path
                captured["chunksize"] = chunksize

        class FakeRequest:
            def __init__(self, body, media_body):
                captured["body"] = body
                captured["media"] = media_body

            def next_chunk(self):
                captured["called"] = True
                return None, {"id": "VID123", "status": "uploaded"}

        class FakeVideos:
            def insert(self, part, body, media_body):
                captured["part"] = part
                return FakeRequest(body, media_body)

        class FakeService:
            def __init__(self, *_a, **_k):
                pass

            def videos(self):
                return FakeVideos()

        # fake the google libs that _do_upload imports lazily
        fake_discovery = type(_sys)("googleapiclient.discovery")
        fake_discovery.build = lambda *a, **k: FakeService(*a, **k)
        fake_http = type(_sys)("googleapiclient.http")
        fake_http.MediaFileUpload = FakeMedia
        _sys.modules["googleapiclient.discovery"] = fake_discovery
        _sys.modules["googleapiclient.http"] = fake_http

        monkeypatch.setenv("YT_PRIVACY", "unlisted")
        uploader = ug.YouTubeUploader(str(tmp_path), dry_run=False)
        monkeypatch.setattr(uploader, "_load_or_create_token", lambda: FakeCreds())
        result = uploader.upload(str(video), "My Title", "My caption",
                                 ["#shorts", "funny"], index=0)
        assert result["video_id"] == "VID123"
        assert result["status"] == "uploaded"
        assert captured["body"]["snippet"]["title"] == "My Title"
        assert captured["body"]["snippet"]["tags"] == ["shorts", "funny"]
        assert "funny" in captured["body"]["snippet"]["description"]
        assert captured["body"]["status"]["privacyStatus"] == "unlisted"
        assert captured["called"] is True

    def test_scheduled_upload_requires_private_and_sets_publish_at(self, tmp_path, monkeypatch):
        import sys as _sys
        from datetime import datetime, timedelta, timezone

        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake video")
        captured = {}

        class FakeCreds:
            valid = True

        class FakeMedia:
            def __init__(self, path, chunksize, resumable):
                captured["media_path"] = path

        class FakeRequest:
            def next_chunk(self):
                return None, {"id": "SCHEDULED123"}

        class FakeVideos:
            def insert(self, part, body, media_body):
                captured["body"] = body
                return FakeRequest()

        class FakeService:
            def videos(self):
                return FakeVideos()

        fake_discovery = type(_sys)("googleapiclient.discovery")
        fake_discovery.build = lambda *a, **k: FakeService()
        fake_http = type(_sys)("googleapiclient.http")
        fake_http.MediaFileUpload = FakeMedia
        monkeypatch.setitem(_sys.modules, "googleapiclient.discovery", fake_discovery)
        monkeypatch.setitem(_sys.modules, "googleapiclient.http", fake_http)

        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        uploader = ug.YouTubeUploader(str(tmp_path), dry_run=False)
        monkeypatch.setattr(uploader, "_load_or_create_token", lambda: FakeCreds())
        result = uploader.upload(str(video), "Scheduled", "Caption", ["#shorts"],
                                 index=0, privacy_status="private", publish_at=future)
        assert result["status"] == "scheduled"
        assert result["video_id"] == "SCHEDULED123"
        assert captured["body"]["status"]["privacyStatus"] == "private"
        assert captured["body"]["status"]["publishAt"].endswith("Z")

        with pytest.raises(ValueError, match="private"):
            uploader.upload(str(video), "Scheduled", "Caption", [], index=0,
                            privacy_status="public", publish_at=future)
