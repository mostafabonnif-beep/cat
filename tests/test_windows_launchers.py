import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read_ascii(name):
    path = ROOT / name
    data = path.read_bytes()
    # Batch files are intentionally ASCII: this avoids Windows cmd parsing
    # corruption on systems using a legacy OEM code page.
    data.decode("ascii")
    return data.decode("ascii").replace("\r\n", "\n")


def _labels(text):
    return {match.group(1).strip().upper() for match in re.finditer(r"^:([A-Za-z0-9_]+)\s*$", text, re.MULTILINE)}


def test_windows_launchers_are_ascii_and_have_valid_gotos():
    for name in ("install_dependencies.bat", "run_webui.bat", "packaging/install_ffmpeg_windows.bat"):
        text = _read_ascii(name)
        labels = _labels(text)
        for target in re.findall(r"\bgoto\s+([A-Za-z0-9_]+)", text, flags=re.IGNORECASE):
            assert target.upper() in labels, f"{name}: missing goto label {target}"


def test_install_launcher_uses_project_drive_for_temp_and_runs_diagnostics():
    text = _read_ascii("install_dependencies.bat")
    assert 'set "TEMP=%CD%\\.installer-tmp"' in text
    assert 'set "TMP=%CD%\\.installer-tmp"' in text
    assert "scripts.windows_diagnostics" in text
    assert "requirements-upload.txt" in text
    assert "google_auth_oauthlib" in text


def test_webui_launcher_is_non_destructive_and_has_warning_path():
    text = _read_ascii("run_webui.bat")
    assert "setlocal EnableExtensions" in text
    assert "cd /d \"%~dp0\"" in text
    assert "scripts.preflight --auto-fix --ensure-upload" in text
    assert 'if "%PREFLIGHT_EXIT%"=="2" goto START_WEBUI_WARNINGS' in text
    assert 'if exist "bin\\ffmpeg.exe" set "PATH=%CD%\\bin;%PATH%"' in text


def test_powershell_wrappers_keep_temp_on_project_drive_and_forward_args():
    for name in ("setup_on_d.ps1", "run_webui_on_d.ps1"):
        text = (ROOT / name).read_text(encoding="ascii")
        assert "$env:TEMP = $TempRoot" in text
        assert "$env:TMP = $TempRoot" in text
        assert "$env:UV_NO_CACHE = \"1\"" in text or name == "setup_on_d.ps1"
    text = (ROOT / "run_webui_on_d.ps1").read_text(encoding="ascii")
    assert "ValueFromRemainingArguments" in text
    assert "& $Launcher @Arguments" in text
