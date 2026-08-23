# -*- coding: utf-8 -*-
"""
CI helper for the YouTube Policy Watch workflow.

Runs the policy-page check and emits GitHub Actions output variables
(status / changed / markers) in a parse-safe way. Kept as a separate script
so the workflow YAML stays simple (multi-line python -c blocks are fragile
inside `run:` blocks and broke the workflow at the YAML-parse stage).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.youtube_policy_watch import check_policy_pages  # noqa: E402


def _github_output(key, value):
    """Append a key=value line to $GITHUB_OUTPUT if present (CI), else print."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")
    else:
        print(f"::set-output name={key}::{value}")


def main():
    # Best-effort check: the watcher never raises, it degrades to "unchanged".
    try:
        result = check_policy_pages()
    except Exception as exc:  # pragma: no cover - defensive
        result = {"status": "error", "changes": [],
                  "markers": {}, "error": str(exc)}

    status = result.get("status", "error")
    changes = result.get("changes", [])
    markers = result.get("markers", {})

    changed = "yes" if status == "changed" else "no"
    _github_output("status", status)
    _github_output("changed", changed)
    _github_output("changes", json.dumps(changes, ensure_ascii=False))
    _github_output("markers", json.dumps(markers, ensure_ascii=False))

    print("[policy-watch-ci] status={} changed={} changes={}".format(
        status, changed, ", ".join(changes) or "none"))
    return 0 if status != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
