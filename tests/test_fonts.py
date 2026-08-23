# -*- coding: utf-8 -*-
"""v6.9.1: bundled Montserrat fonts must be wired into the burn filter.

Before: ffmpeg's subtitles filter resolved the Hormozi-style font via the
system fontconfig — when Montserrat wasn't installed, videos silently used
a substituted font. Now the repo ships fonts/ and burn_subtitles points
fontconfig at it via `:fontsdir=`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import burn_subtitles


def test_fonts_directory_ships_montserrat():
    fonts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")
    assert os.path.isdir(fonts_dir)
    names = set(os.listdir(fonts_dir))
    for expected in ("Montserrat-Regular.ttf", "Montserrat-Bold.ttf",
                     "Montserrat-ExtraBold.ttf"):
        assert expected in names, expected
    assert "OFL.txt" in names  # license must ship with the fonts


def test_fonts_dir_returns_real_path():
    fd = burn_subtitles._fonts_dir()
    assert fd is not None
    assert os.path.basename(fd) == "fonts"
    assert os.path.isdir(fd)


def test_subtitles_filter_includes_fontsdir():
    vf = burn_subtitles._subtitles_filter("/some/proj/subs/x.ass")
    assert vf.startswith("subtitles='")
    assert "fontsdir=" in vf
    assert "fonts" in vf


def test_subtitles_filter_escapes_windows_paths():
    vf = burn_subtitles._subtitles_filter("C:\\proj\\subs\\x.ass")
    # drive-letter colon must be escaped for ffmpeg's filter parser
    assert vf.startswith("subtitles='C\\:/proj/subs/x.ass'")
    assert "fontsdir=" in vf
