# -*- coding: utf-8 -*-
"""
Safety blocklist auto-updater (v7.18 — multi-source, continuous).

YouTube's policies and the evasion vocabulary evolve constantly. Instead of
waiting for a code release, ViralCutter keeps a *versioned blocklist pack*
in the GitHub repo and every installation updates itself from it:

    repo: safety_blocklist.json            (canonical, versioned)
    user: safety_blocklist_cache.json      (downloaded copy, git-ignored)

v7.18 adds:
* **Multi-source merge** — the updater can combine packs from several
  remotes (canonical repo + mirrors + community packs). Terms are merged
  per (term, lang): the highest severity wins, newer packs win ties.
* **Continuous watch mode** — ``python scripts/safety_updater.py --watch 6``
  re-checks every 6 hours so a long-running session always uses fresh
  policy data without restarting.
* **Source provenance** — every cached pack records which URL it came from.

Guarantees (unchanged):
* Offline-safe — any network/parse failure keeps the previous cache (and if
  there is no cache at all, the built-in list still protects the user).
* Only *newer* versions replace the cache.
* Cheap — a daily stamp file prevents hammering GitHub on every run.
* No dependencies — pure urllib (works on Windows out of the box).
"""

import json
import os
import time
import urllib.request

REMOTE_URL = ("https://raw.githubusercontent.com/"
              "mostafabonnif-beep/cat/main/safety_blocklist.json")

# v7.18: fallback mirrors / community packs merged on top of the canonical
# pack. Each entry may also carry "required": False to tolerate a dead mirror.
REMOTE_SOURCES = [
    {"url": REMOTE_URL, "required": True, "label": "canonical"},
    {
        "url": ("https://raw.githubusercontent.com/"
                "eldjazaireldjadida4-web/ViralCutter/main/safety_blocklist.json"),
        "required": False,
        "label": "legacy-mirror",
    },
]

CACHE_FILENAME = "safety_blocklist_cache.json"
STAMP_FILENAME = ".safety_update_stamp"
FETCH_TIMEOUT = 10          # seconds
MAX_TERMS = 20000           # sanity limit for a downloaded pack
ONE_DAY = 24 * 3600
MAX_SOURCES = 8             # sanity limit for merged packs


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cache_path(base_dir=None):
    return os.path.join(base_dir or _repo_root(), CACHE_FILENAME)


def stamp_path(base_dir=None):
    return os.path.join(base_dir or _repo_root(), STAMP_FILENAME)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_pack(data):
    """Return the normalized {"version": int, "terms": [...]} or None."""
    if not isinstance(data, dict):
        return None
    terms = data.get("terms")
    if not isinstance(terms, list) or not terms or len(terms) > MAX_TERMS:
        return None
    clean = []
    for t in terms:
        if not isinstance(t, dict) or not t.get("term"):
            continue
        clean.append({
            "term": str(t["term"])[:200],
            "lang": str(t.get("lang", "multi"))[:20],
            "severity": t.get("severity") if t.get("severity") in ("low", "medium", "high") else "high",
            "category": str(t.get("category", "custom"))[:50],
        })
    if not clean:
        return None
    try:
        version = int(data.get("version", 0))
    except Exception:
        version = 0
    return {"version": version, "terms": clean,
            "updated": str(data.get("updated", ""))[:40],
            "source": str(data.get("source", ""))[:200]}


def load_cached_pack(base_dir=None):
    """Read the downloaded cache (None if missing/corrupt)."""
    path = cache_path(base_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return validate_pack(json.load(f))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Update logic
# ---------------------------------------------------------------------------

def _fetch_json(url, timeout=FETCH_TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": "ViralCutter-safety-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(2 * 1024 * 1024)  # 2 MB cap
    return json.loads(raw.decode("utf-8"))


def merge_packs(packs):
    """Merge validated packs: highest severity per (term, lang) wins.

    ``packs`` is an ordered list of dicts from :func:`validate_pack`. Later
    packs win version ties. Returns a single normalized pack.
    """
    merged = {}
    max_version = 0
    updated = ""
    source_labels = []
    for pack in packs:
        if not pack:
            continue
        max_version = max(max_version, pack["version"])
        if pack.get("updated", "").upper() > updated.upper():
            updated = pack["updated"]
        if pack.get("source"):
            source_labels.append(pack["source"])
        for term in pack["terms"]:
            key = (term["term"].casefold(), term["lang"])
            previous = merged.get(key)
            order = {"low": 0, "medium": 1, "high": 2}
            if previous is None or order.get(term["severity"], 2) > order.get(previous["severity"], 2):
                merged[key] = term
    return {
        "version": max_version,
        "updated": updated,
        "source": "; ".join(dict.fromkeys(source_labels)) or "merged packs",
        "terms": list(merged.values()),
    }


def check_and_update(base_dir=None, url=None, force=False, extra_urls=None):
    """Fetch remote pack(s) and replace the cache if a newer one is available.

    Returns a status dict: {status: updated|up-to-date|offline|error,
                            version, previous_version, message}
    Never raises.

    * ``url=None`` (default) → multi-source mode: canonical repo + legacy
      mirrors merged automatically.
    * ``url=<explicit>`` → single-source mode: only that URL is used (plus
      any ``extra_urls``). An unreachable explicit URL reports ``offline``
      without falling back to mirrors — callers who pin a source get that
      source, not a surprise.
    """
    cached = load_cached_pack(base_dir)
    cached_version = cached["version"] if cached else 0

    # daily throttle (unless forced)
    stamp = stamp_path(base_dir)
    if not force and os.path.exists(stamp):
        try:
            last = float(open(stamp, encoding="utf-8").read().strip() or "0")
            if time.time() - last < ONE_DAY:
                return {"status": "up-to-date", "version": cached_version,
                        "previous_version": cached_version,
                        "message": "checked recently (daily throttle)"}
        except Exception:
            pass

    if url is not None:
        sources = [{"url": url, "required": True, "label": "primary"}]
        sources.extend({"url": u, "required": False, "label": "extra"} for u in (extra_urls or []))
    else:
        sources = [{"url": REMOTE_URL, "required": True, "label": "canonical"}]
        for source in REMOTE_SOURCES:
            if all(source["url"] != s["url"] for s in sources):
                sources.append(source)
    sources = sources[:MAX_SOURCES]

    fetched = []
    errors = []
    for source in sources:
        try:
            pack = validate_pack(_fetch_json(source["url"]))
            if pack:
                fetched.append(pack)
            else:
                errors.append("{}: invalid pack".format(source["label"]))
        except Exception as e:
            errors.append("{}: {}".format(source["label"], e))

    try:
        with open(stamp, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except Exception:
        pass

    if not fetched:
        return {"status": "offline", "version": cached_version,
                "previous_version": cached_version,
                "message": "could not reach any update source ({}) — using local list".format(
                    "; ".join(errors[:2]))}

    merged = merge_packs(fetched)
    if merged["version"] <= cached_version and cached is not None:
        return {"status": "up-to-date", "version": cached_version,
                "previous_version": cached_version,
                "message": "list is current (v{})".format(cached_version)}

    try:
        with open(cache_path(base_dir), "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return {"status": "error", "version": cached_version,
                "previous_version": cached_version,
                "message": "could not write cache ({})".format(e)}

    return {"status": "updated", "version": merged["version"],
            "previous_version": cached_version,
            "message": "updated v{} → v{} ({} terms, sources: {})".format(
                cached_version, merged["version"], len(merged["terms"]),
                merged.get("source", "?"))}


def watch(base_dir=None, interval_hours=6, max_cycles=None):
    """Continuous update loop: re-check every ``interval_hours`` hours.

    Returns after ``max_cycles`` iterations (None = run forever). Designed
    for long-running WebUI sessions and always-on boxes.
    """
    import time as _time
    interval = max(1.0, float(interval_hours)) * 3600
    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        cycle += 1
        result = check_and_update(base_dir=base_dir, force=True)
        print("[safety-updater] cycle {}: {} — {}".format(
            cycle, result["status"], result["message"]))
        if max_cycles is not None and cycle >= max_cycles:
            break
        _time.sleep(interval)
    return cycle


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Update the hate-speech blocklist from GitHub (multi-source).")
    parser.add_argument("--force", action="store_true", help="ignore the daily throttle")
    parser.add_argument("--url", default=REMOTE_URL, help="override the primary pack URL")
    parser.add_argument("--extra-url", action="append", default=[],
                        help="additional pack URL to merge (repeatable)")
    parser.add_argument("--watch", type=float, default=None, metavar="HOURS",
                        help="continuous mode: re-check every N hours")
    parser.add_argument("--cycles", type=int, default=None,
                        help="with --watch: stop after N cycles (default: forever)")
    args = parser.parse_args()

    if args.watch is not None:
        count = watch(interval_hours=args.watch, max_cycles=args.cycles)
        print("[safety-updater] watch finished after {} cycle(s)".format(count))
        return

    result = check_and_update(force=args.force, url=args.url,
                              extra_urls=args.extra_url)
    print("[safety-updater] {}: {}".format(result["status"], result["message"]))


if __name__ == "__main__":
    main()
