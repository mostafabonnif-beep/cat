# -*- coding: utf-8 -*-
"""Version-consistency regression tests.

The v7.32 release shipped code+changelog at 7.32 while ``app_version.py``
(the single source of truth shown in the WebUI) still said 7.30 and
``pyproject.toml`` said 7.26. These tests pin the three canonical version
declarations together so a release can never desync them again.
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
