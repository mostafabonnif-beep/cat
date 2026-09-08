"""Turn measured performance insights into selection-score weights.

Reads ``performance_insights.json`` (from scripts/performance_loop) and maps
the observed correlations into small, bounded weight adjustments for
``_selection_score`` in create_viral_segments. Weights stay within +/-0.10 so
the editorial core of the score is never dominated by a thin sample, and the
report always states how many published clips the learning rests on.

v7.33.3: the same insights file now also carries *content* learning
(``content_insights`` — which hook types / angles / topics / title styles
actually brought views on THIS channel). Those become bounded per-segment
style bonuses, so the next selection round prefers the formulas the
channel's own data proved winning instead of generic editorial taste.
"""
from __future__ import annotations

import json
import os
from typing import Any

INSIGHTS_NAME = "performance_insights.json"
DEFAULT_WEIGHTS = {
    "virality": 0.40, "hook": 0.20, "completeness": 0.20,
    "clarity": 0.10, "novelty": 0.05, "title": 0.05,
}
MAX_SHIFT = 0.10
MIN_SAMPLES = 3

# Content-style learning bounds (v7.33.3). A style must beat the channel
# average by at least STYLE_MIN_DELTA_PCT before it moves the needle, and no
# single style can add/remove more than STYLE_MAX_BONUS points so the
# editorial core of the score always dominates.
STYLE_MIN_DELTA_PCT = 15.0
STYLE_MAX_BONUS = 3.0

_HOOK_WORDS = ("كيف", "كيفاش", "علاش", "شنو", "لماذا", "طريقة", "أفضل",
               "سر", "خطأ", "جديد", "شحال", "how", "why", "best", "secret",
               "mistake", "trick", "tips", "top")


def title_style_flags(title) -> dict[str, bool]:
    """Boolean title-style markers used by the content-learning loop."""
    text = str(title or "").strip()
    lowered = text.casefold()
    return {
        "title_question": any(ch in text for ch in ("؟", "?")),
        "title_number": any(ch.isdigit() for ch in text),
        "title_hook_word": any(w in lowered for w in _HOOK_WORDS if w.isascii())
        or any(w in text for w in _HOOK_WORDS if not w.isascii()),
    }


def _style_bonus(delta_pct) -> float:
    """Bounded bonus from a measured delta vs the channel average."""
    if delta_pct is None or abs(float(delta_pct)) < STYLE_MIN_DELTA_PCT:
        return 0.0
    return round(max(-STYLE_MAX_BONUS, min(STYLE_MAX_BONUS,
                                           float(delta_pct) / 25.0)), 2)


def style_bonuses(insights) -> dict[str, Any]:
    """Content-insights -> per-value style bonuses (empty when unmeasured).

    Only values with >=2 measured clips and a >=15% delta vs the channel
    average produce a bonus; no single style may move a score more than
    STYLE_MAX_BONUS points.
    """
    content = (insights or {}).get("content_insights") or {}
    if not content.get("overall_avg_views"):
        return {}
    style: dict[str, Any] = {"basis": "content_insights"}
    categories = content.get("categories") or {}
    for field in ("hook_type", "angle", "topic"):
        by_value = {}
        for row in categories.get(field) or []:
            if int(row.get("samples") or 0) < 2:
                continue
            value = str(row.get("value") or "")
            bonus = _style_bonus(float(row.get("delta_pct") or 0.0))
            if value and bonus:
                by_value[value] = bonus
        if by_value:
            style[field] = by_value
    for row in content.get("title_styles") or []:
        marker = str(row.get("style") or "")
        if marker not in {"title_question", "title_number", "title_hook_word"}:
            continue
        bonus = _style_bonus(float(row.get("delta_pct") or 0.0))
        if bonus:
            style[marker] = bonus
    return style


def style_bonus_for(segment, style_map) -> float:
    """Selection-score bonus for one segment under a content style map."""
    if not style_map:
        return 0.0
    bonus = 0.0
    for field in ("hook_type", "angle", "topic"):
        value = str(segment.get(field) or "").strip()
        by_value = style_map.get(field) or {}
        if value in by_value:
            bonus += float(by_value[value])
    title = str(segment.get("recommended_title") or segment.get("title") or "").strip()
    if title:
        for marker, present in title_style_flags(title).items():
            if present and style_map.get(marker):
                bonus += float(style_map[marker])
    return round(bonus, 2)


def _load(project_folder: str) -> dict[str, Any]:
    path = os.path.join(project_folder, INSIGHTS_NAME)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def feature_to_component(feature: str) -> str | None:
    mapping = {
        "duration": None,  # handled as a duration bonus, not a score weight
        "hook_strength": "hook",
        "narrative_completeness": "completeness",
        "clarity_score": "clarity",
        "novelty_score": "novelty",
        "selection_score": None,
        "title_quality_score": None,  # applied later as a title boost
    }
    return mapping.get(feature)


def load_weights(project_folder: str | None) -> dict[str, Any]:
    """Return {weights, title_boost, duration_bonus, basis, shifted}.

    Never raises; missing or weak data falls back to the default weights.
    """
    weights = dict(DEFAULT_WEIGHTS)
    result = {
        "weights": weights,
        "title_boost": 0.0,
        "duration_bonus": 0.0,
        "basis": "defaults",
        "shifted": [],
        "samples": 0,
    }
    if not project_folder:
        return result
    insights = _load(project_folder)
    correlations = insights.get("correlations") or {}
    samples = int(insights.get("with_metrics", 0) or 0)
    result["samples"] = samples
    if samples < MIN_SAMPLES:
        return result

    # Content-style learning rides on the same insights file (>=3 measured
    # clips) — it must apply even when no numeric correlation reached the
    # reporting threshold yet.
    style = style_bonuses(insights)
    if style:
        result["style"] = style

    total_shift = 0.0
    for feature, entry in correlations.items():
        component = feature_to_component(feature)
        value = (entry or {}).get("vs_views")
        if component is None or value is None:
            continue
        shift = max(-MAX_SHIFT, min(MAX_SHIFT, 0.15 * float(value)))
        if abs(shift) < 0.01:
            continue
        weights[component] = weights.get(component, 0.0) + shift
        total_shift += shift
        result["shifted"].append(
            {"feature": feature, "component": component, "r": value,
             "shift": round(shift, 3)})

    # Keep the total weight mass at 1.0 so the score scale is unchanged.
    title_quality = correlations.get("title_quality_score") or {}
    title_r = title_quality.get("vs_views")
    if title_r is not None and abs(float(title_r)) >= 0.2:
        result["title_boost"] = round(max(-0.15, min(0.15, 0.2 * float(title_r))), 3)
    duration = correlations.get("duration") or {}
    duration_r = duration.get("vs_views")
    if duration_r is not None and abs(float(duration_r)) >= 0.2:
        result["duration_bonus"] = round(max(-0.15, min(0.15, 0.2 * float(duration_r))), 3)

    drift = sum(weights.values()) - 1.0
    if abs(drift) > 1e-9:
        for key in weights:
            weights[key] = round(weights[key] - drift / len(weights), 4)
    result["weights"] = {key: round(value, 4) for key, value in weights.items()}
    result["basis"] = "performance_insights"
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Show the selection weights learned from performance insights")
    parser.add_argument("--project", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(load_weights(args.project), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
