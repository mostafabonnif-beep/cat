from types import SimpleNamespace

from scripts import autopilot


def test_readiness_fails_closed_when_strict_dependencies_are_missing(monkeypatch):
    monkeypatch.setattr(autopilot, "_binary", lambda name: "/usr/bin/" + name if name in {"ffmpeg", "ffprobe"} else None)
    args = SimpleNamespace(ocr_check="on", visual_check="on", visual_model=None)
    report = autopilot.check_readiness(args, "manual")
    codes = {item["code"] for item in report["issues"]}
    assert not report["ok"]
    assert {"missing_tesseract", "missing_visual_model", "missing_ai_backend"} <= codes


def test_readiness_accepts_complete_ai_setup(monkeypatch, tmp_path):
    model = tmp_path / "visual.onnx"
    model.write_bytes(b"model")
    monkeypatch.setattr(autopilot, "_binary", lambda _name: "/usr/bin/tool")
    monkeypatch.setattr(autopilot, "_tesseract_languages", lambda _binary: {"ara", "eng"})
    args = SimpleNamespace(ocr_check="on", visual_check="on", visual_model=str(model))
    report = autopilot.check_readiness(args, "gemini", "key")
    assert report["ok"] is True


def test_readiness_report_is_written_atomically(tmp_path):
    path = autopilot.write_report(str(tmp_path), {"ok": True})
    assert path.endswith("autopilot_readiness.json")
    assert '"ok": true' in open(path, encoding="utf-8").read()
