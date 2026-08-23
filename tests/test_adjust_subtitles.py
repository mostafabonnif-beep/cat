"""Tests for subtitle helpers (RTL detection + ASS time formatting)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.adjust_subtitles import format_time_ass, is_rtl_language, rtl_ass_prefix


@pytest.mark.parametrize(
    "lang,expected",
    [
        ("ar", True),
        ("ar-SA", True),
        ("ar_SA", True),
        ("he", True),
        ("fa", True),
        ("ur", True),
        ("en", False),
        ("en-US", False),
        ("pt_BR", False),
        ("", False),
        (None, False),
    ],
)
def test_is_rtl_language(lang, expected):
    assert is_rtl_language(lang) is expected


def test_rtl_ass_prefix_rtl():
    assert rtl_ass_prefix("ar") == "{\\rtl}"


def test_rtl_ass_prefix_ltr():
    assert rtl_ass_prefix("en") == ""


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "0:00:00.00"),
        (59.999, "0:01:00.00"),
        (65.5, "0:01:05.50"),
        (3661.25, "1:01:01.25"),
        (3599.99, "0:59:59.99"),
    ],
)
def test_format_time_ass(seconds, expected):
    assert format_time_ass(seconds) == expected
