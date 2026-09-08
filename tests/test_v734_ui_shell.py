# -*- coding: utf-8 -*-
"""v7.34.0 — UI shell: header updates card + stylesheet presence."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webui import header, style


def test_recent_updates_html_contains_version_and_items():
    html = header.recent_updates_html(version="7.34.0")
    assert "7.34.0" in html
    assert "ما الجديد" in html
    # The card summarizes the real shipped capabilities.
    assert "بوابة جودة" in html or "جودة" in html
    assert "vc-updates" in html


def test_recent_updates_html_default_version():
    html = header.recent_updates_html()
    assert "?" in html


def test_stylesheet_covers_new_classes():
    css = style.CSS
    assert "tab-nav button" in css
    assert "vc-updates" in css
    assert "button.secondary" in css
    # RTL + dark theme anchors remain intact.
    assert "direction: rtl" in css
    assert "#0b0b0b" in css
