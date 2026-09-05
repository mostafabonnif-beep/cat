import json
import os
import time

from scripts.pipeline_engine import PipelineEngine
from webui.editor_core import EditorState
from webui.render_queue import RenderQueue


def test_editor_undo_and_roundtrip(tmp_path):
    p = tmp_path / "project.json"
    e = EditorState("input.mp4")
    e.add_clip("input.mp4", 0, 10)
    e.set_transform(0, aspect="9:16", zoom=1.2)
    assert e.undo()
    assert e.clips[0]["zoom"] == 1.0
    e.redo()
    assert e.clips[0]["zoom"] == 1.2
    e.save(p)
    loaded = EditorState.load(p)
    assert loaded.clips[0]["aspect"] == "9:16"
    assert loaded.clips[0]["zoom"] == 1.2

def test_render_queue_preserves_corrupt_state_file(tmp_path):
    path = tmp_path / "queue.json"
    path.write_text('{"jobs":', encoding="utf-8")
    q = RenderQueue(path)
    assert q.snapshot() == {}
    assert q.state_warning
    assert not path.exists()
    assert list(tmp_path.glob("queue.json.corrupt-*"))


def test_render_queue_persists_and_recovers(tmp_path):
    p = tmp_path / "queue.json"
    q = RenderQueue(p)
    jid = q.add({"source": "a.mp4"})
    q.jobs[jid].status = "running"
    q._save()
    q2 = RenderQueue(p)
    assert q2.jobs[jid].status == "queued"

def test_pipeline_dependencies_and_persistence(tmp_path):
    p = tmp_path / "run.json"
    out = []
    e = PipelineEngine(p)
    e.register("a", lambda c,r: out.append("a") or 1)
    e.register("b", lambda c,r: out.append("b") or 2, deps=["a"])
    result = e.run()
    assert out == ["a", "b"]
    assert result["b"] == 2
    assert json.loads(p.read_text())["stages"]["b"]["status"] == "success"

def test_pipeline_cycle_detection(tmp_path):
    e = PipelineEngine(tmp_path / "run.json")
    e.register("a", lambda c,r: 1, deps=["b"])
    e.register("b", lambda c,r: 2, deps=["a"])
    try:
        e.order()
        raise AssertionError("cycle should fail")
    except ValueError:
        pass


def test_render_queue_worker_retry_cancel_and_progress(tmp_path):
    from webui.render_queue import RenderQueue
    attempts = []

    def runner(job, cancel_event, progress):
        attempts.append(job.attempts)
        progress(25, "started")
        if len(attempts) == 1:
            raise RuntimeError("temporary failure")
        progress(100, "done")
        return "output.mp4"

    q = RenderQueue(tmp_path / "worker.json", runner=runner)
    jid = q.add({"source": "a.mp4"}, max_retries=1)
    q.start()
    try:
        assert q.wait(jid, timeout=5) == "succeeded"
        snapshot = q.snapshot(jid)
        assert snapshot["attempts"] == 2
        assert snapshot["progress"] == 100
        assert snapshot["output"] == "output.mp4"
    finally:
        q.stop()


def test_render_queue_requeues_running_job_after_restart(tmp_path):
    from webui.render_queue import RenderQueue
    path = tmp_path / "worker.json"
    q = RenderQueue(path)
    jid = q.add({"source": "a.mp4"})
    q.mark_started(jid)
    q2 = RenderQueue(path)
    assert q2.snapshot(jid)["status"] == "queued"
    assert q2.snapshot(jid)["message"]


def test_project_store_manifest_events_and_safe_names(tmp_path):
    from webui.project_store import (
        append_event,
        create_project,
        list_projects,
        load_manifest,
        read_events,
        safe_project_name,
        update_manifest,
    )
    assert "/" not in safe_project_name("a/b:c")
    project_dir, manifest = create_project(tmp_path, "جلسة/تجربة", source={"type": "upload"})
    assert os.path.isdir(project_dir)
    assert manifest["status"] == "created"
    append_event(project_dir, "render_started", {"clip": 1})
    update_manifest(project_dir, status="completed", outputs=["final/001.mp4"])
    loaded = load_manifest(project_dir)
    assert loaded["status"] == "completed"
    assert loaded["outputs"] == ["final/001.mp4"]
    assert any(event["event"] == "render_started" for event in read_events(project_dir))
    records = list_projects(tmp_path)
    assert records and records[0]["status"] == "completed"


def test_render_queue_prunes_old_terminal_jobs(tmp_path):
    import time

    from webui.render_queue import RenderQueue

    q = RenderQueue(tmp_path / "queue.json")
    jid = q.add({"source": "old.mp4"})
    q.mark_finished(jid, output="old.mp4")
    q.jobs[jid].finished = time.time() - 20 * 86400
    q._save()
    assert q.prune(max_age_days=14, keep_terminal=0) == 1
    assert q.get(jid) is None


def test_editor_rejects_invalid_transform_without_corrupting_state():
    import pytest

    from webui.editor_core import EditorState

    editor = EditorState("input.mp4")
    editor.add_clip("input.mp4", 0, 10)
    before = dict(editor.clips[0])
    with pytest.raises(ValueError):
        editor.set_transform(0, zoom=99)
    assert editor.clips[0] == before


def test_render_queue_cancellation_is_terminal(tmp_path):
    import time

    from webui.render_queue import RenderQueue

    def runner(job, cancel_event, progress):
        while not cancel_event.is_set():
            progress(10, "waiting")
            time.sleep(0.01)
        return "should-not-be-output.mp4"

    q = RenderQueue(tmp_path / "cancel.json", runner=runner)
    jid = q.add({"source": "cancel.mp4"})
    q.start()
    try:
        deadline = time.time() + 3
        while q.snapshot(jid)["status"] == "queued" and time.time() < deadline:
            time.sleep(0.01)
        assert q.cancel(jid) in {"cancelling", "cancelled"}
        assert q.wait(jid, timeout=3) == "cancelled"
    finally:
        q.stop()


def test_translate_chunk_bounded_retry_falls_back(monkeypatch):
    import asyncio

    import scripts.translate_json as translate_json

    class FailingTranslator:
        def translate(self, _text):
            raise RuntimeError("offline")

    monkeypatch.setattr(translate_json, "GoogleTranslator", lambda **_kwargs: FailingTranslator())
    result = asyncio.run(
        translate_json.translate_chunk(1, "النص الأصلي", "en", max_attempts=2, retry_delay=0)
    )
    assert result == "النص الأصلي"


def test_google_translator_parses_response_without_third_party_package(monkeypatch):
    import scripts.translate_json as translate_json

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [[["hello", None], [" world", None], [None, None]]]

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeResponse()

    monkeypatch.setattr(translate_json.requests, "get", fake_get)
    translator = translate_json.GoogleTranslator(source="ar", target="en", timeout=4)

    assert translator.translate("مرحبا") == "hello world"
    assert captured["url"].endswith("/translate_a/single")
    assert captured["kwargs"]["params"]["sl"] == "ar"
    assert captured["kwargs"]["params"]["tl"] == "en"
    assert captured["kwargs"]["timeout"] == 4


def test_adjust_segments_accepts_empty_subtitle_text():
    from scripts.translate_json import adjust_segments

    segments = adjust_segments([
        {"text": "", "start": 0, "end": 1, "words": []},
        {"text": "hello", "start": 1, "end": 2, "words": []},
    ])

    assert segments[0]["words"] == []
    assert segments[1]["words"][0]["word"] == "hello"


def test_transcription_validation_checks_srt_and_tsv(tmp_path):
    from scripts.transcription_validation import validate_transcription

    srt = tmp_path / "clip.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nمرحبا\n\n2\n00:00:02,100 --> 00:00:04,000\nبداية جيدة\n",
        encoding="utf-8",
    )
    report = validate_transcription(str(srt), str(tmp_path / "missing.tsv"))
    assert report["ok"] is True
    assert report["count"] == 2

    bad = tmp_path / "bad.srt"
    bad.write_text("1\n00:00:03,000 --> 00:00:01,000\n\n", encoding="utf-8")
    bad_report = validate_transcription(str(bad), str(tmp_path / "missing.tsv"))
    assert bad_report["ok"] is False


def test_subtitle_editor_timestamp_and_preview_helpers():
    from webui.subtitle_editor import format_timestamp, parse_timestamp

    assert format_timestamp(1.9999) == "00:00:02,000"
    assert format_timestamp(-2) == "00:00:00,000"
    assert abs(parse_timestamp("01:02:03,500") - 3723.5) < 0.001


def test_project_store_rejects_path_traversal(tmp_path):
    import pytest

    from webui.project_store import safe_project_path

    assert os.path.dirname(safe_project_path(tmp_path, "جلسة_01")) == str(tmp_path)
    with pytest.raises(ValueError):
        safe_project_path(tmp_path, "../outside")
    with pytest.raises(ValueError):
        safe_project_path(tmp_path, "/tmp/outside")


def test_render_queue_pause_persists_and_resumes(tmp_path):
    path = tmp_path / "paused.json"
    q = RenderQueue(path)
    jid = q.add({"source": "a.mp4"}, priority=3)
    assert q.pause_all() is True
    q2 = RenderQueue(path)
    assert q2.paused is True
    assert q2.snapshot(jid)["priority"] == 3
    assert q2.resume_all() is True
    assert q2.paused is False


def test_render_queue_deduplicates_repeated_enqueue(tmp_path):
    q = RenderQueue(tmp_path / "dedupe.json")
    jid = q.add({"source": "a.mp4"})
    assert q._enqueue(jid) is True
    assert q._enqueue(jid) is False
    assert q._work.qsize() == 1


def test_render_queue_priority_orders_jobs(tmp_path):
    order = []

    def runner(job, cancel_event, progress):
        order.append(job.plan["name"])
        return job.plan["name"]

    q = RenderQueue(tmp_path / "priority.json", runner=runner)
    q.add({"name": "low"}, priority=-1)
    q.add({"name": "high"}, priority=5)
    q.add({"name": "normal"}, priority=0)
    q.start()
    try:
        deadline = time.time() + 5
        while len(order) < 3 and time.time() < deadline:
            time.sleep(0.01)
        assert order == ["high", "normal", "low"]
    finally:
        q.stop()


def test_render_queue_retry_failed_resets_selected_jobs(tmp_path):
    q = RenderQueue(tmp_path / "retry.json")
    failed = q.add({"source": "failed.mp4"})
    done = q.add({"source": "done.mp4"})
    q.mark_failed(failed, "boom")
    q.mark_finished(done, output="done.mp4")
    assert q.retry_failed([failed, done]) == [failed]
    assert q.snapshot(failed)["status"] == "queued"
    assert q.snapshot(done)["status"] == "succeeded"


def test_transcription_repair_removes_empty_entries_from_all_artifacts(tmp_path):
    from scripts.transcription_validation import (
        repair_transcription_artifacts,
        validate_transcription,
    )

    srt = tmp_path / "input.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nنص صالح\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\n\n",
        encoding="utf-8",
    )
    tsv = tmp_path / "input.tsv"
    tsv.write_text(
        "start\tend\ttext\n"
        "0.000\t2.000\tنص صالح\n"
        "2.000\t3.000\t\n",
        encoding="utf-8",
    )
    transcript_json = tmp_path / "input.json"
    transcript_json.write_text(
        json.dumps({
            "language": "ar",
            "segments": [
                {"start": 0, "end": 2, "text": "نص صالح"},
                {"start": 2, "end": 3, "text": ""},
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    report = repair_transcription_artifacts(str(srt), str(tsv), str(transcript_json))
    assert report == {
        "changed": True,
        "removed": {"srt": 1, "tsv": 1, "json": 1},
        "total_removed": 3,
    }
    validated = validate_transcription(str(srt), str(tsv))
    assert validated["ok"] is True
    assert validated["count"] == 1
    assert "\t\n" not in tsv.read_text(encoding="utf-8")
    assert '"text": ""' not in transcript_json.read_text(encoding="utf-8")
