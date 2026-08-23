# -*- coding: utf-8 -*-
"""Real TikTok / Instagram uploaders (Roadmap 2.2) — mocked HTTP, real logic.

Network is never touched: the urllib transport is monkeypatched so the
init → upload → status (TikTok) and media → publish (Instagram) flows are
exercised end to end, including the safety gate in front of them.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import upload_gate as ug  # noqa: E402


def _write(project, name, data):
    with open(os.path.join(project, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode("utf-8")


class FakeHTTPError(Exception):
    pass


def _install_fake_urlopen(monkeypatch, queue):
    """route urlopen calls through a list of responses (dicts).

    Each response dict: {"payload": <json>, "captured": <filled in>}.
    Returns the list of captured call records.
    """
    import urllib.request
    captures = []

    def fake_urlopen(req_or_url, *a, **k):
        assert queue, "unexpected urlopen call: {}".format(req_or_url)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        captured = {"url": req_or_url.full_url if hasattr(req_or_url, "full_url") else str(req_or_url),
                    "method": getattr(req_or_url, "get_method", lambda: "GET")()}
        if hasattr(req_or_url, "data") and req_or_url.data:
            captured["body"] = req_or_url.data
        if hasattr(req_or_url, "headers"):
            captured["headers"] = dict(req_or_url.headers)
        item["captured"] = captured
        captures.append(captured)
        return FakeResponse(item["payload"])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captures


class TestTikTokUploader:
    def _uploader(self, tmp_path, monkeypatch, token=None):
        monkeypatch.setenv("TIKTOK_CLIENT_KEY", "ck")
        monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "cs")
        if token is None:
            token = {"access_token": "TOK", "refresh_token": "RT",
                     "expires_at": 10 ** 15}
        token_path = str(tmp_path / "tt_token.json")
        with open(token_path, "w", encoding="utf-8") as f:
            json.dump(token, f)
        monkeypatch.setenv("TIKTOK_TOKEN_FILE", token_path)
        return ug.TikTokUploader(str(tmp_path), dry_run=False)

    def test_no_credentials_fails_loudly_before_any_call(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TIKTOK_CLIENT_KEY", raising=False)
        monkeypatch.delenv("TIKTOK_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("TIKTOK_TOKEN_FILE", raising=False)
        uploader = ug.TikTokUploader(str(tmp_path), dry_run=False)
        with pytest.raises(RuntimeError, match="OAuth credentials"):
            uploader.upload(str(tmp_path / "clip.mp4"), "T", "C", [], index=0)

    def test_full_upload_flow(self, tmp_path, monkeypatch):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake-video-bytes")
        uploader = self._uploader(tmp_path, monkeypatch)

        queue = [
            {"payload": {"data": {"publish_id": "PUB1"}}},       # init
            {"payload": {}},                                      # PUT upload
            {"payload": {"data": {"status": "PROCESSING_UPLOAD"}}},
            {"payload": {"data": {"status": "PUBLISH_COMPLETE"}}},
        ]
        captures = _install_fake_urlopen(monkeypatch, queue)

        result = uploader.upload(str(video), "My Title", "caption", ["#shorts"], index=0)
        assert result["status"] == "uploaded"
        assert result["publish_id"] == "PUB1"
        # 4 HTTP calls: init, PUT upload, status poll, status poll
        assert len(captures) == 4
        assert "video/init" in captures[0]["url"]
        assert "video/upload/PUB1" in captures[1]["url"]
        assert captures[1]["method"] == "PUT"
        # The real bug this guards: the video BYTES must be the PUT body
        # (previously blob was read into memory and never sent).
        assert captures[1]["body"] == b"fake-video-bytes"
        hdr = {k.lower(): v for k, v in captures[1]["headers"].items()}
        assert hdr["content-type"] == "video/mp4"

    def test_init_payload_and_status_polling(self, tmp_path, monkeypatch):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x" * 10)
        uploader = self._uploader(tmp_path, monkeypatch)
        queue = [
            {"payload": {"data": {"publish_id": "P2"}}},
            {"payload": {}},
            {"payload": {"data": {"status": "PUBLISH_COMPLETE"}}},
        ]
        captures = _install_fake_urlopen(monkeypatch, queue)

        result = uploader.upload(str(video), "T", "C", [], index=0)
        assert result["status"] == "uploaded"

        init_call = captures[0]
        init_payload = json.loads(init_call["body"].decode())
        assert init_payload["source_info"]["source"] == "FILE_UPLOAD"
        assert init_payload["source_info"]["video_size"] == 10
        assert init_payload["post_info"]["title"] == "T"
        assert "Bearer TOK" in init_call["headers"]["Authorization"]

    def test_failed_publish_raises(self, tmp_path, monkeypatch):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x" * 10)
        uploader = self._uploader(tmp_path, monkeypatch)
        queue = [
            {"payload": {"data": {"publish_id": "P3"}}},
            {"payload": {}},
            {"payload": {"data": {"status": "FAILED", "fail_reason": "copyright"}}},
        ]
        _install_fake_urlopen(monkeypatch, queue)
        with pytest.raises(RuntimeError, match="copyright"):
            uploader.upload(str(video), "T", "C", [], index=0)

    def test_missing_video_raises_after_credentials(self, tmp_path, monkeypatch):
        uploader = self._uploader(tmp_path, monkeypatch)
        with pytest.raises(FileNotFoundError):
            uploader.upload(str(tmp_path / "nope.mp4"), "T", "C", [], index=0)

    def test_gate_still_blocks_before_sdk(self, tmp_path, monkeypatch):
        _write(tmp_path, ug.PUBLISH_BLOCKLIST, {
            "blocked": [{"index": 0, "title": "Bad", "axes": {"reuse": {"score": 80}}}]})
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x" * 10)
        uploader = self._uploader(tmp_path, monkeypatch)
        with pytest.raises(ug.UploadGateError):
            uploader.upload(str(video), "T", "C", [], index=0)


class TestTikTokOAuth:
    def test_auth_url_building(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TIKTOK_CLIENT_KEY", "CK123")
        monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "SEC")
        uploader = ug.TikTokUploader(str(tmp_path), dry_run=True)
        params = {
            "client_key": "CK123",
            "scope": uploader.SCOPES,
            "response_type": "code",
            "redirect_uri": uploader._redirect_uri(),
            "state": "abc",
        }
        url = "{}?{}".format(uploader.AUTH_URL, __import__("urllib.parse").parse.urlencode(params))
        assert "client_key=CK123" in url
        assert "video.publish" in url
        assert "response_type=code" in url

    def test_exchange_code_saves_token(self, tmp_path, monkeypatch):
        import urllib.request
        monkeypatch.setenv("TIKTOK_CLIENT_KEY", "CK123")
        monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "SEC")
        token_path = str(tmp_path / "tok.json")
        monkeypatch.setenv("TIKTOK_TOKEN_FILE", token_path)

        def fake_urlopen(req, *a, **k):
            body = req.data.decode()
            assert "grant_type=authorization_code" in body
            assert "client_secret=SEC" in body
            return FakeResponse({"access_token": "AT", "expires_in": 86400,
                                 "refresh_token": "RT", "open_id": "O1"})

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        uploader = ug.TikTokUploader(str(tmp_path), dry_run=True)
        token = uploader._exchange_code("CODE", "http://localhost:8431/", "CK123", "SEC")
        assert token["access_token"] == "AT"
        assert token["expires_at"] > 0

    def test_refresh_token(self, tmp_path, monkeypatch):
        import urllib.request
        monkeypatch.setenv("TIKTOK_CLIENT_KEY", "CK123")
        monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "SEC")
        token_path = str(tmp_path / "tok.json")
        with open(token_path, "w", encoding="utf-8") as f:
            json.dump({"access_token": "OLD", "refresh_token": "RT",
                       "expires_at": time_now_minus_1h()}, f)
        monkeypatch.setenv("TIKTOK_TOKEN_FILE", token_path)

        def fake_urlopen(req, *a, **k):
            body = req.data.decode()
            assert "grant_type=refresh_token" in body
            assert "refresh_token=RT" in body
            return FakeResponse({"access_token": "NEW", "expires_in": 86400})

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        uploader = ug.TikTokUploader(str(tmp_path), dry_run=True)
        new_token = uploader._refresh_token({"access_token": "OLD",
                                             "refresh_token": "RT",
                                             "expires_at": 1})
        assert new_token["access_token"] == "NEW"
        with open(token_path, encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["access_token"] == "NEW"


class TestSetupCheck:
    def test_tiktok_missing_keys(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TIKTOK_CLIENT_KEY", raising=False)
        monkeypatch.delenv("TIKTOK_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("TIKTOK_TOKEN_FILE", raising=False)
        checks = ug.check_platform_setup("tiktok")
        by_item = {c["item"]: c for c in checks}
        assert by_item["TikTok app credentials (TIKTOK_CLIENT_KEY/SECRET)"]["ok"] is False
        # approval cannot be verified locally — surfaced honestly
        assert by_item["Content Posting API approval"]["ok"] is None
        assert "days/weeks" in by_item["Content Posting API approval"]["detail"]

    def test_instagram_reports_auto_host_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("IG_ACCESS_TOKEN", "T")
        monkeypatch.setenv("IG_USER_ID", "7")
        monkeypatch.delenv("IG_VIDEO_URL", raising=False)
        checks = ug.check_platform_setup("instagram")
        by_item = {c["item"]: c for c in checks}
        assert by_item["Instagram access token"]["ok"] is True
        assert "AUTO-HOST" in by_item["Video URL source"]["detail"]

    def test_unknown_platform(self):
        checks = ug.check_platform_setup("nope")
        assert checks[0]["ok"] is False


class TestTikTokHint:
    def test_permission_error_gets_approval_hint(self):
        msg = ug._with_tiktok_hint("API error 43201: no permission")
        assert ug.TIKTOK_APPROVAL_HINT in msg

    def test_clean_error_unchanged(self):
        msg = ug._with_tiktok_hint("API error 400: bad request")
        assert ug.TIKTOK_APPROVAL_HINT not in msg


class TestHostMediaFile:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ug.host_media_file(str(tmp_path / "nope.mp4"))

    def test_empty_file_raises(self, tmp_path):
        video = tmp_path / "empty.mp4"
        video.write_bytes(b"")
        with pytest.raises(ValueError):
            ug.host_media_file(str(video))

    def test_too_large_raises(self, tmp_path, monkeypatch):
        video = tmp_path / "big.mp4"
        video.write_bytes(b"x" * (200 * 1024 * 1024 + 1))
        with pytest.raises(RuntimeError, match="200 MB"):
            ug.host_media_file(str(video))

    def test_catbox_then_0x0_fallback(self, tmp_path, monkeypatch):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"1234")
        import urllib.request as urlreq
        calls = []

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"<html>error page</html>"  # catbox replies garbage

        def fake_urlopen(req, *a, **k):
            calls.append(req.full_url)
            if len(calls) == 1:
                return Resp()          # catbox: bad body → not an https URL
            raise OSError("0x0.st refused")  # 0x0.st: network error

        monkeypatch.setattr(urlreq, "urlopen", fake_urlopen)
        with pytest.raises(RuntimeError, match="auto-hosting failed"):
            ug.host_media_file(str(video))
        # Tried both providers before giving up.
        assert len(calls) == 2
        assert calls[0] == "https://catbox.moe/user/api.php"
        assert calls[1] == "https://0x0.st"

    def test_success_returns_https_url(self, tmp_path, monkeypatch):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"1234")
        import urllib.request as urlreq

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"https://catbox.moe/shorts/xyz.mp4"

        monkeypatch.setattr(urlreq, "urlopen", lambda req, *a, **k: Resp())
        url = ug.host_media_file(str(video))
        assert url == "https://catbox.moe/shorts/xyz.mp4"


def time_now_minus_1h():
    import time
    return time.time() - 3600


class TestInstagramUploader:
    def _uploader(self, tmp_path, monkeypatch):
        monkeypatch.setenv("IG_ACCESS_TOKEN", "IGTOK")
        monkeypatch.setenv("IG_USER_ID", "12345")
        monkeypatch.setenv("IG_VIDEO_URL", "https://cdn.example.com/hosted.mp4")
        return ug.InstagramUploader(str(tmp_path), dry_run=False)

    def test_no_credentials_fails_loudly(self, tmp_path, monkeypatch):
        monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("IG_USER_ID", raising=False)
        uploader = ug.InstagramUploader(str(tmp_path), dry_run=False)
        with pytest.raises(RuntimeError, match="OAuth credentials"):
            uploader.upload(str(tmp_path / "clip.mp4"), "T", "C", [], index=0)

    def test_auto_host_closes_public_url_gap(self, tmp_path, monkeypatch):
        """No IG_VIDEO_URL → the local clip is auto-hosted, then published."""
        monkeypatch.setenv("IG_ACCESS_TOKEN", "IGTOK")
        monkeypatch.setenv("IG_USER_ID", "12345")
        monkeypatch.delenv("IG_VIDEO_URL", raising=False)
        monkeypatch.setattr(ug, "host_media_file",
                            lambda p, timeout=300: "https://catbox.moe/auto1.mp4")
        uploader = ug.InstagramUploader(str(tmp_path), dry_run=False)
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        queue = [
            {"payload": {"id": "CREATION1"}},
            {"payload": {"id": "MEDIA9"}},
        ]
        captures = _install_fake_urlopen(monkeypatch, queue)
        result = uploader.upload(str(video), "T", "C", [], index=0)
        assert result["status"] == "uploaded"
        import urllib.parse as up
        body = up.parse_qs(captures[0]["body"].decode())
        assert body["media_type"] == ["REELS"]
        assert body["video_url"] == ["https://catbox.moe/auto1.mp4"]

    def test_auto_host_can_be_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("IG_ACCESS_TOKEN", "IGTOK")
        monkeypatch.setenv("IG_USER_ID", "12345")
        monkeypatch.delenv("IG_VIDEO_URL", raising=False)
        monkeypatch.setenv("IG_HOST_DISABLE", "1")
        uploader = ug.InstagramUploader(str(tmp_path), dry_run=False)
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        with pytest.raises(RuntimeError, match="(?i)IG_HOST_DISABLE|public https"):
            uploader.upload(str(video), "T", "C", [], index=0)

    def test_two_step_publish(self, tmp_path, monkeypatch):
        uploader = self._uploader(tmp_path, monkeypatch)
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        queue = [
            {"payload": {"id": "CREATION1"}},
            {"payload": {"id": "MEDIA9"}},
        ]
        captures = _install_fake_urlopen(monkeypatch, queue)
        result = uploader.upload(str(video), "Big Title", "caption here",
                                 ["#reels", "fyp"], index=0)
        assert result["status"] == "uploaded"
        assert result["media_id"] == "MEDIA9"
        import urllib.parse as up
        media_call = captures[0]
        # Graph API calls are form-encoded (application/x-www-form-urlencoded).
        media_headers = {k.lower(): v for k, v in media_call["headers"].items()}
        assert media_headers["content-type"] == \
            "application/x-www-form-urlencoded"
        body = up.parse_qs(media_call["body"].decode())
        assert body["media_type"] == ["REELS"]
        assert body["video_url"][0].startswith("https://")
        assert "Big Title" in body["caption"][0]
        assert "#reels" in body["caption"][0]
        publish_call = captures[1]
        body2 = up.parse_qs(publish_call["body"].decode())
        assert body2["creation_id"] == ["CREATION1"]
        assert "12345/media_publish" in publish_call["url"]

    def test_video_url_override(self, tmp_path, monkeypatch):
        uploader = ug.InstagramUploader(str(tmp_path), dry_run=False,
                                        video_url="https://cdn.example.com/x.mp4")
        monkeypatch.setenv("IG_ACCESS_TOKEN", "T")
        monkeypatch.setenv("IG_USER_ID", "7")
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        queue = [{"payload": {"id": "C1"}}, {"payload": {"id": "M1"}}]
        _install_fake_urlopen(monkeypatch, queue)
        captures = _install_fake_urlopen(monkeypatch, queue)
        result = uploader.upload(str(video), "T", "C", [], index=0)
        assert result["status"] == "uploaded"
        import urllib.parse as up
        body = up.parse_qs(captures[0]["body"].decode())
        assert body["video_url"] == ["https://cdn.example.com/x.mp4"]
