"""Tests for the system check script (scripts/doctor.py)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import doctor


def test_run_checks_structure():
    checks = doctor.run_checks()
    assert len(checks) > 5
    for c in checks:
        assert set(c.keys()) == {"name", "status", "detail"}
        assert c["status"] in (doctor.OK, doctor.WARN, doctor.FAIL)


def test_check_python_passes_in_test_env():
    result = doctor.check_python()
    assert result["status"] == doctor.OK


def test_check_binary_found(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/usr/bin/{name}")
    result = doctor.check_binary("ffmpeg")
    assert result["status"] == doctor.OK
    assert "ffmpeg" in result["detail"]


def test_check_binary_missing_critical(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    result = doctor.check_binary("ffmpeg", critical=True)
    assert result["status"] == doctor.FAIL


def test_check_binary_missing_optional(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    result = doctor.check_binary("someoptional", critical=False)
    assert result["status"] == doctor.WARN


def test_check_dependency_missing():
    result = doctor.check_dependency("module_that_does_not_exist_xyz", critical=False)
    assert result["status"] == doctor.WARN
    result = doctor.check_dependency("module_that_does_not_exist_xyz", critical=True)
    assert result["status"] == doctor.FAIL


def test_check_dependency_present():
    result = doctor.check_dependency("json", critical=True)
    assert result["status"] == doctor.OK


def test_check_writable(tmp_path):
    result = doctor.check_writable(str(tmp_path))
    assert result["status"] == doctor.OK


def test_main_reports_and_exit_code(capsys):
    # In the sandbox some optional deps are missing but criticals (python,
    # ffmpeg) may vary — assert the report shape, not the final verdict.
    code = doctor.main()
    out = capsys.readouterr().out
    assert "OUSSAMA Cutter Doctor" in out
    assert code in (0, 1)
    if code == 0:
        assert "System ready" in out
    else:
        assert "critical" in out
