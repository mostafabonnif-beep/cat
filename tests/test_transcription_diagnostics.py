from scripts.transcription_diagnostics import (
    TranscriptionUnavailableError,
    build_error_message,
    diagnose,
)


def test_diagnostics_is_json_serializable():
    report = diagnose(".")
    assert report["app"] == "OUSSAMA Cutter"
    assert isinstance(report["packages"], dict)
    assert set(report["packages"]) == {"torch", "torchaudio", "whisperx"}


def test_missing_stack_message_is_actionable():
    message = build_error_message(
        "No module named whisperx",
        "No module named torch",
        base_dir=".",
    )
    assert "OUSSAMA Cutter" in message
    assert "setup_on_d.ps1 -Mode Full -Transcription cpu" in message
    assert "--repair cpu" in message
    assert "لا تستخدم الترجمة الوهمية" in message


def test_dependency_error_is_marked_for_no_retry():
    error = TranscriptionUnavailableError("missing stack")
    assert error.dependency_error is True


def test_huggingface_conflict_message_is_actionable():
    message = build_error_message(
        "huggingface-hub>=0.34.0,<1.0 is required but found 1.27.0",
        base_dir=".",
    )
    assert "تعارض بين Transformers وhuggingface-hub" in message
    assert "huggingface-hub>=0.34.0,<1.0" in message
