import json
import zipfile

import pytest

from webui.backup import create_backup, inspect_backup, restore_backup


def test_backup_excludes_secrets_and_media_by_default(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "project_manifest.json").write_text("{}", encoding="utf-8")
    (project / "token.json").write_text("secret", encoding="utf-8")
    (project / "client_secrets.json").write_text("secret", encoding="utf-8")
    (project / "clip.mp4").write_bytes(b"video")
    result = create_backup(str(project))
    assert result["files"] == 1
    with zipfile.ZipFile(result["path"]) as archive:
        names = archive.namelist()
    assert "project/project_manifest.json" in names
    assert not any("token.json" in name or "client_secrets.json" in name for name in names)
    assert not any(name.endswith("clip.mp4") for name in names)


def test_backup_with_media_inspects_and_restores_to_new_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "metadata.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (project / "clip.mp4").write_bytes(b"video")
    result = create_backup(str(project), include_media=True)
    info = inspect_backup(result["path"])
    assert info["manifest"]["include_media"] is True
    restored = restore_backup(result["path"], str(tmp_path / "restored"))
    assert (tmp_path / "restored" / "project_restored" / "clip.mp4").read_bytes() == b"video"
    restored_again = restore_backup(result["path"], str(tmp_path / "restored"))
    assert restored_again != restored


def test_backup_rejects_invalid_archive(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not zip")
    with pytest.raises(ValueError):
        inspect_backup(str(bad))
