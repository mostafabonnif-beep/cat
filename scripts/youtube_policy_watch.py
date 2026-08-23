# -*- coding: utf-8 -*-
"""
YouTube policy watch — continuously track YouTube's official moderation
documents so the local safety database stays in sync with what YouTube
actually enforces.

Why this exists
---------------
YouTube does not publish a machine-readable hate-word list, but it *does*
maintain official, versioned policy pages (Hate speech policy, Harassment &
cyberbullying, Violent or graphic content, Community Guidelines). When those
pages change, the moderation vocabulary changes with them — and the
blocklist must be updated soon after.

This module:
1. Fetches the official YouTube policy pages (plain HTML).
2. Hashes each page and stores the hash in ``youtube_policy_feed.json``
   (repo root / project folder).
3. Reports whether a policy revision was detected since the last run.
4. Extracts the *category names* and *example markers* the page uses, so the
   maintainer can see exactly which policy area changed.
5. Can be run continuously (``--watch``) exactly like ``safety_updater.py``.

It never parses JavaScript, never requires an API key, and never blocks the
pipeline: every failure degrades to "unchanged" so an offline box keeps
working with the previous feed.
"""

import hashlib
import json
import os
import re
import urllib.request

FEED_FILENAME = "youtube_policy_feed.json"

# Official YouTube help-center pages (stable URLs).
POLICY_PAGES = {
    "hate_speech": "https://support.google.com/youtube/answer/2801939",
    "harassment": "https://support.google.com/youtube/answer/2802268",
    "violent_graphic": "https://support.google.com/youtube/answer/2802008",
    "community_guidelines": "https://www.youtube.com/howyoutubeworks/policies/community-guidelines/",
}

FETCH_TIMEOUT = 12
MAX_PAGE_BYTES = 1024 * 512
USER_AGENT = "Mozilla/5.0 (ViralCutter-safety-updater)"


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def feed_path(base_dir=None):
    return os.path.join(base_dir or _repo_root(), FEED_FILENAME)


def _fetch_text(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return resp.read(MAX_PAGE_BYTES).decode("utf-8", errors="replace")


def _normalize_html(html):
    """Strip tags/whitespace so content-only changes change the hash."""
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().casefold()


def _page_hash(html):
    return hashlib.sha256(_normalize_html(html).encode("utf-8")).hexdigest()


def _extract_markers(html):
    """Pull short quoted 'do not' markers the page uses (best effort)."""
    text = _normalize_html(html)
    markers = []
    for match in re.finditer(r"(?:don'?t|do not|not allowed|policy)[^.]{0,160}?\.", text):
        snippet = match.group(0).strip()
        if 20 <= len(snippet) <= 180:
            markers.append(snippet[:180])
        if len(markers) >= 8:
            break
    return markers


def load_feed(base_dir=None):
    path = feed_path(base_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def check_policy_pages(base_dir=None, force=False):
    """Fetch the policy pages and compare hashes against the stored feed.

    Returns a dict:
        {
          "status": "changed" | "unchanged" | "offline",
          "checked_at": ISO timestamp,
          "changes": [policy_key, ...],       # keys whose hash changed
          "markers": {policy_key: [markers]}, # extracted markers for changed pages
          "feed_written": bool,
        }
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    feed = load_feed(base_dir)
    stored = feed.get("hashes") if isinstance(feed.get("hashes"), dict) else {}
    changes = []
    markers = {}
    fresh_hashes = {}
    offline = False

    for key, url in POLICY_PAGES.items():
        try:
            html = _fetch_text(url)
        except Exception:
            offline = True
            continue
        digest = _page_hash(html)
        fresh_hashes[key] = digest
        if stored.get(key) not in (None, digest):
            changes.append(key)
            markers[key] = _extract_markers(html)

    if not fresh_hashes:
        return {"status": "offline", "checked_at": now, "changes": [],
                "markers": {}, "feed_written": False}

    feed = {
        "checked_at": now,
        "hashes": fresh_hashes,
        "last_change_detected_at": feed.get("last_change_detected_at"),
    }
    if changes:
        feed["last_change_detected_at"] = now
    try:
        with open(feed_path(base_dir), "w", encoding="utf-8") as f:
            json.dump(feed, f, ensure_ascii=False, indent=2)
        written = True
    except OSError:
        written = False

    return {
        "status": "changed" if changes else "unchanged",
        "checked_at": now,
        "changes": changes,
        "markers": markers,
        "feed_written": written,
        "offline_pages": [key for key in POLICY_PAGES if key not in fresh_hashes],
    }


def watch(base_dir=None, interval_hours=24, max_cycles=None):
    """Continuous loop mirroring ``safety_updater.watch``."""
    import time as _time
    interval = max(1.0, float(interval_hours)) * 3600
    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        cycle += 1
        result = check_policy_pages(base_dir=base_dir, force=True)
        print("[policy-watch] cycle {}: {}".format(cycle, result["status"]))
        if result["status"] == "changed":
            print("[policy-watch] CHANGED policies: {}".format(
                ", ".join(result["changes"])))
        if max_cycles is not None and cycle >= max_cycles:
            break
        _time.sleep(interval)
    return cycle


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Watch YouTube's official policy pages for changes.")
    parser.add_argument("--watch", type=float, default=None, metavar="HOURS",
                        help="continuous mode: re-check every N hours")
    parser.add_argument("--cycles", type=int, default=None,
                        help="with --watch: stop after N cycles (default: forever)")
    args = parser.parse_args()

    if args.watch is not None:
        count = watch(interval_hours=args.watch, max_cycles=args.cycles)
        print("[policy-watch] finished after {} cycle(s)".format(count))
        return

    result = check_policy_pages()
    print("[policy-watch] status={} changes={}".format(
        result["status"], ", ".join(result["changes"]) or "none"))


if __name__ == "__main__":
    main()
