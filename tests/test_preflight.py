"""Tests for the pre-flight check & auto-repair module (scripts/preflight.py)."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import preflight


# --------------------------------------------------------------------------
# Unit checks
# --------------------------------------------------------------------------
def test_collect_checks_structure():
    checks, critical, warn = preflight.collect_checks()
    assert len(checks) > 5
    for c in checks:
        assert set(c.keys()) == {"name", "status", "detail"}
        assert c["status"] in (preflight.OK, preflight.WARN, preflight.FAIL)
    assert isinstance(critical, list)
    assert isinstance(warn, list)


def test_check_python_passes():
    assert preflight.check_python()["status"] in (preflight.OK, preflight.WARN)


def test_check_binary_found(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: f"/usr/bin/{name}")
    r = preflight.check_binary("ffmpeg")
    assert r["status"] == preflight.OK
    assert "ffmpeg" in r["detail"]


def test_check_binary_missing_critical(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    assert preflight.check_binary("ffmpeg", critical=True)["status"] == preflight.FAIL
    assert preflight.check_binary("fpcalc", critical=False)["status"] == preflight.WARN


def test_check_bundled_binary_skipped_when_not_frozen():
    assert preflight.check_bundled_binary("ffmpeg") is None


def test_check_bundled_binary_found_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight.sys, "frozen", True, raising=False)
    fake = tmp_path / "ffmpeg"
    fake.write_text("x")
    monkeypatch.setattr(preflight.sys, "_MEIPASS", str(tmp_path), raising=False)
    r = preflight.check_bundled_binary("ffmpeg")
    assert r is not None
    assert r["status"] == preflight.OK


def test_check_dependency_present():
    assert preflight.check_dependency("json", "json", True)["status"] == preflight.OK


def test_check_dependency_missing(monkeypatch):
    monkeypatch.setattr(preflight, "_module_importable", lambda m: False)
    monkeypatch.setattr(preflight, "_dist_installed", lambda d: False)
    assert preflight.check_dependency("no_such_pkg_xyz", "no_such_pkg_xyz", True)["status"] == preflight.FAIL
    assert preflight.check_dependency("no_such_pkg_xyz", "no_such_pkg_xyz", False)["status"] == preflight.WARN


def test_check_api_config_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "_app_dir", lambda: str(tmp_path))
    r = preflight.check_api_config()
    assert r["status"] == preflight.FAIL
    assert "missing" in r["detail"]


def test_check_api_config_invalid(tmp_path):
    p = tmp_path / "api_config.json"
    p.write_text("{not json")
    monkeypatch_dir_holder = tmp_path
    old = preflight._app_dir
    preflight._app_dir = lambda: str(monkeypatch_dir_holder)
    try:
        r = preflight.check_api_config()
        assert r["status"] == preflight.FAIL
        assert "invalid" in r["detail"]
    finally:
        preflight._app_dir = old


def test_check_api_config_empty_key(tmp_path):
    p = tmp_path / "api_config.json"
    p.write_text(json.dumps({"selected_api": "gemini", "gemini": {"api_key": "", "model": "m"}}))
    old = preflight._app_dir
    preflight._app_dir = lambda: str(tmp_path)
    try:
        r = preflight.check_api_config()
        assert r["status"] == preflight.WARN
        assert "empty" in r["detail"]
    finally:
        preflight._app_dir = old


def test_check_assets_ok_and_missing(tmp_path):
    # ok: use the real repo
    assert preflight.check_assets()["status"] == preflight.OK
    # missing: point APP_ROOT at an empty dir
    old = preflight.APP_ROOT
    preflight.APP_ROOT = str(tmp_path)
    try:
        r = preflight.check_assets()
        assert r["status"] == preflight.FAIL
    finally:
        preflight.APP_ROOT = old


def test_check_models_dir(tmp_path):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "llama.gguf").write_text("x")
    old = preflight.APP_ROOT
    preflight.APP_ROOT = str(tmp_path)
    try:
        assert preflight.check_models_dir()["status"] == preflight.OK
    finally:
        preflight.APP_ROOT = old


def test_check_pin_numpy_ok(monkeypatch):
    # Deterministic: with an installed numpy that satisfies the pin, the
    # check must pass regardless of the ambient environment (the old test
    # depended on the dev box having numpy<2, flaking on numpy 2.x boxes).
    monkeypatch.setattr(preflight._pkg_meta, "version", lambda d: "1.26.4")
    assert preflight.check_pin("numpy", "numpy<2") is None


def test_check_pin_numpy_violation(monkeypatch):
    monkeypatch.setattr(preflight._pkg_meta, "version", lambda d: "2.1.0")
    r = preflight.check_pin("numpy", "numpy<2")
    assert r is not None and r["status"] == preflight.FAIL


# --------------------------------------------------------------------------
# Repair actions
# --------------------------------------------------------------------------
def test_ensure_dirs_creates(tmp_path):
    old = preflight._app_dir
    preflight._app_dir = lambda: str(tmp_path)
    try:
        results = preflight.ensure_dirs()
        assert all(ok for _, ok, _ in results)
        assert os.path.isdir(tmp_path / "models")
        assert os.path.isdir(tmp_path / "VIRALS")
    finally:
        preflight._app_dir = old


def test_ensure_api_config_creates_template(tmp_path):
    old = preflight._app_dir
    preflight._app_dir = lambda: str(tmp_path)
    try:
        ok, detail = preflight.ensure_api_config()
        assert ok
        p = tmp_path / "api_config.json"
        assert p.exists()
        data = json.loads(p.read_text())
        assert data["selected_api"] == "gemini"
        assert "api_key" in data["gemini"]
    finally:
        preflight._app_dir = old


def test_ensure_api_config_preserves_existing(tmp_path):
    p = tmp_path / "api_config.json"
    p.write_text(json.dumps({"selected_api": "gemini", "gemini": {"api_key": "AIzaSECRET", "model": "m"}}))
    old = preflight._app_dir
    preflight._app_dir = lambda: str(tmp_path)
    try:
        ok, _ = preflight.ensure_api_config()
        assert ok
        data = json.loads(p.read_text())
        assert data["gemini"]["api_key"] == "AIzaSECRET"  # never overwrite a real key
    finally:
        preflight._app_dir = old


def test_install_requirements_dry_run():
    ok, detail = preflight.install_requirements("requirements-dev.txt", dry_run=True)
    assert not ok
    assert "would run" in detail


def test_install_requirements_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "APP_ROOT", str(tmp_path))
    ok, detail = preflight.install_requirements("does_not_exist.txt")
    assert ok
    assert "empty" in detail


def test_fix_numpy_pin_skipped_when_missing(monkeypatch):
    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: None)
    ok, detail = preflight.fix_numpy_pin()
    assert ok
    assert "not installed" in detail


def test_fix_numpy_pin_dry_run(monkeypatch):
    class FakeSpec:
        pass

    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: FakeSpec())
    monkeypatch.setattr(preflight.sys, "path", [])
    # force version read as 2.x
    import types

    fake_numpy = types.ModuleType("numpy")
    fake_numpy.__version__ = "2.1.0"
    monkeypatch.setitem(sys.modules, "numpy", fake_numpy)
    ok, detail = preflight.fix_numpy_pin(dry_run=True)
    assert not ok
    assert "would downgrade" in detail


# --------------------------------------------------------------------------
# Orchestration exit codes
# --------------------------------------------------------------------------
def test_run_preflight_ready(monkeypatch):
    fake = [
        {"name": "Python", "status": preflight.OK, "detail": "3.11"},
        {"name": "ffmpeg", "status": preflight.OK, "detail": "/usr/bin/ffmpeg"},
        {"name": "ffprobe", "status": preflight.OK, "detail": "/usr/bin/ffprobe"},
    ]
    monkeypatch.setattr(preflight, "collect_checks", lambda: (fake, [], []))
    assert preflight.run_preflight(mode="check", quiet=True) == 0


def test_run_preflight_warnings(monkeypatch):
    fake = [
        {"name": "Python", "status": preflight.OK, "detail": "3.11"},
        {"name": "ffmpeg", "status": preflight.WARN, "detail": "x"},
    ]
    monkeypatch.setattr(preflight, "collect_checks", lambda: (fake, [], ["ffmpeg"]))
    assert preflight.run_preflight(mode="check", quiet=True) == 2


def test_run_preflight_critical_blocks(monkeypatch):
    fake = [
        {"name": "Python", "status": preflight.OK, "detail": "3.11"},
        {"name": "ffmpeg", "status": preflight.FAIL, "detail": "missing"},
    ]
    monkeypatch.setattr(preflight, "collect_checks", lambda: (fake, ["ffmpeg"], []))
    assert preflight.run_preflight(mode="check", quiet=True) == 1


def test_run_preflight_autofix_installs_core(monkeypatch, tmp_path):
    """A missing core dep + auto-fix must trigger a pip install and end ready."""
    calls = []

    def fake_install(req_file, dry_run=False):
        calls.append(req_file)
        return True, "installed"

    monkeypatch.setattr(preflight, "install_requirements", fake_install)
    monkeypatch.setattr(preflight, "ensure_dirs", lambda: [("x", True, "ok")])
    monkeypatch.setattr(preflight, "ensure_api_config", lambda: (True, "ok"))
    monkeypatch.setattr(preflight, "fix_numpy_pin", lambda: (True, "ok"))

    def fake_collect():
        if not calls:
            return ([{"name": "gradio", "status": preflight.FAIL, "detail": "missing"}],
                    ["gradio"], [])
        return ([{"name": "gradio", "status": preflight.OK, "detail": "6.0"}], [], [])

    monkeypatch.setattr(preflight, "collect_checks", fake_collect)
    assert preflight.run_preflight(mode="auto-fix", quiet=True) == 0
    assert calls == ["requirements.txt"]


def test_main_off_flag(capsys):
    assert preflight.main(["--off"]) == 0


def test_main_json_output(capsys):
    code = preflight.main(["--check", "--json", "--quiet"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "checks" in data
    assert data["exit"] in (0, 1, 2)
    assert code == data["exit"]


def test_run_pip_falls_back_to_uv_when_pip_missing(monkeypatch):
    calls = []
    monkeypatch.setattr(preflight, "_module_importable", lambda name: False)
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "uv.exe" if name == "uv" else None)

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Result()

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    result = preflight._run_pip(["--no-cache-dir", "numpy==1.26.4"])
    assert result.returncode == 0
    assert calls[0][:4] == ["uv.exe", "pip", "install", "--python"]
    assert "numpy==1.26.4" in calls[0]


def test_run_preflight_ensure_upload_installs_oauth(monkeypatch):
    calls = []

    def fake_install(req_file, dry_run=False):
        calls.append(req_file)
        return True, "installed upload"

    monkeypatch.setattr(preflight, "install_requirements", fake_install)
    monkeypatch.setattr(preflight, "ensure_dirs", lambda: [])
    monkeypatch.setattr(preflight, "ensure_api_config", lambda: (True, "ok"))
    monkeypatch.setattr(preflight, "fix_numpy_pin", lambda: (True, "ok"))

    def fake_collect():
        if not calls:
            return ([{"name": "google-auth-oauthlib", "status": preflight.FAIL, "detail": "missing"}],
                    ["google-auth-oauthlib"], [])
        return ([{"name": "google-auth-oauthlib", "status": preflight.OK, "detail": "installed"}], [], [])

    monkeypatch.setattr(preflight, "collect_checks", fake_collect)
    assert preflight.run_preflight(mode="auto-fix", quiet=True, ensure_upload=True) == 0
    assert calls == ["requirements-upload.txt"]


def test_run_preflight_ensure_upload_repairs_warning_only(monkeypatch):
    calls = []

    def fake_install(req_file, dry_run=False):
        calls.append(req_file)
        return True, "installed upload"

    monkeypatch.setattr(preflight, "install_requirements", fake_install)
    monkeypatch.setattr(preflight, "ensure_dirs", lambda: [])
    monkeypatch.setattr(preflight, "ensure_api_config", lambda: (True, "ok"))
    monkeypatch.setattr(preflight, "fix_numpy_pin", lambda: (True, "ok"))

    def fake_collect():
        if not calls:
            return ([{"name": "google-auth-oauthlib", "status": preflight.WARN, "detail": "optional - missing"}],
                    [], ["google-auth-oauthlib"])
        return ([{"name": "google-auth-oauthlib", "status": preflight.OK, "detail": "installed"}], [], [])

    monkeypatch.setattr(preflight, "collect_checks", fake_collect)
    assert preflight.run_preflight(mode="auto-fix", quiet=True, ensure_upload=True) == 0
    assert calls == ["requirements-upload.txt"]
