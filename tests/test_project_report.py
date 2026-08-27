import json

from scripts.project_report import build_report, render_html, write_report


def test_build_report_is_safe_for_legacy_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    report = build_report(str(project))
    assert report["project"]["status"] == "legacy"
    assert report["readiness"]["ready_for_publish"] is False
    assert report["media"]["count"] == 0
    assert "stages" in report


def test_safety_and_risk_are_reflected_in_report(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "checkpoint.json").write_text(
        json.dumps({"stages": {stage: True for stage in (
            "download", "transcribe", "segments", "safety", "cut",
            "edit", "polish", "subtitles", "scorecard", "done",
        )}}), encoding="utf-8")
    (project / "safety_report.json").write_text(json.dumps({
        "segments": [{
            "index": 0,
            "status": "manual_review",
            "semantic": {"action": "review"},
        }],
    }), encoding="utf-8")
    (project / "risk_scorecard.json").write_text(json.dumps({
        "summary": {"blocked_for_publish": 1},
        "blocked": [{"index": 0}],
    }), encoding="utf-8")
    report = build_report(str(project))
    assert report["safety"]["counts"]["manual_review"] == 1
    assert report["risk"]["blocked"] == 1
    assert report["readiness"]["ready_for_publish"] is False
    assert report["readiness"]["errors"]


def test_audio_qc_is_reflected_in_readiness_and_html(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "audio_qc_report.json").write_text(json.dumps({
        "status": "review",
        "summary": {"total": 2, "pass": 1, "review": 1, "block": 0},
    }), encoding="utf-8")
    report = build_report(str(project))
    assert report["audio_qc"]["status"] == "review"
    assert report["audio_qc"]["review"] == 1
    assert any("Audio QC" in error for error in report["readiness"]["errors"])
    html = render_html(report)
    assert "فحص جودة الصوت" in html
    assert "review" in html


def test_write_report_creates_json_and_html(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    write_report(str(project), html_report=True)
    assert (project / "project_report.json").exists()
    html = (project / "project_report.html").read_text(encoding="utf-8")
    assert "تقرير جاهزية مشروع" in html
    assert "project_report" not in html or "project_report.html" not in html


def test_html_escapes_project_path(tmp_path):
    report = build_report(str(tmp_path / "<unsafe>"))
    html = render_html(report)
    assert "&lt;unsafe&gt;" in html
