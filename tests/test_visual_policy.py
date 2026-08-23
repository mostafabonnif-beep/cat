"""Regression tests for the visual safety policy integration."""

import json


def test_visual_required_without_model_fails_closed(tmp_path):
    from scripts import risk_scorecard

    report = risk_scorecard.analyze_project(
        str(tmp_path),
        viral_segments={
            "segments": [
                {"title": "sample", "start_time": 0, "end_time": 5},
            ]
        },
        visual_check="on",
        visual_gate="block",
    )

    assert report["summary"]["visual_unavailable"] is True
    assert report["summary"]["visual_gate_failed"] is True
    assert report["summary"]["visual_available"] is False


def test_upload_gate_blocks_when_visual_scan_required_but_unavailable(tmp_path):
    from scripts import upload_gate

    (tmp_path / "risk_scorecard.json").write_text(
        json.dumps({"summary": {"visual_gate_failed": True}}),
        encoding="utf-8",
    )
    verdict = upload_gate.check_clip(str(tmp_path), index=0)
    assert verdict["allowed"] is False
    assert any(r["source"] == "visual_safety" for r in verdict["reasons"])


def test_pipeline_passes_visual_policy_flags():
    from webui.pipeline import build_command

    cmd = build_command(
        "main.py",
        ["--url", "https://example.test/video"],
        visual_check="on",
        visual_gate="block",
        visual_frames=8,
        visual_model="models/custom.onnx",
        auto_download_visual=True,
    )
    assert "--visual-check" in cmd and "on" in cmd
    assert "--visual-gate" in cmd and "block" in cmd
    assert "--visual-frames" in cmd and "8" in cmd
    assert "--visual-model" in cmd and "models/custom.onnx" in cmd
    assert "--auto-download-visual" in cmd
