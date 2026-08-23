from scripts import upload_gate as ug
from webui import app, publish_panel


def _validation_args(**overrides):
    values = {
        "input_source": "YouTube URL",
        "project_name": None,
        "url": "https://youtu.be/abc",
        "video_file": None,
        "segments": 3,
        "min_duration": 15,
        "max_duration": 60,
        "workflow": "Full",
        "ai_backend": "manual",
        "transcription_device": "auto",
        "safety_mode": "block",
        "visual_check": "auto",
        "visual_model": "",
        "logo_path": "",
        "music_path": "",
        "auto_upload": False,
        "auto_upload_dry_run": True,
        "auto_upload_source": "auto",
        "auto_upload_specific_file": None,
        "auto_upload_privacy": "private",
        "auto_upload_publish_at": "",
        "auto_upload_public_confirm": False,
    }
    values.update(overrides)
    return values


def test_expected_project_clip_count_reads_current_segments(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "viral_segments.txt").write_text(
        '{"segments": [{"start_time": 0, "end_time": 5}, {"start_time": 10, "end_time": 15}]}',
        encoding="utf-8",
    )
    assert app._expected_project_clip_count(str(project)) == 2
    assert app._expected_project_clip_count(str(tmp_path / "missing")) == 0


def test_public_upload_is_blocked_before_uploader_creation(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    clip = project / "final.mp4"
    clip.write_bytes(b"fake mp4")

    def fail_if_created(*_args, **_kwargs):
        raise AssertionError("uploader must not be created for an unconfirmed public upload")

    monkeypatch.setattr(ug, "UPLOADERS", {"youtube": fail_if_created})
    lines = list(
        publish_panel.stream_upload(
            str(project), "youtube", str(clip), "Title", "Caption", [], False, "warn",
            privacy_status="public", public_confirm=False,
        )
    )
    output = "\n".join(lines)
    assert "تم إيقاف الرفع العام" in output
    assert "uploader must not" not in output


def test_public_upload_dry_run_does_not_require_confirmation(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    clip = project / "final.mp4"
    clip.write_bytes(b"fake mp4")
    seen = {}

    class FakeUploader:
        def __init__(self, *_args, **kwargs):
            seen.update(kwargs)

        def upload(self, *_args, **_kwargs):
            return {"status": "dry-run"}

    monkeypatch.setattr(ug, "UPLOADERS", {"youtube": FakeUploader})
    output = "\n".join(
        publish_panel.stream_upload(
            str(project), "youtube", str(clip), "Title", "Caption", [], True, "warn",
            privacy_status="public", public_confirm=False,
        )
    )
    assert "Upload finished." in output
    assert seen["dry_run"] is True


def test_processing_validator_rejects_invalid_input_and_public_without_confirmation():
    from webui.app import validate_processing_config

    report = validate_processing_config(
        **_validation_args(
            url="https://example.com/not-youtube",
            auto_upload=True,
            auto_upload_dry_run=False,
            auto_upload_privacy="public",
        )
    )
    assert any("رابط YouTube" in item for item in report["errors"])
    assert any("النشر العام الحقيقي" in item for item in report["errors"])


def test_processing_validator_accepts_safe_dry_run():
    from webui.app import validate_processing_config

    report = validate_processing_config(**_validation_args(auto_upload=True, auto_upload_privacy="public"))
    assert report["errors"] == []
    assert any("Dry Run" in item for item in report["warnings"])


def test_youtube_preflight_checks_channel_and_schedule_without_upload(tmp_path, monkeypatch):
    import scripts.content_guard as content_guard
    import scripts.upload_gate as upload_gate
    import webui.youtube_credentials as credentials
    from webui import app

    oauth = tmp_path / "client_secrets.json"
    oauth.write_text("{}", encoding="utf-8")
    seen = {}

    monkeypatch.setattr(credentials, "store_client_secrets", lambda path: {
        "path": str(tmp_path / "stored-client.json"), "changed": False,
    })
    monkeypatch.setattr(content_guard, "channel_status", lambda *_args, **_kwargs: {
        "locked": False, "incidents": [],
    })

    class FakeUploader:
        def __init__(self, project_folder, **kwargs):
            seen["project"] = project_folder
            seen["kwargs"] = kwargs

        def verify_channel(self):
            seen["verified"] = True
            return {"id": "UC-test", "title": "Test channel"}

    monkeypatch.setattr(upload_gate, "YouTubeUploader", FakeUploader)
    result = app.prepare_youtube_preflight(
        str(tmp_path / "project"), str(oauth), dry_run=False,
        privacy_status="private", publish_at="2030-01-01T10:00:00+00:00",
        schedule_interval_minutes=90, expected_clips=6,
    )
    assert result["ready"] is True
    assert result["scheduled_count"] == 6
    assert result["schedule_last"].startswith("2030-01-01T17:30:00")
    assert seen["verified"] is True
    assert seen["kwargs"]["dry_run"] is True


def test_schedule_requires_timezone_future_and_valid_interval():
    from webui.app import validate_processing_config

    good = validate_processing_config(**_validation_args(
        auto_upload=True,
        auto_upload_publish_at="2030-01-01T10:00:00+00:00",
        auto_upload_interval_minutes=90,
    ))
    assert good["errors"] == []

    naive = validate_processing_config(**_validation_args(
        auto_upload=True,
        auto_upload_publish_at="2030-01-01T10:00:00",
    ))
    assert any("منطقة زمنية" in item for item in naive["errors"])

    invalid_interval = validate_processing_config(**_validation_args(
        auto_upload=True,
        auto_upload_interval_minutes=0,
    ))
    assert any("فاصل الجدولة" in item for item in invalid_interval["errors"])
