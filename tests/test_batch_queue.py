"""Tests for the batch queue logic (webui/batch_queue.py)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webui import batch_queue


def test_parse_queue_text_basic():
    text = "https://youtu.be/a\nhttps://youtu.be/b\n"
    assert batch_queue.parse_queue_text(text) == ["https://youtu.be/a", "https://youtu.be/b"]


def test_parse_queue_text_skips_blank_and_comments():
    text = "\n  \n# comment line\nhttps://youtu.be/a\n   https://youtu.be/b   \n#x\n"
    assert batch_queue.parse_queue_text(text) == ["https://youtu.be/a", "https://youtu.be/b"]


def test_parse_queue_text_dedupes_preserving_order():
    text = "https://youtu.be/a\nhttps://youtu.be/b\nhttps://youtu.be/a\n"
    assert batch_queue.parse_queue_text(text) == ["https://youtu.be/a", "https://youtu.be/b"]


def test_parse_queue_text_empty():
    assert batch_queue.parse_queue_text("") == []
    assert batch_queue.parse_queue_text(None) == []
    assert batch_queue.parse_queue_text("# only comments\n\n") == []


def test_make_items_and_mark():
    items = batch_queue.make_items(["u1", "u2"])
    assert items == [{"url": "u1", "status": "pending"},
                     {"url": "u2", "status": "pending"}]
    batch_queue.mark(items, 0, "running")
    assert items[0]["status"] == "running"
    batch_queue.mark(items, 0, "done")
    assert items[0]["status"] == "done"


def test_mark_rejects_unknown_status():
    items = batch_queue.make_items(["u1"])
    with pytest.raises(ValueError):
        batch_queue.mark(items, 0, "weird")


def test_rows_from_items():
    items = batch_queue.make_items(["u1", "u2"])
    batch_queue.mark(items, 1, "failed")
    rows = batch_queue.rows_from_items(items)
    assert rows[0][0] == 1 and rows[0][1] == "u1"
    assert "⏳" in rows[0][2]
    assert "❌" in rows[1][2]


def test_summary_counts():
    items = batch_queue.make_items(["a", "b", "c", "d"])
    batch_queue.mark(items, 0, "done")
    batch_queue.mark(items, 1, "done")
    batch_queue.mark(items, 2, "failed")
    done, failed = batch_queue.summary_counts(items)
    assert (done, failed) == (2, 1)


def test_looks_completed():
    marker = batch_queue.completion_marker()
    assert marker  # localized prefix must exist
    assert batch_queue.looks_completed(f"...\n{marker}/VIRALS/proj\n") is True
    assert batch_queue.looks_completed("ERROR: something broke") is False
    assert batch_queue.looks_completed("") is False
    assert batch_queue.looks_completed(None) is False


def test_supported_youtube_url_validation():
    assert batch_queue.is_supported_url("https://www.youtube.com/watch?v=abc")
    assert batch_queue.is_supported_url("https://youtu.be/abc")
    assert batch_queue.is_supported_url("https://youtube.com/shorts/abc")
    assert not batch_queue.is_supported_url("https://example.com/video")
    assert not batch_queue.is_supported_url("not a url")
    assert batch_queue.invalid_urls(["https://youtu.be/a", "bad"]) == ["bad"]
