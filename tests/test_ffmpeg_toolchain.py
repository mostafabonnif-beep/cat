# -*- coding: utf-8 -*-
"""Regression tests for the verified ffmpeg toolchain resolution (v7.32.2).

Root bug: a broken ffmpeg.exe on PATH (lone copy in C:\\Windows\\system32
without its DLLs) passed the existence-only preflight, while yt-dlp — which
EXECUTES the binary before merging — failed every download with
"You have requested merging of multiple formats but ffmpeg is not installed".
"""
import os

from scripts import download_video, preflight
from scripts import ffmpeg_toolchain as ft
from scripts.ffmpeg_toolchain import FFMPEG_BROKEN_GUIDANCE, resolve_toolchain


def _exe(name):
    return name + (".exe" if os.name == "nt" else "")


def _which_factory(mapping):
    def which(name):
        return mapping.get(name)
    return which


def _runs_factory(working):
    def runs(path, *a, **k):
        return path in working
    return runs


def test_path_ffmpeg_works_means_no_override():
    ff_dir = r"C:\ffmpeg\bin"
    good_ff = os.path.join(ff_dir, _exe("ffmpeg"))
    good_fp = os.path.join(ff_dir, _exe("ffprobe"))
    which = _which_factory({"ffmpeg": good_ff, "ffprobe": good_fp})
    runs = _runs_factory({good_ff, good_fp})
    tc = resolve_toolchain(which_fn=which, runs=runs, env={})
    assert tc["ok"] is True
    assert tc["location"] is None
    assert tc["problems"] == []
    assert tc["programs"]["ffmpeg"]["state"] == "ok"


def test_broken_path_ffmpeg_is_rescued_from_ffprobe_dir():
    """The reported user case: broken system32 ffmpeg shadows a good install."""
    broken = os.path.join(r"C:\Windows\system32", _exe("ffmpeg"))
    ff_dir = r"C:\ffmpeg\bin"
    good_ff = os.path.join(ff_dir, _exe("ffmpeg"))
    good_fp = os.path.join(ff_dir, _exe("ffprobe"))
    which = _which_factory({"ffmpeg": broken, "ffprobe": good_fp})
    runs = _runs_factory({good_ff, good_fp})
    tc = resolve_toolchain(which_fn=which, isfile=lambda p: p in {good_ff, good_fp},
                           runs=runs, env={})
    assert tc["ok"] is True
    assert tc["programs"]["ffmpeg"]["state"] == "rescued"
    assert tc["programs"]["ffmpeg"]["path"] == good_ff
    assert tc["location"] == ff_dir


def test_env_dir_rescue_when_ffprobe_also_broken():
    broken_ff = os.path.join(r"C:\Windows\system32", _exe("ffmpeg"))
    broken_fp = os.path.join(r"C:\Windows\system32", _exe("ffprobe"))
    good_dir = r"D:\tools\ffmpeg\bin"
    good_ff = os.path.join(good_dir, _exe("ffmpeg"))
    good_fp = os.path.join(good_dir, _exe("ffprobe"))
    which = _which_factory({"ffmpeg": broken_ff, "ffprobe": broken_fp})
    runs = _runs_factory({good_ff, good_fp})
    tc = resolve_toolchain(which_fn=which, isfile=lambda p: True, runs=runs,
                           env={"VIRALCUTTER_FFMPEG_DIR": good_dir})
    assert tc["ok"] is True
    assert tc["location"] == good_dir


def test_totally_broken_toolchain_reports_problems():
    broken = os.path.join(r"C:\Windows\system32", _exe("ffmpeg"))
    which = _which_factory({"ffmpeg": broken, "ffprobe": None})
    tc = resolve_toolchain(which_fn=which, isfile=lambda p: False,
                           runs=_runs_factory(set()), env={})
    assert tc["ok"] is False
    assert any("does not run" in p for p in tc["problems"])
    assert any("ffprobe not found" in p for p in tc["problems"])


def test_guidance_mentions_real_fixes():
    assert "winget install ffmpeg" in FFMPEG_BROKEN_GUIDANCE
    assert "VIRALCUTTER_FFMPEG_DIR" in FFMPEG_BROKEN_GUIDANCE
    assert "system32" in FFMPEG_BROKEN_GUIDANCE


def test_friendly_error_maps_ytdlp_ffmpeg_message():
    msg = download_video._friendly_download_error(
        Exception("You have requested merging of multiple formats but ffmpeg is not installed"))
    assert "ffmpeg" in msg
    assert "winget install ffmpeg" in msg


def test_preflight_flags_broken_binary_as_warn_with_rescue(monkeypatch):
    ff_dir = r"C:\ffmpeg\bin"
    good_ff = os.path.join(ff_dir, _exe("ffmpeg"))
    good_fp = os.path.join(ff_dir, _exe("ffprobe"))
    monkeypatch.setattr(ft.shutil, "which",
                        _which_factory({"ffmpeg": os.path.join(r"C:\Windows\system32", _exe("ffmpeg")),
                                        "ffprobe": good_fp}))
    monkeypatch.setattr(ft, "binary_runs", _runs_factory({good_ff, good_fp}))
    monkeypatch.setattr(ft.os.path, "isfile", lambda p: True)
    entries = {e["name"]: e for e in preflight.check_ffmpeg_toolchain()}
    # ffmpeg PATH copy is broken but a verified rescue exists -> WARN, not FAIL
    assert entries["ffmpeg"]["status"] == preflight.WARN
    assert good_ff in entries["ffmpeg"]["detail"]
    assert entries["ffprobe"]["status"] == preflight.OK


def test_preflight_fails_when_nothing_runs(monkeypatch):
    monkeypatch.setattr(ft.shutil, "which",
                        _which_factory({"ffmpeg": os.path.join(r"C:\Windows\system32", _exe("ffmpeg")),
                                        "ffprobe": None}))
    monkeypatch.setattr(ft, "binary_runs", lambda *a, **k: False)
    monkeypatch.setattr(ft.os.path, "isfile", lambda p: False)
    entries = {e["name"]: e for e in preflight.check_ffmpeg_toolchain()}
    assert entries["ffmpeg"]["status"] == preflight.FAIL
    assert "does not run" in entries["ffmpeg"]["detail"]
    assert entries["ffprobe"]["status"] == preflight.FAIL
