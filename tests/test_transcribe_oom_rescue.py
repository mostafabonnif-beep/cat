# -*- coding: utf-8 -*-
"""Regression tests for the transcription memory-rescue ladder (v7.32.3).

Root bug (user report 2026-09-07): on a 72-minute video, WhisperX + pyannote
VAD died with ``RuntimeError: bad allocation`` and, on retry, numpy
``_ArrayMemoryError: Unable to allocate 263. MiB ...``. Neither message was in
the OOM-guard markers, so the whole run crashed twice instead of degrading
gracefully. WhisperX's own model-shrinking also cannot help a VAD-stage or
system-RAM failure — the real rescue is the lightweight faster-whisper
backend (CTranslate2 streams audio, uses its own Silero VAD, no pyannote).
"""
import os

from scripts import oom_guard, transcribe_video, transcription_fallback

# --- OOM detection ----------------------------------------------------------

def test_oom_guard_matches_torch_bad_allocation():
    assert oom_guard._looks_like_oom(RuntimeError("bad allocation"))


def test_oom_guard_matches_numpy_unable_to_allocate():
    err = "Unable to allocate 263. MiB for an array with shape (68820696,) and data type float32"
    assert oom_guard._looks_like_oom(RuntimeError(err))


def test_oom_guard_matches_bare_memoryerror_instance():
    # str(MemoryError()) is empty — the isinstance() branch must catch it
    assert oom_guard._looks_like_oom(MemoryError("bad allocation"))


def test_oom_guard_still_ignores_unrelated_errors():
    assert not oom_guard._looks_like_oom(RuntimeError("expected scalar type Long"))
    assert not oom_guard._looks_like_oom(ValueError("Invalid model size"))


def test_is_memory_error_same_coverage_in_transcribe_video():
    assert transcribe_video._is_memory_error(RuntimeError("bad allocation"))
    assert transcribe_video._is_memory_error(
        RuntimeError("Unable to allocate 1.2 GiB for an array"))
    assert transcribe_video._is_memory_error(MemoryError())
    assert not transcribe_video._is_memory_error(RuntimeError("boom"))


# --- faster-whisper rescue --------------------------------------------------

def _write_outputs_fake(result, srt_path, tsv_path, json_path):
    for path in (srt_path, tsv_path, json_path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("{}")


def test_via_fallback_writes_outputs_and_cache(tmp_path, monkeypatch):
    project = str(tmp_path)
    srt = os.path.join(project, "input.srt")
    tsv = os.path.join(project, "input.tsv")
    data = os.path.join(project, "input.json")
    cache = os.path.join(project, "transcription_cache.json")

    calls = {}
    monkeypatch.setattr(
        transcription_fallback, "transcribe",
        lambda input_file, model_name, device="auto", progress=None: (
            calls.update({"input_file": input_file, "model": model_name,
                          "device": device}),
            {"segments": [], "language": "ar", "backend": "faster-whisper"},
        )[1])
    monkeypatch.setattr(transcription_fallback, "write_outputs", _write_outputs_fake)
    saved = {}
    monkeypatch.setattr(
        transcribe_video, "_save_transcription_cache",
        lambda cache_path, input_file, model_name, srt_file, tsv_file, json_file,
               device="auto": saved.update({"_": True}))

    out = transcribe_video._transcribe_via_fallback(
        "input.mp4", "large-v3-turbo", project, srt, tsv, data, cache, "cuda")

    assert out == (srt, tsv)
    assert calls["model"] == "large-v3-turbo"
    assert calls["device"] == "cuda"
    assert saved.get("_") is True
    assert os.path.exists(srt) and os.path.exists(tsv) and os.path.exists(data)


def test_via_fallback_propagates_when_backend_missing(monkeypatch):
    def missing(*a, **k):
        raise ImportError(
            "faster-whisper is not installed; install requirements-transcribe-fallback.txt")

    monkeypatch.setattr(transcription_fallback, "transcribe", missing)
    try:
        transcribe_video._transcribe_via_fallback(
            "in.mp4", "large-v3-turbo", "proj",
            "proj/in.srt", "proj/in.tsv", "proj/in.json", "proj/cache.json", "auto")
    except ImportError as exc:
        assert "faster-whisper" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected ImportError to propagate")
