import json
from pathlib import Path

from scripts import content_guard


def _project(root: Path, name: str, source: Path, segments=None) -> Path:
    project = root / name
    project.mkdir(parents=True)
    (project / "project_manifest.json").write_text(
        json.dumps({"source": {"type": "local", "path": str(source), "managed": False}}),
        encoding="utf-8",
    )
    (project / "viral_segments.txt").write_text(
        json.dumps({"segments": segments or [{"title": "لقطة", "start_time": 10, "end_time": 30}]}),
        encoding="utf-8",
    )
    return project


def test_exact_file_is_blocked_across_projects(tmp_path):
    root = tmp_path / "VIRALS"
    root.mkdir()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    project_a = _project(root, "a", source)
    project_b = _project(root, "b", source)
    output_a = project_a / "clip.mp4"
    output_b = project_b / "clip.mp4"
    output_a.write_bytes(b"same-rendered-output")
    output_b.write_bytes(b"same-rendered-output")

    assert content_guard.record_publish(
        str(project_a), "youtube", str(output_a), title="A", index=0,
        result={"status": "uploaded", "video_id": "one"})
    verdict = content_guard.assess_clip(
        str(project_b), 0, title="B", video_path=str(output_b), platform="youtube")

    assert verdict["allowed"] is False
    assert any(r["code"] == "exact_file_already_published" for r in verdict["reasons"])


def test_same_source_window_is_filtered_before_cutting(tmp_path):
    root = tmp_path / "VIRALS"
    root.mkdir()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    project_a = _project(root, "a", source)
    project_b = _project(root, "b", source)
    assert content_guard.record_publish(
        str(project_a), "youtube", str(project_a / "a.mp4"), index=0,
        result={"status": "scheduled"})

    kept, report = content_guard.filter_segments(
        str(project_b), [{"title": "same window", "start_time": 10, "end_time": 30},
                         {"title": "new window", "start_time": 50, "end_time": 70}])

    assert [item["title"] for item in kept] == ["new window"]
    assert report["blocked"] == 1
    saved = json.loads((project_b / content_guard.REPORT_FILENAME).read_text(encoding="utf-8"))
    assert saved["blocked_segments"][0]["reasons"][0]["code"] == "source_window_already_published"


def test_source_publish_rate_limit_blocks_mass_publication(tmp_path):
    root = tmp_path / "VIRALS"
    root.mkdir()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    segments = [{"title": str(i), "start_time": i * 20, "end_time": i * 20 + 10} for i in range(10)]
    project = _project(root, "project", source, segments)
    for index in range(content_guard.MAX_SOURCE_PUBLISHES_PER_DAY):
        assert content_guard.record_publish(
            str(project), "youtube", str(project / f"{index}.mp4"), index=index,
            result={"status": "uploaded"})

    verdict = content_guard.assess_clip(str(project), 9, title="ninth", platform="youtube")
    assert verdict["allowed"] is False
    assert any(r["code"] == "source_publish_rate_limit" for r in verdict["reasons"])


def test_dry_run_and_failed_status_are_not_registered(tmp_path):
    root = tmp_path / "VIRALS"
    root.mkdir()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    project = _project(root, "project", source)
    output = project / "clip.mp4"
    output.write_bytes(b"output")

    assert content_guard.record_publish(str(project), "youtube", str(output), status="dry-run") is False
    assert content_guard.record_publish(str(project), "youtube", str(output), status="failed") is False
    verdict = content_guard.assess_clip(str(project), 0, video_path=str(output))
    assert verdict["allowed"] is True
    assert "exact_match" not in verdict["evidence"]


def test_policy_ambiguous_candidate_is_blocked_before_export(tmp_path):
    root = tmp_path / "VIRALS"
    root.mkdir()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    project = _project(root, "project", source)
    kept, report = content_guard.filter_segments(
        str(project),
        [{"title": "خبر توعوي", "text": "نرفض قتل المهاجرين ونحذر من العنف", "start_time": 0, "end_time": 20},
         {"title": "مقطع عادي", "text": "نصيحة مفيدة للمشاهدين", "start_time": 30, "end_time": 50}],
    )

    assert [item["title"] for item in kept] == ["مقطع عادي"]
    assert report["blocked"] == 1
    reason_codes = {reason["code"] for reason in report["blocked_segments"][0]["reasons"]}
    assert "semantic_policy_review" in reason_codes


def test_policy_incident_locks_future_automation_but_quota_does_not(tmp_path):
    root = tmp_path / "VIRALS"
    root.mkdir()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    project = _project(root, "project", source)

    assert content_guard.record_platform_error(
        str(project), "youtube", "HTTP 403: Community Guidelines strike") is True
    assert content_guard.record_platform_error(
        str(project), "youtube", "HTTP 429 quota exceeded") is False
    state = content_guard.channel_status(str(project), "youtube")
    assert state["locked"] is True

    verdict = content_guard.assess_clip(str(project), 0, platform="youtube")
    assert verdict["allowed"] is False
    assert any(r["code"] == "channel_circuit_breaker" for r in verdict["reasons"])


def test_acknowledgement_unlocks_without_deleting_incident(tmp_path):
    root = tmp_path / "VIRALS"
    root.mkdir()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    project = _project(root, "project", source)
    content_guard.record_channel_incident(
        str(project), "youtube", "policy", "Community Guidelines warning", lock=True)

    assert content_guard.acknowledge_channel_risk(str(project), "youtube") == 1
    state = content_guard.channel_status(str(project), "youtube")
    assert state["locked"] is False
    assert state["count"] == 1
