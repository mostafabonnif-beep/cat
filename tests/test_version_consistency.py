# -*- coding: utf-8 -*-
"""Version-consistency regression tests.

The v7.32 release shipped code+changelog at 7.32 while ``app_version.py``
(the single source of truth shown in the WebUI) still said 7.30 and
``pyproject.toml`` said 7.26. These tests pin the three canonical version
declarations together so a release can never desync them again.

v7.32.4: the same drift class was found in the three READMEs — their
"Current Version" lines still advertised 7.26.0 while the code was at
7.32.x. The README pin test below closes that hole.
"""
import re
from pathlib import Path

import app_version

ROOT = Path(__file__).resolve().parent.parent


def _core(version: str) -> str:
    """'7.32.0-pro' / '7.32.0+pro' -> '7.32.0'."""
    return re.split(r"[-+]", version, maxsplit=1)[0]


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    assert match, "pyproject.toml is missing a [project] version field"
    return match.group(1)


def _changelog_top_version() -> str:
    text = (ROOT / "changelog.md").read_text(encoding="utf-8")
    match = re.search(r"OUSSAMA Cutter (\d+\.\d+\.\d+)", text)
    assert match, "changelog.md has no 'OUSSAMA Cutter X.Y.Z' release entry"
    return match.group(1)


def test_app_version_matches_pyproject():
    assert _core(app_version.__version__) == _core(_pyproject_version())


def test_app_version_matches_changelog_top_entry():
    assert _core(app_version.__version__) == _changelog_top_version()


def test_version_and_alias_stay_in_sync():
    assert app_version.VERSION == app_version.__version__


# Each README advertises the running version in a human-language line.
# (marker text -> file). A stale line is how 7.26.0 survived six releases.
README_VERSION_MARKERS = {
    "README.md": "Versão Atual",
    "README_en.md": "Current Version",
    "README_ar.md": "الإصدار الحالي",
}


def test_readme_current_version_lines_match_app_version():
    core = _core(app_version.__version__)
    for filename, marker in README_VERSION_MARKERS.items():
        text = (ROOT / filename).read_text(encoding="utf-8")
        line = next((ln for ln in text.splitlines() if marker in ln), None)
        assert line, f"{filename} is missing its '{marker}' line"
        assert core in line, (
            f"{filename} advertises a stale version: {line.strip()!r} "
            f"(expected it to contain {core})"
        )
