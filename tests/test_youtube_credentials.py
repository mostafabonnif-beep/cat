import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webui import youtube_credentials as yc


def _oauth_payload(client_id="id-1"):
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": "secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def test_store_reports_changed_and_invalidates_token(tmp_path, monkeypatch):
    client = tmp_path / "client.json"
    client.write_text(json.dumps(_oauth_payload("id-1")), encoding="utf-8")
    target = tmp_path / "stored.json"
    token = tmp_path / "token.json"
    token.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("YT_TOKEN_FILE", str(token))

    first = yc.store_client_secrets(str(client), destination=str(target))
    assert first["changed"] is True
    assert yc.replace_client_secrets(str(client), destination=str(target))["token_invalidated"] is True

    second = yc.store_client_secrets(str(client), destination=str(target))
    assert second["changed"] is False


def test_verify_channel_uses_mine_true(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    secrets = tmp_path / "client.json"
    token.write_text("{}", encoding="utf-8")
    secrets.write_text(json.dumps(_oauth_payload()), encoding="utf-8")

    # Use the real class but replace its credential loader below.
    from scripts.upload_gate import YouTubeUploader

    uploader = YouTubeUploader(
        str(tmp_path), dry_run=True,
        client_secrets_path=str(secrets), token_path=str(token),
    )
    monkeypatch.setattr(uploader, "ensure_authenticated", lambda: object())
    calls = {}

    class Request:
        def execute(self):
            calls["executed"] = True
            return {"items": [{"id": "UC123", "snippet": {"title": "Oussama Channel"}}]}

    class Channels:
        def list(self, **kwargs):
            calls["kwargs"] = kwargs
            return Request()

    class Service:
        def channels(self):
            return Channels()

    fake_google = types.ModuleType("googleapiclient.discovery")
    fake_google.build = lambda *args, **kwargs: Service()
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", fake_google)

    result = uploader.verify_channel()
    assert result == {"id": "UC123", "title": "Oussama Channel"}
    assert calls["kwargs"] == {"part": "id,snippet", "mine": True}
    assert calls["executed"] is True


def test_atomic_private_write_works_without_fchmod(tmp_path, monkeypatch):
    target = tmp_path / "oauth.json"
    monkeypatch.delattr(os, "fchmod", raising=False)
    written = yc._atomic_private_write(target, b'{"ok": true}\n')
    assert written == target
    assert target.read_text(encoding="utf-8") == '{"ok": true}\n'


def test_upload_scopes_include_channel_read_access():
    assert "https://www.googleapis.com/auth/youtube.upload" in yc.scopes_for_access(False)
    assert "https://www.googleapis.com/auth/youtube.readonly" in yc.scopes_for_access(False)
    assert yc.missing_scopes(
        ["https://www.googleapis.com/auth/youtube.upload"], False
    ) == ["https://www.googleapis.com/auth/youtube.readonly"]


def test_verify_channel_insufficient_permissions_invalidates_token(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    secrets = tmp_path / "client.json"
    token.write_text("{}", encoding="utf-8")
    secrets.write_text(json.dumps(_oauth_payload()), encoding="utf-8")

    from scripts.upload_gate import YouTubeUploader

    uploader = YouTubeUploader(
        str(tmp_path), dry_run=True,
        client_secrets_path=str(secrets), token_path=str(token),
    )
    monkeypatch.setattr(uploader, "ensure_authenticated", lambda: object())

    class Request:
        def execute(self):
            raise RuntimeError(
                "HttpError 403: Request had insufficient authentication scopes; "
                "reason=insufficientPermissions"
            )

    class Channels:
        def list(self, **kwargs):
            return Request()

    class Service:
        def channels(self):
            return Channels()

    fake_google = types.ModuleType("googleapiclient.discovery")
    fake_google.build = lambda *args, **kwargs: Service()
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", fake_google)

    try:
        uploader.verify_channel()
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("verify_channel should reject insufficient scopes")
    assert "نطاقات OAuth غير كافية" in message
    assert "تسجيل الدخول" in message
    assert not token.exists()
