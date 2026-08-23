from webui import project_store


def test_local_source_is_reference_only(tmp_path):
    virals = tmp_path / "VIRALS"
    source = tmp_path / "recording.mp4"
    source.write_bytes(b"video")

    project, _ = project_store.create_project(
        virals,
        "recording_01",
        source={"type": "local", "path": str(source), "managed": False},
        settings={"storage": "external_reference"},
    )

    assert project_store.resolve_project_input(project) == str(source.resolve())
    assert not (project and (tmp_path / "VIRALS" / "recording_01" / "input.mp4").exists())
    assert project_store.list_projects(virals)[0]["has_input"] is True


def test_legacy_input_mp4_remains_supported(tmp_path):
    project = tmp_path / "VIRALS" / "legacy"
    project.mkdir(parents=True)
    legacy = project / "input.mp4"
    legacy.write_bytes(b"legacy")

    assert project_store.resolve_project_input(project) == str(legacy.resolve())
