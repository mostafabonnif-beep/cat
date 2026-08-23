"""Tests for saving viral segments to the project folder."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.save_json import save_viral_segments


class _NonTtyStdin:
    def isatty(self):
        return False

    def read(self, *a, **k):
        raise OSError("stdin not readable in tests")


def test_save_viral_segments_writes_file(tmp_path, capsys):
    project = str(tmp_path)
    data = {"segments": [{"start": 0, "end": 5, "text": "hi"}]}

    save_viral_segments(segments_data=data, project_folder=project)

    out = os.path.join(project, "viral_segments.txt")
    assert os.path.exists(out)
    saved = json.loads(open(out, encoding="utf-8").read())
    assert saved == data


def test_save_viral_segments_does_not_overwrite(tmp_path):
    project = str(tmp_path)
    out = os.path.join(project, "viral_segments.txt")
    os.makedirs(project, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("original")

    save_viral_segments(segments_data={"segments": []}, project_folder=project)

    with open(out, encoding="utf-8") as f:
        assert f.read() == "original"


def test_save_viral_segments_never_blocks_automation(tmp_path, monkeypatch, capsys):
    """Without data and without an interactive terminal it must skip, not hang."""
    monkeypatch.setattr(sys, "stdin", _NonTtyStdin())

    save_viral_segments(segments_data=None, project_folder=str(tmp_path))

    out = os.path.join(str(tmp_path), "viral_segments.txt")
    assert not os.path.exists(out)
    assert capsys.readouterr().out.strip() != ""
