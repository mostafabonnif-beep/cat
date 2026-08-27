import json
import subprocess

from scripts import audio_qc


def _completed(stderr="", returncode=0, stdout=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def test_analyze_file_passes_with_measured_audio(tmp_path, monkeypatch):
    video = tmp_path / "000_clip.mp4"
    video.write_bytes(b"test")
    loudnorm = '{"input_i":"-16.2","input_tp":"-2.1","input_lra":"5.0","input_thresh":"-26.0","target_offset":"0.2"}'

    def fake_run(command, timeout=300):
        if command[0] == "ffprobe":
            return _completed(stdout=json.dumps({
                "streams": [{"codec_type": "video"}, {"codec_type": "audio", "sample_rate": "48000", "channels": 2}],
                "format": {"duration": "30.0"},
            }))
        if "loudnorm=" in " ".join(command):
            return _completed(stderr=loudnorm)
        return _completed(stderr="silence_start: 0\nsilence_end: 0.2\n")

    monkeypatch.setattr(audio_qc, "_run", fake_run)
    report = audio_qc.analyze_file(str(video))

    assert report["status"] == "pass"
    assert report["ok"] is True
    assert report["metrics"]["input_i"] == -16.2
    assert report["metrics"]["silence"]["duration"] == 0.2


def test_analyze_file_blocks_missing_audio(tmp_path, monkeypatch):
    video = tmp_path / "silent-track.mp4"
    video.write_bytes(b"test")

    def fake_run(command, timeout=300):
        return _completed(stdout=json.dumps({
            "streams": [{"codec_type": "video"}],
            "format": {"duration": "10"},
        }))

    monkeypatch.setattr(audio_qc, "_run", fake_run)
    report = audio_qc.analyze_file(str(video))

    assert report["status"] == "block"
    assert report["issues"][0]["code"] == "missing_audio"


def test_analyze_file_marks_bad_loudness_for_review(tmp_path, monkeypatch):
    video = tmp_path / "loud.mp4"
    video.write_bytes(b"test")
    loudnorm = '{"input_i":"-5.0","input_tp":"-0.1","input_lra":"5.0"}'

    def fake_run(command, timeout=300):
        if command[0] == "ffprobe":
            return _completed(stdout=json.dumps({
                "streams": [{"codec_type": "audio"}],
                "format": {"duration": "20"},
            }))
        if "loudnorm=" in " ".join(command):
            return _completed(stderr=loudnorm)
        return _completed(stderr="")

    monkeypatch.setattr(audio_qc, "_run", fake_run)
    report = audio_qc.analyze_file(str(video))

    assert report["status"] == "review"
    codes = {item["code"] for item in report["issues"]}
    assert {"loudness_out_of_range", "true_peak_high"}.issubset(codes)


def test_analyze_project_writes_atomic_report(tmp_path, monkeypatch):
    folder = tmp_path / "project"
    output = folder / "final"
    output.mkdir(parents=True)
    video = output / "000_clip.mp4"
    video.write_bytes(b"test")
    loudnorm = '{"input_i":"-16.0","input_tp":"-2.0","input_lra":"5.0"}'

    def fake_run(command, timeout=300):
        if command[0] == "ffprobe":
            return _completed(stdout=json.dumps({
                "streams": [{"codec_type": "audio"}],
                "format": {"duration": "20"},
            }))
        if "loudnorm=" in " ".join(command):
            return _completed(stderr=loudnorm)
        return _completed(stderr="")

    monkeypatch.setattr(audio_qc, "_run", fake_run)
    report = audio_qc.analyze_project(str(folder))
    report_path = folder / audio_qc.REPORT_NAME

    assert report["status"] == "pass"
    assert report_path.is_file()
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["summary"]["total"] == 1
    assert audio_qc.report_for_file(str(folder), str(video))["status"] == "pass"


def test_gate_requires_report_and_blocks_review():
    allowed, reason = audio_qc.gate_allows(None)
    assert allowed is False
    assert "missing" in reason

    allowed, reason = audio_qc.gate_allows({"status": "review"})
    assert allowed is False
    assert "review" in reason


def test_webui_command_threads_audio_qc_options():
    from webui.pipeline import build_command

    command = build_command(
        "main_improved.py", ["--project-path", "project"], segments=2,
        audio_qc="off", audio_qc_gate="block",
    )
    assert command[command.index("--audio-qc") + 1] == "off"
    assert command[command.index("--audio-qc-gate") + 1] == "block"
