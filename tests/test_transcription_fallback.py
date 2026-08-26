import json
from types import SimpleNamespace

import pytest

import scripts.transcribe_video as transcribe_video
from scripts import transcription_diagnostics, transcription_fallback
from scripts.transcription_diagnostics import TranscriptionUnavailableError


def test_normalize_segments_drops_invalid_items_and_keeps_words():
    segments = [
        SimpleNamespace(
            start=1.25,
            end=3.5,
            text="  مرحباً بالعالم  ",
            words=[
                SimpleNamespace(word="مرحباً", start=1.25, end=1.8),
                SimpleNamespace(word="", start=2.0, end=2.1),
            ],
        ),
        SimpleNamespace(start=4.0, end=4.0, text="invalid"),
        SimpleNamespace(start=None, end=5.0, text="invalid"),
    ]
    result = transcription_fallback.normalize_segments(segments)
    assert result == [
        {
            "start": 1.25,
            "end": 3.5,
            "text": "مرحباً بالعالم",
            "words": [{"word": "مرحباً", "start": 1.25, "end": 1.8}],
        }
    ]


def test_write_outputs_creates_pipeline_compatible_files(tmp_path):
    result = {
        "segments": [{"start": 0.25, "end": 1.5, "text": "hello\nworld"}],
        "language": "en",
        "backend": "faster-whisper",
    }
    srt = tmp_path / "input.srt"
    tsv = tmp_path / "input.tsv"
    data = tmp_path / "input.json"
    transcription_fallback.write_outputs(result, str(srt), str(tsv), str(data))
    assert "00:00:00,250 --> 00:00:01,500" in srt.read_text(encoding="utf-8")
    assert "hello world" in srt.read_text(encoding="utf-8")
    assert "start\tend\ttext" in tsv.read_text(encoding="utf-8")
    assert json.loads(data.read_text(encoding="utf-8"))["backend"] == "faster-whisper"


def test_transcribe_uses_faster_whisper_when_primary_stack_is_missing(monkeypatch, tmp_path):
    class FakeModel:
        def __init__(self, model_name, device, compute_type):
            self.args = (model_name, device, compute_type)

        def transcribe(self, input_file, **kwargs):
            assert input_file.endswith("input.mp4")
            assert kwargs["vad_filter"] is True
            return iter([
                SimpleNamespace(start=0.0, end=2.0, text="نص تجريبي", words=[]),
            ]), SimpleNamespace(language="ar")

    fake_module = SimpleNamespace(__version__="1.2.0", WhisperModel=FakeModel)
    monkeypatch.setattr(transcribe_video, "whisperx", None)
    monkeypatch.setattr(transcribe_video, "torch", None)
    monkeypatch.setattr(transcription_fallback, "_module", lambda: fake_module)
    input_file = tmp_path / "input.mp4"
    input_file.write_bytes(b"fake media")

    srt_file, tsv_file = transcribe_video.transcribe(
        str(input_file), model_name="large-v3-turbo", project_folder=str(tmp_path), device="cpu"
    )

    assert srt_file.endswith("input.srt")
    assert tsv_file.endswith("input.tsv")
    assert "نص تجريبي" in (tmp_path / "input.srt").read_text(encoding="utf-8")
    cache = json.loads((tmp_path / "transcription_cache.json").read_text(encoding="utf-8"))
    assert cache["model_name"] == "large-v3-turbo"


def test_transcribe_fails_closed_when_both_backends_are_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(transcribe_video, "whisperx", None)
    monkeypatch.setattr(transcribe_video, "torch", None)
    monkeypatch.setattr(transcription_fallback, "_module", lambda: None)
    monkeypatch.delenv("VIRALCUTTER_ALLOW_PLACEHOLDER", raising=False)
    input_file = tmp_path / "input.mp4"
    input_file.write_bytes(b"fake media")

    with pytest.raises(TranscriptionUnavailableError) as error:
        transcribe_video.transcribe(
            str(input_file), project_folder=str(tmp_path), device="cpu"
        )
    assert "requirements-transcribe-fallback.txt" in str(error.value)


def test_transcription_backend_can_be_forced(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_TRANSCRIPTION_BACKEND", "faster-whisper")
    monkeypatch.setattr(transcribe_video, "whisperx", object())
    monkeypatch.setattr(transcribe_video, "torch", object())
    assert transcribe_video._transcription_backend_preference() == "faster-whisper"


def test_repair_fallback_only_installs_optional_profile(monkeypatch):
    calls = []
    monkeypatch.setattr(
        transcription_diagnostics,
        "_run_install",
        lambda command: calls.append(command) or {"ok": True},
    )
    monkeypatch.setattr(
        transcription_diagnostics,
        "diagnose",
        lambda base_dir=None: {"fallback_ready": True},
    )
    result = transcription_diagnostics.repair_fallback()
    assert result["ok"] is True
    assert result["mode"] == "fallback"
    assert calls and "requirements-transcribe-fallback.txt" in " ".join(calls[0])
