# -*- coding: utf-8 -*-
"""v7.36.0 — voice-face link (v7.35 core) exposed from the WebUI.

The v7.35 speaker feature was CLI/env-only (--voice-face-link on /
VIRALCUTTER_VOICE_FACE_LINK=1). This round wires an opt-in checkbox through
webui/pipeline.build_command for every run path (start / review-render /
batch) while keeping the CLI default "off" — behavior unchanged unless the
user explicitly enables the toggle.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webui import header, pipeline  # noqa: E402


def _cmd(**kwargs):
    return pipeline.build_command("main.py", ["--url", "x"], **kwargs)


def test_voice_face_link_flag_emitted_when_enabled():
    cmd = _cmd(voice_face_link=True)
    assert "--voice-face-link" in cmd
    assert cmd[cmd.index("--voice-face-link") + 1] == "on"


def test_voice_face_link_omitted_by_default():
    cmd = _cmd()
    assert "--voice-face-link" not in cmd
    cmd = _cmd(voice_face_link=False)
    assert "--voice-face-link" not in cmd


def test_voice_face_link_independent_of_focus_active_speaker():
    # Focus on active speaker keeps its own flag; the voice link is additive.
    cmd = _cmd(voice_face_link=True, focus_active_speaker=False)
    assert "--voice-face-link" in cmd
    assert "--focus-active-speaker" not in cmd
    cmd = _cmd(voice_face_link=True, focus_active_speaker=True)
    assert "--focus-active-speaker" in cmd


def test_voice_face_link_does_not_leak_into_other_flags():
    cmd = _cmd(voice_face_link=True)
    # The flag pair is emitted exactly once — no stray duplicates.
    assert cmd.count("--voice-face-link") == 1
    assert cmd.count("--focus-active-speaker") == 0


def test_whats_new_card_mentions_voice_face_link():
    html = header.recent_updates_html(version="7.36.0")
    assert "7.36.0" in html
    assert "ربط الصوت بالوجه" in html
    assert "ما الجديد" in html
    assert "vc-updates" in html
