# -*- coding: utf-8 -*-
"""
Arabic offensive-lexicon importer (v7.19).

Pulls the academic Arabic offensive-language lexicon by Mohammed Attia
(Google Research) — an LLM-extracted, human-annotated list of offensive,
vulgar, slur and dehumanizing Arabic terms — and merges the *high-severity*
identity slurs + dehumanization categories into the safety blocklist.

Source (attribution kept in the pack):
  https://github.com/mohammedattia/arabic-offensive-words
  "An LLM-Based Approach to the Creation of an Arabic Offensive Language
   Lexicon from Annotated Corpora" (Mohammed Attia, Google LLC)

Only categories that map directly to YouTube's Hate speech / Harassment
policies are imported (identity slurs, misogynist phrases, dehumanizing
insults). General profanity is intentionally excluded — the built-in list
already covers it and medium/low profanity must never over-block normal
speech.

Usage:
    python scripts/arabic_lexicon_importer.py --dry-run
    python scripts/arabic_lexicon_importer.py --write           # write safety_terms_arabic.json
    python scripts/arabic_lexicon_importer.py --merge-blocklist # append to safety_filter.BLOCKLIST
"""

import argparse
import json
import os
import re
import sys
import urllib.request

LEXICON_URL = ("https://raw.githubusercontent.com/"
               "mohammedattia/arabic-offensive-words/main/README.md")
FETCH_TIMEOUT = 20
OUT_FILENAME = "safety_terms_arabic.json"

# Sections that map to YouTube policy (hate speech / harassment).
# Identity slurs (4.x) are ALWAYS high. Animal/filth dehumanization (5, 6)
# is context-dependent (a pet video saying "كلب" is benign) → medium, which
# still blocks in default mode without over-censoring normal speech.
IMPORT_SECTIONS = [
    ("4. Identity-Based Slurs", "hate_slur", "high"),
    ("4.1", "hate_slur", "high"),          # fallback numbering variants
    ("4.2", "hate_slur", "high"),
    ("4.3", "hate_slur", "high"),
    ("4.4", "harassment", "medium"),
    ("5. Dehumanizing Insults", "hate_dehumanize", "medium"),
    ("6. Dehumanizing Insults", "hate_dehumanize", "medium"),
]


def _fetch(url=LEXICON_URL):
    req = urllib.request.Request(url, headers={"User-Agent": "ViralCutter-lexicon-importer"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return resp.read(4 * 1024 * 1024).decode("utf-8", errors="replace")


# A term bullet: * **عربية (Translit):** description
_BULLET_RE = re.compile(r"^\s*\*\s+\*{1,2}(.+?):\*{1,2}\s*(.*)$")
_MAIN_TERM_RE = re.compile(r"^([^/]+(?:/[^/]+)*)")  # split variants on '/'


def _extract_terms(text):
    """Return [(section_title, [terms])] grouped by heading."""
    sections = []
    current_title = None
    current_terms = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        heading = re.match(r"^#{2,4}\s+(.+)$", line)
        if heading:
            if current_title and current_terms:
                sections.append((current_title, current_terms))
            current_title = heading.group(1).strip()
            current_terms = []
            continue
        match = _BULLET_RE.match(line)
        if match and current_title:
            raw_term = match.group(1).strip()
            # take variants separated by '/' — each is a real surface form
            # (e.g. "زنجي / زنوج" -> "زنجي", "زنوج"); the transliteration in
            # parentheses is dropped, and the primary term keeps its order.
            cleaned = raw_term.split("(")[0]
            variants = [v.strip() for v in cleaned.split("/") if v.strip()]
            if not variants:
                variants = [cleaned.strip()]
            for term in variants:
                term = term.strip()
                if term and 2 <= len(term) <= 60:
                    current_terms.append(term)
    if current_title and current_terms:
        sections.append((current_title, current_terms))
    return sections


def _should_import(title):
    lowered = title.lower()
    for marker, _category, _severity in IMPORT_SECTIONS:
        if marker.lower() in lowered:
            return True
    # Broad catches for renamed headings
    if "identity-based" in lowered or "slur" in lowered:
        return True
    if "dehumanizing" in lowered:
        return True
    if "misogynist" in lowered or "sexist" in lowered:
        return True
    return False


def build_terms(text):
    """Return list of {term, lang, severity, category} dicts (ar)."""
    out = []
    seen = set()
    for title, terms in _extract_terms(text):
        if not _should_import(title):
            continue
        category, severity = "hate_slur", "high"
        for marker, cat, sev in IMPORT_SECTIONS:
            if marker.lower() in title.lower():
                category, severity = cat, sev
                break
        if "dehumanizing" in title.lower():
            # context-dependent → medium (still blocked by default policy)
            category, severity = "hate_dehumanize", "medium"
        if "misogynist" in title.lower() or "sexist" in title.lower():
            category, severity = "harassment", "medium"
        for term in terms:
            key = term.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append({"term": term, "lang": "ar", "severity": severity,
                        "category": category})
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="print stats only, write nothing")
    parser.add_argument("--write", action="store_true",
                        help="write safety_terms_arabic.json (repo root)")
    parser.add_argument("--merge-blocklist", action="store_true",
                        help="append terms to scripts/safety_filter.py BLOCKLIST")
    args = parser.parse_args()

    try:
        text = _fetch()
    except Exception as exc:
        print("[arabic-lexicon] fetch failed: {}".format(exc))
        return 1

    terms = build_terms(text)
    print("[arabic-lexicon] imported {} high-severity terms".format(len(terms)))
    by_cat = {}
    for t in terms:
        by_cat[t["category"]] = by_cat.get(t["category"], 0) + 1
    for cat, count in sorted(by_cat.items()):
        print("  {}: {}".format(cat, count))

    if args.dry_run:
        return 0

    if args.write:
        import hashlib
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, OUT_FILENAME)
        pack = {
            "version": 1,
            "updated": __import__("datetime").date.today().isoformat(),
            "source": "Mohammed Attia Arabic Offensive Lexicon (Google Research), "
                      "https://github.com/mohammedattia/arabic-offensive-words",
            "upstream_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "terms": terms,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pack, f, ensure_ascii=False, indent=2)
        print("[arabic-lexicon] wrote {}".format(path))

    if args.merge_blocklist:
        _merge_into_blocklist(terms)
    return 0


def _merge_into_blocklist(terms):
    """Append missing terms to safety_filter.BLOCKLIST (idempotent)."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts import safety_filter

    existing = {(t[0].casefold(), t[1]) for t in safety_filter.BLOCKLIST}
    added = 0
    for item in terms:
        if (item["term"].casefold(), item["lang"]) in existing:
            continue
        safety_filter.BLOCKLIST.append(
            (item["term"], item["lang"], item["severity"], item["category"]))
        existing.add((item["term"].casefold(), item["lang"]))
        added += 1
    print("[arabic-lexicon] appended {} new terms to BLOCKLIST".format(added))


if __name__ == "__main__":
    raise SystemExit(main())
