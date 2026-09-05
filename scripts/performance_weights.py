"""Turn measured performance insights into selection-score weights.

Reads ``performance_insights.json`` (from scripts/performance_loop) and maps
the observed correlations into small, bounded weight adjustments for
``_selection_score`` in create_viral_segments. Weights stay within +/-0.10 so
the editorial core of the score is never dominated by a thin sample, and the
report always states how many published clips the learning rests on.
"""
from __future__ import annotations

import json
import os
from typing import Any

INSIGHTS_NAME = "performance_insights.json"
DEFAULT_WEIGHTS = {
    "virality": 0.45, "hook": 0.20, "completeness": 0.20,
    "clarity": 0.10, "novelty": 0.05,
}
MAX_SHIFT = 0.10
MIN_SAMPLES = 3


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
    if samples < MIN_SAMPLES or not correlations:
        return result

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
