"""Helpers for safe, testable Gradio file-picker values.

Gradio may return a string path, a file-like mapping, or a list of either
(depending on ``type`` and ``file_count``). Keeping this normalization outside
app.py makes the UI handlers small and unit-testable.
"""

import os
from os.path import commonpath, dirname


def _raw_path(value):
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("path") or value.get("name") or "").strip()
    return str(getattr(value, "name", value) or "").strip()


def paths(value):
    """Return existing-looking path strings from a Gradio file value."""
    values = value if isinstance(value, (list, tuple)) else [value]
    result = []
    for item in values:
        path = os.path.abspath(os.path.expanduser(_raw_path(item))) if _raw_path(item) else ""
        if path and os.path.isfile(path):
            result.append(path)
    return result


def first_path(value, current=""):
    """Use the selected first file, or preserve the current text field."""
    selected = paths(value)
    return selected[0] if selected else str(current or "")


def common_parent(value, current=""):
    """Return the common parent directory of selected files.

    This is useful for the SFX picker because the editing engine accepts a
    folder. If the browser returns files from different directories, retaining
    the current manual value is safer than silently choosing a wrong folder.
    """
    selected = paths(value)
    if not selected:
        return str(current or "")
    parents = [dirname(path) for path in selected]
    try:
        parent = commonpath(parents)
    except ValueError:
        return str(current or "")
    return parent if os.path.isdir(parent) else str(current or "")
