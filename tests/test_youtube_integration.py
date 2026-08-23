import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from scripts.upload_gate import YouTubeUploader
from webui import publish_history
from webui.youtube_credentials import (
    store_client_secrets,
    validate_client_secrets_payload,
)


def _oauth_payload():
    return {
        "installed": {
            "client_id": "demo.apps.googleusercontent.com",
            "client_secret": "not-a-real-secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def test_oauth_payload_validation_and_private_storage(tmp_path):
    source = tmp_path / "client_secrets.json"
    source.write_text(json.dumps(_oauth_payload()), encoding="utf-8")
    target = tmp_path / "secure" / "client_secrets.json"

    metadata = store_client_secrets(source, target)
    assert metadata["stored"] is True
    assert metadata["client_type"] == "installed"
    assert metadata["scopes"] == [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
    ]
    assert json.loads(target.read_text(encoding="utf-8")) == _oauth_payload()
    if os.name != "nt":
        assert oct(target.stat().st_mode & 0o777) == "0o600"


def test_oauth_payload_rejects_missing_client():
    with pytest.raises(ValueError, match="installed"):
        validate_client_secrets_payload({"wrong": {}})


def test_youtube_publish_at_is_normalized_and_requires_future_timezone():
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    normalized = YouTubeUploader._normalize_publish_at(future)
    assert normalized.endswith("Z")
    assert YouTubeUploader._normalize_publish_at("") is None
    with pytest.raises(ValueError, match="timezone"):
        YouTubeUploader._normalize_publish_at("2030-01-01T12:00:00")
    with pytest.raises(ValueError, match="future"):
        YouTubeUploader._normalize_publish_at("2020-01-01T12:00:00Z")


def test_publish_history_is_secret_free_and_persistent(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    clip = project / "clip.mp4"
    clip.write_bytes(b"video")
    publish_history.record(
        str(project), platform="youtube", video_path=str(clip),
        title="A safe title", result={"status": "scheduled", "video_id": "abc123"},
        privacy_status="private", publish_at="2030-01-01T12:00:00Z")
    rows = publish_history.load(str(project))
    assert rows[-1]["status"] == "scheduled"
    assert rows[-1]["video_id"] == "abc123"
    assert "client_secret" not in json.dumps(rows[-1])


def test_publish_history_deduplicates_successful_file_but_not_dry_run(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    clip = project / "clip.mp4"
    clip.write_bytes(b"video")
    publish_history.record(
        str(project), platform="youtube", video_path=str(clip), title="Title",
        result={"status": "dry-run"})
    assert publish_history.find_success(
        str(project), platform="youtube", video_path=str(clip)) is None
    publish_history.record(
        str(project), platform="youtube", video_path=str(clip), title="Title",
        result={"status": "uploaded", "video_id": "abc123"})
    prior = publish_history.find_success(
        str(project), platform="youtube", video_path=str(clip))
    assert prior and prior["video_id"] == "abc123"


def test_publish_history_ignores_corrupt_lines(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / publish_history.HISTORY_NAME).write_text(
        "{bad json}\n{" + '"status": "uploaded"' + "}\n", encoding="utf-8")
    assert publish_history.load(str(project)) == [{"status": "uploaded"}]
