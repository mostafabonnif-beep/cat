import json


def _oauth_payload(client_id="demo.apps.googleusercontent.com"):
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": "not-a-real-secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def test_file_input_normalization(tmp_path):
    from webui import file_inputs

    music = tmp_path / "bed.m4a"
    logo = tmp_path / "logo.png"
    sfx = tmp_path / "pop.wav"
    for path in (music, logo, sfx):
        path.write_bytes(b"x")

    assert file_inputs.first_path(str(music)).endswith("bed.m4a")
    assert file_inputs.first_path({"path": str(logo)}).endswith("logo.png")
    assert file_inputs.common_parent([str(sfx), str(logo)]) == str(tmp_path)


def test_replace_client_secrets_invalidates_previous_token(tmp_path, monkeypatch):
    from webui.youtube_credentials import replace_client_secrets

    source = tmp_path / "client_secrets.json"
    source.write_text(json.dumps(_oauth_payload()), encoding="utf-8")
    target = tmp_path / "private" / "client_secrets.json"
    token = tmp_path / "private" / "token.json"
    token.parent.mkdir()
    token.write_text('{"refresh_token":"old"}', encoding="utf-8")
    monkeypatch.setenv("YT_TOKEN_FILE", str(token))

    result = replace_client_secrets(source, destination=target)
    assert result["stored"] is True
    assert result["token_invalidated"] is True
    assert not token.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["installed"]["client_id"] == "demo.apps.googleusercontent.com"
