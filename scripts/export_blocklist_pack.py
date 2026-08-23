# -*- coding: utf-8 -*-
"""
Export the built-in BLOCKLIST from safety_filter.py into the versioned
canonical pack ``safety_blocklist.json`` (repo root).

Every installation auto-updates from that file (scripts/safety_updater.py),
so this is how maintainers push new words to all users:

    python scripts/export_blocklist_pack.py --version N

(remember to bump N on every change — equal or lower versions are ignored
by clients)
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import safety_filter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", type=int, required=True,
                        help="Pack version (bump on every change)")
    parser.add_argument("--out", default=None,
                        help="Output path (default: repo root safety_blocklist.json)")
    args = parser.parse_args()

    terms = [
        {"term": term, "lang": lang, "severity": severity, "category": category}
        for term, lang, severity, category in safety_filter.BLOCKLIST
    ]

    pack = {
        "version": args.version,
        "updated": __import__("datetime").date.today().isoformat(),
        "source": "ViralCutter built-in blocklist",
        "terms": terms,
    }

    out = args.out or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "safety_blocklist.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, indent=2)
    print(f"Exported {len(terms)} terms (v{args.version}) -> {out}")


if __name__ == "__main__":
    main()
