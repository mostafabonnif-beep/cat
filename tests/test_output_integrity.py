import json

from scripts import background_music, cut_segments
from scripts.cut_segments import _clear_downstream_artifacts
from scripts.save_json import _atomic_write_json, save_viral_segments
from webui.project_store import list_projects


def test_background_music_reports_probe_failure(tmp_path, monkeypatch):
    source = tmp_path / "clip.mp4"
    output = tmp_path / "music.mp4"
    source.write_bytes(b"clip")
    music = tmp_path / "music.mp3"
    music.write_bytes(b"music")
    monkeypatch.setattr(background_music, "_has_audio_stream", lambda path: None)

    report = background_music.apply_background_music(
        str(source), str(music), str(output)
    )

    assert report["ok"] is False
    assert "audio stream" in report["error"]
    assert output.read_bytes() == source.read_bytes()


def test_clear_downstream_artifacts_removes_generated_outputs_only(tmp_path):
    for folder_name in ("final", "final_polished", "burned_sub"):
        folder = tmp_path / folder_name
        folder.mkdir()
        (folder / "old.mp4").write_bytes(b"old")
        (folder / "old_coords.json").write_text("{}", encoding="utf-8")
        (folder / "keep.txt").write_text("keep", encoding="utf-8")
    (tmp_path / "polish_report.json").write_text("{}", encoding="utf-8")
    (tmp_path / "publish_batch_report.json").write_text("{}", encoding="utf-8")
    (tmp_path / "publish_history.jsonl").write_text("{}\n", encoding="utf-8")

    removed = _clear_downstream_artifacts(str(tmp_path))

    assert len(removed) == 8
    assert not (tmp_path / "final" / "old.mp4").exists()
    assert not (tmp_path / "final_polished" / "old_coords.json").exists()
    assert (tmp_path / "final" / "keep.txt").exists()
    assert (tmp_path / "publish_history.jsonl").exists()


def test_cut_stops_pipeline_when_one_segment_fails(tmp_path, monkeypatch):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"source")
    response = {"segments": [{"start_time": 0, "end_time": 5}, {"start_time": 5, "end_time": 10}]}

    monkeypatch.setattr(cut_segments, "_detect_best_encoder", lambda: ("copy", "fast", []))
    monkeypatch.setattr(
        cut_segments,
        "_process_segment",
        lambda index, *args: {"ok": index == 0, "index": index, "error": "ffmpeg failed"},
    )

    try:
        cut_segments.cut(response, project_folder=str(tmp_path), workers=1, source_video=str(source))
    except RuntimeError as exc:
        assert "downstream stages were stopped" in str(exc)
    else:
        raise AssertionError("cut must stop when a segment fails")


def test_project_library_detects_final_polished_files(tmp_path):
    root = tmp_path / "VIRALS"
    project = root / "project-one"
    (project / "final_polished").mkdir(parents=True)
    (project / "final_polished" / "000_clip.mp4").write_bytes(b"video")
    (root / "empty-project" / "final_polished").mkdir(parents=True)

    rows = {row["name"]: row for row in list_projects(str(root))}

    assert rows["project-one"]["has_outputs"] is True
    assert rows["empty-project"]["has_outputs"] is False


def test_atomic_write_json_replaces_complete_document(tmp_path):
    target = tmp_path / "viral_segments.txt"
    _atomic_write_json(str(target), {"segments": [{"start_time": 1, "end_time": 5}]})
    assert json.loads(target.read_text(encoding="utf-8"))["segments"][0]["start_time"] == 1
    assert not list(tmp_path.glob(".viral_segments.*.tmp"))


def test_save_viral_segments_overwrite_is_atomic_and_current(tmp_path):
    save_viral_segments({"segments": [{"title": "new", "start_time": 10, "end_time": 20}]}, str(tmp_path), overwrite=True)
    data = json.loads((tmp_path / "viral_segments.txt").read_text(encoding="utf-8"))
    assert data["segments"][0]["title"] == "new"
