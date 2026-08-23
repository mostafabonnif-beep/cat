# -*- coding: utf-8 -*-
"""
SEO title & keywords engine (v7.22) — عناوين SEO ذكية للمقاطع القصيرة.

What it does
------------
1. **YouTube autocomplete fetch** — uses YouTube's public suggestion API
   (``suggestqueries.google.com``, no key needed) to find what people
   actually search around a topic:
   ``python scripts/seo_titles.py suggest --topic "كيف تكسب المال"``
2. **Title scoring** — a transparent heuristic score (0-100) rewarding
   hook strength, keyword presence, length in the 35-70 char sweet spot,
   question/urgency patterns, and number presence; punishing ALL-CAPS,
   clickbait repetition and keyword stuffing.
3. **Title generation** — deterministic template-based candidates from a
   topic + keywords + angle, ranked by the same scorer.
4. **Best publish-time hints** — a small built-in table of engagement
   windows (conservative, editable) so the scheduler has a sane default.

Everything is offline-safe: the suggest fetch degrades to a keyword-only
result when the network is unavailable.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

SUGGEST_URL = "https://suggestqueries.google.com/complete/search"
FETCH_TIMEOUT = 8
MAX_SUGGESTIONS = 10

# Conservative engagement windows (local time). Tune per channel: these are
# defaults, not gospel — the scheduler combines them with the user's own
# chosen window when provided.
BEST_TIMES_BY_PLATFORM = {
    "youtube": {"weekday": [(17, 21)], "weekend": [(10, 13), (17, 21)]},
    "tiktok": {"weekday": [(12, 14), (19, 23)], "weekend": [(10, 12), (19, 23)]},
    "reels": {"weekday": [(12, 14), (18, 21)], "weekend": [(11, 13), (18, 21)]},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------

def fetch_suggestions(topic: str, language: str = "ar", region: str = "",
                      max_results: int = MAX_SUGGESTIONS) -> list[str]:
    """Query YouTube's public autocomplete API.

    Returns a de-duplicated list of suggestion strings. On any network /
    parse failure returns [] (callers treat that as "offline, use topic").
    """
    params = {
        "client": "youtube",
        "ds": "yt",
        "hl": language or "ar",
        "q": topic,
    }
    if region:
        params["gl"] = region
    url = "{}?{}".format(SUGGEST_URL, urllib.parse.urlencode(params))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            raw = resp.read(512 * 1024).decode("utf-8", errors="replace")
        data = _parse_suggest_response(raw)
        suggestions = data[1] if isinstance(data, list) and len(data) > 1 else []
        seen = set()
        out = []
        for item in suggestions:
            text = _suggestion_text(item)
            key = text.casefold()
            if text and key not in seen:
                seen.add(key)
                out.append(text)
            if len(out) >= max_results:
                break
        return out
    except Exception:
        return []


def _suggestion_text(item) -> str:
    """Extract the plain suggestion string from either a list item or the
    stringified form YouTube sometimes returns (``"['make money', 0, ...]"``)."""
    if isinstance(item, list) and item:
        return str(item[0]).strip()
    text = str(item).strip()
    if text.startswith("[") and text.endswith("]") and text[1:2] in ("'", '"'):
        # stringified list: ['make money', 0, [512]]
        inner = text[2:text.rfind("'")] if text[1:2] == "'" else text[2:text.rfind('"')]
        return inner.strip()
    return text


def _parse_suggest_response(raw: str):
    """Parse the autocomplete response — plain JSON OR the
    ``window.google.ac.h([...])`` wrapper YouTube sometimes returns."""
    text = str(raw).strip()
    if not text:
        return []
    if text.startswith("window.google.ac.h("):
        start = text.find("(")
        end = text.rfind(")")
        if start != -1 and end > start:
            text = text[start + 1:end]
    try:
        return json.loads(text)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _len_score(title: str) -> float:
    n = len(title)
    if 35 <= n <= 70:
        return 25.0
    if 20 <= n < 35 or 70 < n <= 90:
        return 15.0
    if 10 <= n < 20 or 90 < n <= 110:
        return 8.0
    return 0.0


def _hook_score(title: str) -> float:
    score = 0.0
    if title.endswith(("?", "؟")):
        score += 8.0
    elif title.endswith(("!", "!")):
        score += 4.0
    if re.search(r"\d", title):
        score += 6.0
    if any(word in title for word in ("كيف", "لماذا", "ما هو", "طريقة", "أفضل",
                                      "سر", "خطأ", "جديد", "how", "why", "best",
                                      "secret", "mistake", "trick", "tips")):
        score += 7.0
    if title.count("!") > 2 or title.count("؟") > 2:
        score -= 6.0
    return max(0.0, score)


def _keyword_score(title: str, keywords: list[str]) -> float:
    if not keywords:
        return 5.0
    folded = title.casefold()
    hits = sum(1 for k in keywords if k and k.casefold() in folded)
    if hits == 0:
        return 0.0
    if hits == 1:
        return 8.0
    return min(15.0, 8.0 + (hits - 1) * 3.5)


def _penalty_score(title: str) -> float:
    penalty = 0.0
    if title.isupper() and any(c.isalpha() for c in title):
        penalty += 15.0
    # keyword stuffing: repeated words
    words = re.findall(r"\w+", title.casefold())
    repeats = len(words) - len(set(words))
    if repeats > 1:
        penalty += 5.0 * min(3, repeats)
    if len(title) > 110:
        penalty += 10.0
    return penalty


def score_title(title: str, keywords: list[str] | None = None) -> dict[str, Any]:
    """Return a transparent 0-100 score with a breakdown."""
    title = str(title or "").strip()
    if not title:
        return {"score": 0.0, "breakdown": {"length": 0, "hook": 0,
                                            "keywords": 0, "penalty": 0}}
    keywords = [str(k) for k in (keywords or []) if str(k).strip()]
    length = _len_score(title)
    hook = _hook_score(title)
    keywords_score = _keyword_score(title, keywords)
    penalty = _penalty_score(title)
    total = round(max(0.0, min(100.0, length + hook + keywords_score - penalty)), 1)
    return {
        "score": total,
        "breakdown": {
            "length": round(length, 1),
            "hook": round(hook, 1),
            "keywords": round(keywords_score, 1),
            "penalty": round(penalty, 1),
        },
    }


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

_TEMPLATES = [
    "{topic} — {hook}",
    "طريقة {topic} خطوة بخطوة",
    "{topic}: {benefit}",
    "لماذا {topic}؟ الجواب سيفاجئك",
    "{topic} في {number} خطوات فقط",
    "أفضل نصيحة عن {topic}",
    "ما الذي يجب أن تعرفه عن {topic}؟",
    "اعتراف مهم عن {topic}",
]

_HOOKS = ["هذه الطريقة تعمل فعلاً", "لا أحد يخبرك بهذا", "اكتشفت السر",
          "بعد تجربة طويلة", "النتيجة صدمتني"]
_BENEFITS = ["وفّر وقتك وجهدك", "النتائج من أول مرة", "بلا أخطاء شائعة",
             "بأبسط طريقة ممكنة"]


def _topic_token(topic: str) -> str:
    return re.sub(r"[\?؟!\s]+$", "", str(topic or "").strip()) or "هذا الموضوع"


def generate_titles(topic: str, keywords: list[str] | None = None,
                    count: int = 6) -> list[dict[str, Any]]:
    """Generate ranked title candidates from a topic + optional keywords."""
    topic = _topic_token(topic)
    keywords = [str(k) for k in (keywords or []) if str(k).strip()]
    candidates = []
    import random
    rng = random.Random(hash(topic) & 0xFFFFFFFF)
    for i, template in enumerate(_TEMPLATES):
        title = template.format(
            topic=topic,
            hook=_HOOKS[i % len(_HOOKS)],
            benefit=_BENEFITS[i % len(_BENEFITS)],
            number=str(rng.randint(3, 10)),
        )
        title = re.sub(r"\s+", " ", title).strip()
        if title.casefold().startswith("كيف كيف"):
            continue
        candidates.append({"title": title, **score_title(title, keywords)})
    # de-duplicate by title
    seen = set()
    unique = []
    for item in candidates:
        key = item["title"].casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    unique.sort(key=lambda item: item["score"], reverse=True)
    return unique[:count]


# ---------------------------------------------------------------------------
# Publish-time hints
# ---------------------------------------------------------------------------

def best_time_windows(platform: str = "youtube") -> dict[str, Any]:
    """Return the built-in engagement windows for a platform."""
    resolved = platform if platform in BEST_TIMES_BY_PLATFORM else "youtube"
    data = BEST_TIMES_BY_PLATFORM[resolved]
    return {"platform": resolved, **data}


def suggest_next_slots(platform: str = "youtube", count: int = 5,
                       from_time=None) -> list[str]:
    """Return the next ``count`` ISO slots inside the platform's windows.

    ``from_time`` (datetime, timezone-aware) defaults to now. Slots are
    hourly starts inside the best windows, skipping ones in the past.
    """
    import datetime as dt
    data = BEST_TIMES_BY_PLATFORM.get(
        platform, BEST_TIMES_BY_PLATFORM["youtube"])
    now = from_time or dt.datetime.now(dt.timezone.utc)
    slots = []
    # scan the next 14 days
    for day_offset in range(14):
        day = now + dt.timedelta(days=day_offset)
        is_weekend = day.weekday() >= 5
        windows = data.get("weekend" if is_weekend else "weekday", [])
        for start_hour, _end_hour in windows:
            slot = day.replace(hour=start_hour, minute=0, second=0, microsecond=0)
            if slot <= now:
                continue
            slots.append(slot.isoformat())
            if len(slots) >= count:
                return slots
    return slots


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")

    sug = sub.add_parser("suggest", help="fetch YouTube autocomplete suggestions")
    sug.add_argument("topic")
    sug.add_argument("--lang", default="ar")
    sug.add_argument("--region", default="")

    gen = sub.add_parser("generate", help="generate ranked SEO titles")
    gen.add_argument("topic")
    gen.add_argument("--keyword", action="append", default=[])
    gen.add_argument("--count", type=int, default=6)

    score = sub.add_parser("score", help="score a title")
    score.add_argument("title")
    score.add_argument("--keyword", action="append", default=[])

    slots = sub.add_parser("slots", help="suggest next publish-time slots")
    slots.add_argument("--platform", default="youtube",
                       choices=["youtube", "tiktok", "reels"])
    slots.add_argument("--count", type=int, default=5)

    args = parser.parse_args(argv)
    if args.command == "suggest":
        result = fetch_suggestions(args.topic, args.lang, args.region)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "generate":
        result = generate_titles(args.topic, args.keyword, args.count)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "score":
        result = score_title(args.title, args.keyword)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "slots":
        print(json.dumps(suggest_next_slots(args.platform, args.count),
                         ensure_ascii=False, indent=2))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
