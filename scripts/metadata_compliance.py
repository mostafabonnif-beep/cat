# -*- coding: utf-8 -*-
"""
Metadata Compliance — title / caption / hashtag policy check.

Roadmap item 2.4 ("فحص الكابشن والعنوان" / Metadata Compliance).

Protects against YouTube "spam / deceptive practices" strikes that keyword
filters can't see because they live in the *publishing metadata*:

    * banned hashtags        (gambling, adult, scams, politics, ...)
    * medical / financial    ("guaranteed cure", "get rich overnight")
    * exaggerated clickbait  ("you won't believe", "100% free", ...)
    * engagement bait        ("comment YES", "subscribe or miss out")
    * keyword stuffing       (the same phrase repeated too many times)

Design:
    * Pure stdlib (regex + sets) → runs anywhere, unit-testable.
    * Ships a sane default rule set; users can extend it with a JSON file
      (same pattern as safety_terms.json).
    * Findings carry severity (low/medium/high) so callers can warn or block.
    * `metadata_axis()` merges the result into the risk_scorecard entry.
"""

import json
import os
import re

# ---------------------------------------------------------------------------
# Default rules
# ---------------------------------------------------------------------------

# severity levels: low (advisory) < medium (flag for review) < high (block)
SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}

# Hashtag prefixes that are almost always policy-hostile for viral channels.
BANNED_HASHTAG_TERMS = [
    # scams / gambling / money
    "casino", "betting", "gamble", "gambling", "lottery", "freebitcoin",
    "cryptogiveaway", "giveaway", "earnmoney", "makemoneyonline", "getrich",
    "moneyscam", "doubledyourmoney", "bitcoinmining", "investmentguarantee",
    # adult content
    "nsfw", "xxx", "porn", "sex", "adultcontent", "18plus",
    # prohibited / regulated
    "buyfollowers", "buysubscribers", "buyviews", "getviewsfast",
    "hackedaccounts", "freeinstagram", "crackedsoftware", "keygen",
    "drugs", "marijuanashop", "buymedicines", "steroids",
    # medical misinformation
    "covidcure", "cancerremedy", "diabetescure", "weightlossmagic",
    # political manipulation / hate
    "electionfraud", "rigged", "whitegenocide", "racistmemes",
]

# Phrase patterns → category, severity
PATTERN_RULES = [
    # --- medical claims ---
    (r"\b(cures?|treats?|heals?) (all|cancer|diabetes|covid|corona)\w*", "medical_claim", "high"),
    (r"\b(cure|remedy|treatment)\b.{0,40}\b(guaranteed|100%|natural)\b", "medical_claim", "medium"),
    (r"\b(no (more|need) (doctors?|medication)|stop (taking|needing) (pills|medicine))\b", "medical_claim", "medium"),
    # --- financial guarantees ---
    (r"\b(make|earn|get) \$?\d+[kK]? (a|per|every) (day|week|hour)\b", "financial_claim", "medium"),
    (r"\b(get rich|passive income|financial freedom)\b.{0,30}\b(guaranteed|overnight|without (work|effort))\b", "financial_claim", "high"),
    (r"\b(guaranteed|100%)\b.{0,20}\b(profit|returns?|income|money)\b", "financial_claim", "high"),
    # --- exaggerated clickbait ---
    (r"\byou won'?t believe\b", "clickbait", "low"),
    (r"\b(they|this|the (truth|secret)) (hides?|don'?t want you to (know|see))\b", "clickbait", "low"),
    (r"\b(shocking|mind[- ]blowing|insane|unbelievable)\b", "clickbait", "low"),
    (r"\b(100%|totally|absolutely) (free|real|true)\b", "clickbait", "low"),
    # --- engagement bait ---
    (r"\b(comment|type) (yes|no|done|below|me)\b", "engagement_bait", "low"),
    (r"\b(subscribe|follow|share) (or|else)\b", "engagement_bait", "medium"),
    (r"\b(lIke|share|subscribe) this (video|post)\b.{0,20}\b(or (you'll|you will))", "engagement_bait", "medium"),
    # --- deceptive / manipulative ---
    (r"\b(not (a )?(scam|clickbait))\b", "deceptive", "low"),
    (r"\b(leaked|hacked|secret) (video|footage|audio)\b", "deceptive", "medium"),
    (r"\b(goes (viral|insane)|watch before (it'?s|it is) deleted)\b", "deceptive", "low"),
]

# Hashtags are lower-cased before matching, so rules use lowercase here.
HASHTAG_PATTERN_RULES = [
    (r"^(dr|doctor|medical|health)(tips?|advice)?$", "medical_hashtag", "low"),
]

# Keyword-stuffing: if the same 3+ word phrase appears this many times
KEYWORD_STUFFING_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Rule loading (user-extensible, mirrors safety_terms.json pattern)
# ---------------------------------------------------------------------------

def _normalize_rules(rules):
    """Ensure every rule tuple has exactly 3 fields (pattern, category, severity)."""
    out = []
    for item in rules:
        if len(item) == 3:
            out.append((item[0], item[1], item[2] if item[2] in SEVERITY_ORDER else "low"))
        else:
            continue
    return out


def load_extra_rules(path):
    """Load a JSON file with extra rules: {"hashtags": [...], "patterns": [[re, category, severity], ...]}."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        extra = {}
        hashtags = data.get("hashtags", [])
        if isinstance(hashtags, list):
            extra["hashtags"] = [str(h).lstrip("#").lower() for h in hashtags]
        patterns = data.get("patterns", [])
        if isinstance(patterns, list):
            extra["patterns"] = _normalize_rules(
                [(str(p[0]), str(p[1]), str(p[2])) for p in patterns if len(p) >= 3])
        return extra or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Core checks
# ---------------------------------------------------------------------------

def _find_patterns(text, rules):
    """Return findings for every pattern rule matched in `text` (lower-cased)."""
    findings = []
    lowered = (text or "").lower()
    for pattern, category, severity in rules:
        try:
            if re.search(pattern, lowered):
                findings.append({
                    "category": category,
                    "severity": severity,
                    "matched": pattern,
                })
        except re.error:
            continue
    return findings


def check_hashtags(hashtags, banned_terms=None, pattern_rules=None):
    """Validate a hashtag list. Returns (ok, findings)."""
    findings = []
    if not hashtags:
        return True, findings
    if isinstance(hashtags, str):
        hashtags = [h.strip().lstrip("#") for h in re.split(r"[,\s]+", hashtags) if h.strip()]
    banned = banned_terms if banned_terms is not None else BANNED_HASHTAG_TERMS
    rules = pattern_rules if pattern_rules is not None else HASHTAG_PATTERN_RULES

    for tag in hashtags:
        tag = str(tag).lstrip("#").lower()
        if not tag:
            continue
        if tag in banned:
            findings.append({"category": "banned_hashtag", "severity": "high",
                             "matched": "#" + tag})
            continue
        for pattern, category, severity in rules:
            if re.search(pattern, tag):
                findings.append({"category": category, "severity": severity,
                                 "matched": "#" + tag})
    return (len([f for f in findings if SEVERITY_ORDER[f["severity"]] >= 2]) == 0,
            findings)


def _keyword_stuffing_findings(text):
    """Flag the same long phrase repeated >= threshold times."""
    findings = []
    lowered = (text or "").lower()
    tokens = re.findall(r"\w+", lowered)
    phrases = {}
    for i in range(len(tokens) - 2):
        phrase = " ".join(tokens[i:i + 3])
        if len(phrase) >= 8:  # ignore ultra-common short combos
            phrases[phrase] = phrases.get(phrase, 0) + 1
    for phrase, count in phrases.items():
        if count >= KEYWORD_STUFFING_THRESHOLD:
            findings.append({
                "category": "keyword_stuffing",
                "severity": "low",
                "matched": '"{}" x{}'.format(phrase, count),
            })
    return findings


def check_metadata(title, caption, hashtags, extra_rules_path=None):
    """Full metadata compliance check.

    Returns:
        {
          "ok": bool,           # True when nothing reached medium severity
          "severity": str,      # worst severity found ("low" if clean)
          "title": [...findings],
          "caption": [...findings],
          "hashtags": [...findings],
          "findings": [...all findings],
        }
    """
    extra = load_extra_rules(extra_rules_path)
    banned = BANNED_HASHTAG_TERMS + (extra["hashtags"] if extra else [])
    pattern_rules = PATTERN_RULES + (extra["patterns"] if extra else [])

    title_findings = _find_patterns(title, pattern_rules) + _keyword_stuffing_findings(title)
    caption_findings = _find_patterns(caption, pattern_rules) + _keyword_stuffing_findings(caption)
    _, hashtag_findings = check_hashtags(hashtags, banned_terms=banned)

    all_findings = title_findings + caption_findings + hashtag_findings

    worst = "low"
    for f in all_findings:
        if SEVERITY_ORDER[f["severity"]] > SEVERITY_ORDER[worst]:
            worst = f["severity"]

    ok = not any(SEVERITY_ORDER[f["severity"]] >= 2 for f in all_findings)
    return {
        "ok": ok,
        "severity": worst if all_findings else "low",
        "title": title_findings,
        "caption": caption_findings,
        "hashtags": hashtag_findings,
        "findings": all_findings,
    }


def metadata_axis(title, caption, hashtags, extra_rules_path=None):
    """Risk-scorecard friendly axis: {"ok": bool, "severity": str, "score": int, "findings": [...]}."""
    res = check_metadata(title, caption, hashtags, extra_rules_path)
    score = {"low": 0, "medium": 40, "high": 80}[res["severity"]]
    return {
        "ok": res["ok"],
        "severity": res["severity"],
        "score": score,
        "findings": res["findings"],
    }


def summarize_metadata(res):
    """Short human-readable summary line for console output."""
    n = len(res["findings"])
    if n == 0:
        return "metadata ok"
    parts = []
    for cat in sorted({f["category"] for f in res["findings"]}):
        parts.append("{} x{}".format(cat, sum(1 for f in res["findings"] if f["category"] == cat)))
    return "metadata {}: {}".format(res["severity"], ", ".join(parts))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ViralCutter metadata compliance check.")
    parser.add_argument("--title", default="", help="Video title")
    parser.add_argument("--caption", default="", help="Video caption/description")
    parser.add_argument("--hashtags", default="", help="Comma-separated hashtags (with or without #)")
    parser.add_argument("--extra-rules", default=None, help="Extra rules JSON file")
    parser.add_argument("--json", action="store_true", help="Print raw JSON report")
    args = parser.parse_args()

    res = check_metadata(args.title, args.caption, args.hashtags, args.extra_rules)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(summarize_metadata(res))
        for f in res["findings"]:
            print("[{}] {}".format(f["severity"].upper(), f["matched"]))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
