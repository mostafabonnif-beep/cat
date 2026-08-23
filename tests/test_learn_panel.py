"""Tests for the WebUI Learn & Performance panels (webui/learn_panel.py)."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "webui"))

import learn_panel


@pytest.fixture(autouse=True)
def isolate_repo_root(tmp_path, monkeypatch):
    """Point strike_feedback's file locations at a temp dir (no repo writes)."""
    import scripts.strike_feedback as sf
    monkeypatch.setattr(sf, "APP_ROOT", str(tmp_path))
    yield tmp_path


def test_list_terms_empty():
    out = learn_panel.list_terms()
    assert "0" in out


def test_add_term_and_list(isolate_repo_root):
    out = learn_panel.add_term("كلمة ممنوعة", "high", "test")
    assert "✅" in out
    listed = learn_panel.list_terms()
    assert "كلمة ممنوعة" in listed


def test_add_empty_term():
    assert "❌" in learn_panel.add_term("  ", "high", "")


def test_allow_term(isolate_repo_root):
    assert "✅" in learn_panel.allow_term("منغولي", "history channel")
    listed = learn_panel.list_terms()
    assert "منغولي" in listed


def test_remove_term(isolate_repo_root):
    learn_panel.add_term("temp-word", "low", "")
    assert "✅" in learn_panel.remove_term("temp-word")


def test_show_stats(isolate_repo_root):
    learn_panel.add_term("a", "high", "")
    out = learn_panel.show_stats()
    assert "events:" in out and "1" in out


def test_extract_from_project_no_project():
    assert "❌" in learn_panel.extract_from_project("")


def test_extract_from_project_missing_folder():
    assert "❌" in learn_panel.extract_from_project("nope_project",
                                                    virals_dir="/tmp/does_not_exist_xyz")


def test_extract_from_project_with_reports(isolate_repo_root, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    with open(project / "safety_report.json", "w", encoding="utf-8") as fh:
        json.dump({"segments": [
            {"title": "s", "status": "blocked",
             "matches": [{"term": "badword", "severity": "high"}]}]}, fh)
    virals = tmp_path / "VIRALS"
    virals.mkdir()
    (virals / "proj").mkdir()
    (virals / "proj" / "safety_report.json").write_text(
        (project / "safety_report.json").read_text())

    out = learn_panel.extract_from_project("proj", apply=False, virals_dir=str(virals))
    assert "badword" in out
    assert "Apply" in out

    out2 = learn_panel.extract_from_project("proj", apply=True, virals_dir=str(virals))
    assert "✅ Learned" in out2
    assert "badword" in learn_panel.list_terms()


def test_run_analytics_not_configured(monkeypatch):
    import scripts.analytics as analytics_mod

    def boom():
        raise RuntimeError("no credentials")
    monkeypatch.setattr(analytics_mod, "_build_services", boom)
    out = learn_panel.run_analytics("summary", 28)
    assert "❌" in out and "client_secrets" in out


def test_local_publish_summary(isolate_repo_root, tmp_path):
    virals = tmp_path / "VIRALS"
    project = virals / "project-a"
    project.mkdir(parents=True)
    history = [
        {"timestamp": "2099-01-02T12:00:00Z", "platform": "youtube", "status": "uploaded", "privacy_status": "private", "title": "أفضل مقطع"},
        {"timestamp": "2099-01-02T12:01:00Z", "platform": "youtube", "status": "failed", "privacy_status": "private", "title": "فشل"},
        {"timestamp": "2099-01-02T12:02:00Z", "platform": "youtube", "status": "dry-run", "privacy_status": "public", "title": "محاكاة"},
    ]
    (project / "publish_history.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in history) + "\n",
        encoding="utf-8",
    )

    out = learn_panel.local_publish_summary(days=30, virals_dir=str(virals))
    assert "الأحداث: 3" in out
    assert "الناجحة/المجدولة: 1" in out
    assert "الفاشلة: 1" in out
    assert "المحاكاة: 1" in out
    assert "أفضل مقطع" in out
