"""Smoke + unit tests for the CLI entry module (main_improved.py).

The heavy AI/video stacks (cv2 / mediapipe / torch / whisperx / ...) are
stubbed in sys.modules before the module is imported — these tests cover the
pure helpers and the startup/argument paths, not the video pipeline itself.
"""

import json
import os
import sys
import types
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HEAVY_MODULES = [
    "cv2", "mediapipe", "torch", "torchaudio", "whisperx", "insightface",
    "onnxruntime", "av", "moviepy", "librosa", "soundfile",
    # light but not in requirements-dev.txt (CI installs pytest + numpy only)
    "tqdm", "tqdm.asyncio", "psutil",
]


@pytest.fixture(scope="module")
def cli():
    """Import main_improved once, with heavy third-party deps stubbed."""
    saved = {}
    stubs = {}
    for name in _HEAVY_MODULES:
        saved[name] = sys.modules.get(name)
        if saved[name] is None:
            stubs[name] = mock.MagicMock(name=name)
    sys.modules.update(stubs)
    try:
        import main_improved as mod
    finally:
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
    return mod


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_parse_face_detect_interval_single(cli):
    assert cli.parse_face_detect_interval("0.5") == {"1": 0.5, "2": 0.5}


def test_parse_face_detect_interval_pair(cli):
    assert cli.parse_face_detect_interval("0.17, 1.0") == {"1": 0.17, "2": 1.0}


@pytest.mark.parametrize("raw", [None, "", "abc", "0.1,abc", "  "])
def test_parse_face_detect_interval_invalid(cli, raw):
    assert cli.parse_face_detect_interval(raw) is None


def test_load_json_file_missing_returns_default(cli, tmp_path):
    missing = str(tmp_path / "nope.json")
    assert cli.load_json_file(missing) == {}
    assert cli.load_json_file(missing, default={"x": 1}) == {"x": 1}


def test_load_json_file_roundtrip(cli, tmp_path):
    p = tmp_path / "data.json"
    p.write_text(json.dumps({"a": 1, "b": [2, 3]}), encoding="utf-8")
    assert cli.load_json_file(str(p)) == {"a": 1, "b": [2, 3]}


def test_load_json_file_corrupt_returns_default(cli, tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    assert cli.load_json_file(str(p)) == {}
    assert cli.load_json_file(str(p), default={"fallback": True}) == {"fallback": True}


def test_emit_progress_format(cli, capsys):
    cli.emit_progress("ai", 58, "فحص الأمان")
    assert capsys.readouterr().out == "PROGRESS|ai|58|فحص الأمان\n"


def test_emit_progress_coerces_percent(cli, capsys):
    cli.emit_progress("cut", 12.9, "msg")
    assert capsys.readouterr().out == "PROGRESS|cut|12|msg\n"


def test_get_subtitle_config_defaults(cli):
    cfg = cli.get_subtitle_config()
    assert cfg["font"] == "Montserrat-Regular"
    assert cfg["mode"] == "highlight"
    assert cfg["base_color"] == "&H00FFFFFF&"
    assert cfg["highlight_color"] == "&H0000FF00&"
    assert cfg["words_per_block"] == 3


def test_get_subtitle_config_override_keeps_untouched_defaults(cli, tmp_path):
    p = tmp_path / "sub.json"
    p.write_text(json.dumps({"base_size": 42, "mode": "word_by_word"}), encoding="utf-8")
    cfg = cli.get_subtitle_config(str(p))
    assert cfg["base_size"] == 42
    assert cfg["mode"] == "word_by_word"
    assert cfg["font"] == "Montserrat-Regular"


def test_cleanup_temp_files_removes(cli, tmp_path, monkeypatch):
    target = tmp_path / "temp_subtitle_config.json"
    target.write_text("{}")
    monkeypatch.setattr(cli, "TEMP_SUBTITLE_CONFIG", str(target))
    cli.cleanup_temp_files()
    assert not target.exists()


def test_cleanup_temp_files_missing_is_ok(cli, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "TEMP_SUBTITLE_CONFIG", str(tmp_path / "missing.json"))
    cli.cleanup_temp_files()  # must not raise


def test_interactive_input_int_retries_until_valid(cli, monkeypatch):
    answers = iter(["abc", "-5", "3"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    assert cli.interactive_input_int("Pick a number") == 3


# ---------------------------------------------------------------------------
# Startup / argument paths
# ---------------------------------------------------------------------------

def test_main_no_args_launches_webui(cli, monkeypatch):
    """Double-click UX: bare invocation must open the WebUI, never the CLI."""
    monkeypatch.setattr(sys, "argv", ["viralcutter"])
    with mock.patch.object(cli, "_launch_webui") as launch:
        cli.main()
    launch.assert_called_once_with()


def test_main_help_exits_zero_and_lists_flags(cli, monkeypatch, capsys):
    """`--help` exercises the full argparse construction (all flags defined)."""
    monkeypatch.setattr(sys, "argv", ["viralcutter", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--url", "--segments", "--ai-backend", "--workflow", "--face-mode", "--audio-qc", "--audio-qc-gate"):
        assert flag in out


def test_main_unknown_flag_exits_two(cli, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["viralcutter", "--no-such-flag-xyz"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# run_safety_stage (extracted pipeline stage)
# ---------------------------------------------------------------------------

def _safety_args(**over):
    base = dict(safety_mode="block", safety_autoupdate="off",
                safety_min_severity="high", safety_extra_terms=None,
                safety_ai="off", ai_model_name=None)
    base.update(over)
    return types.SimpleNamespace(**base)


def _segments(*texts):
    return {"segments": [{"title": f"clip {i}", "text": t} for i, t in enumerate(texts)]}


def test_resolve_safety_backend_prefers_openai_moderation(cli, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    assert cli.resolve_safety_backend("auto", "manual") == ("openai-moderation", "test-openai-key")


def test_resolve_safety_backend_falls_back_to_local(cli, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODERATION_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert cli.resolve_safety_backend("auto", "manual") == ("local", None)


def test_safety_stage_skips_workflow_3(cli, tmp_path):
    segs = _segments("hello")
    out = cli.run_safety_stage(segs, project_folder=str(tmp_path),
                               args=_safety_args(), ai_backend="manual",
                               api_key=None, workflow_choice="3")
    assert out is segs  # untouched


def test_safety_stage_skips_mode_off(cli, tmp_path):
    segs = _segments("hello")
    out = cli.run_safety_stage(segs, project_folder=str(tmp_path),
                               args=_safety_args(safety_mode="off"),
                               ai_backend="manual", api_key=None,
                               workflow_choice="1")
    assert out is segs


def test_safety_stage_returns_filtered_segments(cli, tmp_path, monkeypatch):
    filtered = {"segments": [{"title": "ok", "text": "fine"}]}
    monkeypatch.setattr(cli.safety_filter, "apply_safety_filter",
                        lambda *a, **k: filtered)
    saved = []
    monkeypatch.setattr(cli.save_json, "save_viral_segments",
                        lambda data, **k: saved.append(data))
    out = cli.run_safety_stage(_segments("bad word"), project_folder=str(tmp_path),
                               args=_safety_args(), ai_backend="manual",
                               api_key=None, workflow_choice="1")
    assert out is filtered
    assert saved == [filtered]  # filtered result persisted


def test_safety_stage_unchanged_filter_does_not_resave(cli, tmp_path, monkeypatch):
    segs = _segments("clean")
    monkeypatch.setattr(cli.safety_filter, "apply_safety_filter",
                        lambda *a, **k: segs)  # same object = nothing blocked
    monkeypatch.setattr(cli.save_json, "save_viral_segments",
                        lambda *a, **k: pytest.fail("must not resave"))
    out = cli.run_safety_stage(segs, project_folder=str(tmp_path),
                               args=_safety_args(), ai_backend="manual",
                               api_key=None, workflow_choice="1")
    assert out is segs


def test_safety_stage_exits_when_everything_blocked(cli, tmp_path, monkeypatch):
    monkeypatch.setattr(cli.safety_filter, "apply_safety_filter",
                        lambda *a, **k: {"segments": []})
    monkeypatch.setattr(cli.save_json, "save_viral_segments", lambda *a, **k: None)
    with pytest.raises(SystemExit) as exc:
        cli.run_safety_stage(_segments("slur"), project_folder=str(tmp_path),
                             args=_safety_args(safety_mode="block"),
                             ai_backend="manual", api_key=None,
                             workflow_choice="1")
    assert exc.value.code == 1


def test_safety_stage_fails_closed_when_filter_errors(cli, tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("filter exploded")
    monkeypatch.setattr(cli.safety_filter, "apply_safety_filter", boom)
    with pytest.raises(SystemExit) as exc:
        cli.run_safety_stage(_segments("anything"), project_folder=str(tmp_path),
                             args=_safety_args(), ai_backend="manual",
                             api_key=None, workflow_choice="1")
    assert exc.value.code == 1



def test_process_segments_keeps_distinct_partial_overlap():
    from scripts.create_viral_segments import process_segments

    transcript = [
        {"start": 0.0, "end": 1.0, "text": "first"},
        {"start": 8.0, "end": 9.0, "text": "second"},
    ]
    raw = [
        {"title": "A", "start_time_ref": "0s", "start_text": "first", "end_text": "", "score": 90},
        {"title": "B", "start_time_ref": "8s", "start_text": "second", "end_text": "", "score": 80},
    ]
    result = process_segments(raw, transcript, 15, 90)
    assert len(result["segments"]) == 2


def test_content_guard_stage_filters_before_export(cli, tmp_path):
    from scripts import content_guard

    root = tmp_path / "VIRALS"
    root.mkdir()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    previous = root / "previous"
    current = root / "current"
    for project in (previous, current):
        project.mkdir()
        (project / "project_manifest.json").write_text(
            json.dumps({"source": {"path": str(source), "managed": False}}),
            encoding="utf-8",
        )
    (previous / "viral_segments.txt").write_text(
        json.dumps({"segments": [{"title": "old", "start_time": 10, "end_time": 30}]}),
        encoding="utf-8",
    )
    (current / "viral_segments.txt").write_text(
        json.dumps({"segments": [{"title": "same", "start_time": 10, "end_time": 30},
                                   {"title": "new", "start_time": 50, "end_time": 70}]}),
        encoding="utf-8",
    )
    assert content_guard.record_publish(
        str(previous), "youtube", str(previous / "old.mp4"), index=0,
        result={"status": "uploaded"})

    out = cli.run_content_guard_stage(
        {"segments": [{"title": "same", "start_time": 10, "end_time": 30},
                      {"title": "new", "start_time": 50, "end_time": 70}]},
        project_folder=str(current), workflow_choice="1")
    assert [item["title"] for item in out["segments"]] == ["new"]
