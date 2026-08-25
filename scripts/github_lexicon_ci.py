# -*- coding: utf-8 -*-
"""
CI helper for the Arabic Lexicon Freshness workflow.

Compares the upstream lexicon README hash against the stored
upstream_sha256 in safety_terms_arabic.json and emits GitHub Actions
outputs (changed / upstream_sha256). Kept as a script so the workflow YAML
stays simple and parse-safe.
"""

import hashlib
import json
import os
import urllib.request

UPSTREAM_URL = ("https://raw.githubusercontent.com/"
                "mohammedattia/arabic-offensive-words/main/README.md")
TIMEOUT = 20


def _github_output(key, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")
    else:
        print(f"::set-output name={key}::{value}")


def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    req = urllib.request.Request(
        UPSTREAM_URL, headers={"User-Agent": "viralcutter-lexicon-watch"})
    upstream = urllib.request.urlopen(req, timeout=TIMEOUT).read()
    digest = hashlib.sha256(upstream).hexdigest()

    stored = ""
    pack_path = os.path.join(repo_root, "safety_terms_arabic.json")
    try:
        with open(pack_path, encoding="utf-8") as f:
            stored = (json.load(f) or {}).get("upstream_sha256", "")
    except Exception:
        stored = ""

    changed = "yes" if (stored and digest != stored) else ("yes" if not stored else "no")
    _github_output("changed", changed)
    _github_output("upstream_sha256", digest)
    print("[lexicon-ci] stored={} upstream={} changed={}".format(
        stored[:16] or "none", digest[:16], changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
