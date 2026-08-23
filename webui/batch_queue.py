"""Batch queue logic: parse URL lists and track per-item status.

Pure logic (no gradio imports) — unit-testable. The WebUI drives one
pipeline run per item and uses this module for parsing + status rows.
"""
import os
import sys
import urllib.parse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from i18n.i18n import DEFAULT_LANGUAGE, I18nAuto

i18n = I18nAuto(DEFAULT_LANGUAGE)

HEADERS = ["#", i18n("Link"), i18n("Status")]

STATUS_ICONS = {"pending": "⏳", "running": "🔄", "done": "✅", "failed": "❌"}
STATUS_I18N = {
    "pending": "Status Pending",
    "running": "Status Running",
    "done": "Status Done",
    "failed": "Status Failed",
}


def parse_queue_text(text):
    """Parse a textarea into a clean URL list.

    Skips empty lines and # comments, trims whitespace, dedupes while
    preserving order.
    """
    urls = []
    seen = set()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line not in seen:
            seen.add(line)
            urls.append(line)
    return urls


SUPPORTED_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}


def is_supported_url(url):
    try:
        parsed = urllib.parse.urlparse(str(url).strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or host not in SUPPORTED_HOSTS:
            return False
        if host == "youtu.be":
            return bool(parsed.path.strip("/"))
        return parsed.path.startswith(("/watch", "/shorts/", "/live/", "/embed/"))
    except (TypeError, ValueError):
        return False


def invalid_urls(urls):
    return [url for url in (urls or []) if not is_supported_url(url)]


def make_items(urls):
    return [{"url": u, "status": "pending"} for u in urls]


def mark(items, index, status):
    if status not in STATUS_ICONS:
        raise ValueError(f"unknown status: {status}")
    items[index]["status"] = status
    return items


def status_display(status):
    return f"{STATUS_ICONS[status]} {i18n(STATUS_I18N[status])}"


def rows_from_items(items):
    return [[i + 1, item["url"], status_display(item["status"])]
            for i, item in enumerate(items)]


def summary_counts(items):
    done = sum(1 for it in items if it["status"] == "done")
    failed = sum(1 for it in items if it["status"] == "failed")
    return done, failed


def completion_marker():
    """Localized prefix the pipeline logs on success (from main_improved)."""
    return i18n("Process completed! Check your results in: {}").split("{}")[0]


def looks_completed(logs):
    """Heuristic: did a run finish successfully based on its final logs?"""
    if not logs:
        return False
    return completion_marker() in logs
