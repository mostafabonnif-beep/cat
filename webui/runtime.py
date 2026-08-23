# -*- coding: utf-8 -*-
"""Runtime helpers for the packaged (PyInstaller) exe vs source runs.

The frozen exe has no `main_improved.py` / `scripts/...` on disk — everything
lives inside the bundle. Any code that spawned `python main_improved.py` as a
subprocess must instead re-invoke the exe itself (sys.executable), and paths
like the project folder (VIRALS) must point next to the exe, not into the
temporary extraction dir.
"""

import os
import sys


def is_frozen():
    """True when running inside the packaged ViralCutter.exe."""
    return bool(getattr(sys, "frozen", False))


def app_dir():
    """Folder the app treats as home: exe folder when frozen, repo root otherwise."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def python_cmd(script_path=None):
    """Base command that runs a ViralCutter Python script.

    Source run:  [sys.executable, <script_path>]
    Frozen run:  [sys.executable]  — the exe re-invokes itself; the CLI args
                 follow directly (the script path does not exist on disk).
    """
    if is_frozen():
        return [sys.executable]
    cmd = [sys.executable]
    if script_path:
        cmd.append(script_path)
    return cmd
