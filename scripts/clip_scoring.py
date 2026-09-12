# -*- coding: utf-8 -*-
"""clip_scoring — centralized, configurable clip-ranking system (v7.40).

One place owns the selection weights. Every candidate clip receives a 0-100
score for each factor and a transparent weighted final score:

    final_score =
        0.20 * hook_strength +
        0.18 * standalone_context +
        0.15 * emotional_value +
        0.12 * information_density +
        0.12 * completion_score +
        0.10 * transcript_alignment +
        0.05 * audio_quality +
        0.04 * visual_quality +
        0.04 * title_relevance -
        repetition_penalty -
        safety_penalty

Configuration (documented constants + env overrides, nothing scattered):
* ``DEFAULT_SELECTION_WEIGHTS`` — the single weight object.
* ``VIRALCUTTER_SELECTION_WEIGHTS`` — optional JSON object merged over the
  defaults (unknown keys rejected, values clamped to [0, 1]).
* ``VIRALCUTTER_MIN_FINAL_SCORE`` — editorial floor (default 0 = disabled);
  candidates below it are dropped so a weak video yields FEWER clips instead
  of padded weak ones.

All factor helpers are deterministic and side-effect free so scores are
reproducible in tests and in the WebUI review table.
"""

from __future__ import annotations

import json
import os
import re

# Bump when the scoring formula/weights change: embedded in the segments
# config fingerprint so old results are regenerated, never silently reused.
SCORING_VERSION = "7.40.1"

# The single source of truth for selection weights (sums to 1.00).
DEFAULT_SELECTION_WEIGHTS = {
    "hook_strength": 0.20,
    "standalone_context": 0.18,
    "emotional_value": 0.15,
    "information_density": 0.12,
    "completion_score": 0.12,
    "transcript_alignment": 0.10,
    "audio_quality": 0.05,
    "visual_quality": 0.04,
    "title_relevance": 0.04,
}

# Penalty factors are subtracted (not weighted): they are computed per
# candidate by the pipeline (semantic/temporal repetition, safety flags).
PENALTY_FACTORS = ("repetition_penalty", "safety_penalty")

FACTOR_NAMES = tuple(DEFAULT_SELECTION_WEIGHTS) + PENALTY_FACTORS

# Neutral default for factors with no measurable signal in the current
# environment (e.g. visual quality without frame analysis): documented, not
# hidden — candidates are neither rewarded nor punished.
NEUTRAL_SCORE = 80.0


def _bounded(value, default=0.0):
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return float(default)


def load_selection_weights(env_var="VIRALCUTTER_SELECTION_WEIGHTS"):
    """Return the active weights: defaults merged with an optional JSON override.

    The override is a JSON object such as ``{"hook_strength": 0.25}``; unknown
    keys are ignored and values are clamped to [0, 1]. Never raises — a broken
    override falls back to the documented defaults.
    """
    weights = dict(DEFAULT_SELECTION_WEIGHTS)
    raw = os.getenv(env_var, "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    if key not in weights:
                        continue
                    try:
                        weights[key] = max(0.0, min(1.0, float(value)))
                    except (TypeError, ValueError):
                        continue
        except (ValueError, TypeError):
            pass
    return weights


def min_final_score(default=0.0):
    """Editorial floor for the final score (env VIRALCUTTER_MIN_FINAL_SCORE).

    Default 0 keeps legacy behaviour (no extra dropping); when set (e.g. 30),
    candidates below the floor are rejected so a thin video produces fewer,
    stronger clips instead of padded weak ones.
    """
    try:
        return max(0.0, min(100.0, float(os.getenv("VIRALCUTTER_MIN_FINAL_SCORE", "").strip())))
    except (TypeError, ValueError):
        return float(default)


def compute_final_score(factors, weights=None):
    """Weighted sum of factors minus penalties, bounded to [0, 100].

    ``factors`` maps factor name -> 0-100 score; missing factors fall back to
    ``NEUTRAL_SCORE`` for positive factors and 0 for penalties. Returns a
    rounded float (1 decimal).
    """
    weights = weights or DEFAULT_SELECTION_WEIGHTS
    total = 0.0
    for name, weight in weights.items():
        total += float(weight) * _bounded(factors.get(name), NEUTRAL_SCORE)
    for name in PENALTY_FACTORS:
        total -= _bounded(factors.get(name), 0.0)
    return round(max(0.0, min(100.0, total)), 1)


# ---------------------------------------------------------------------------
# Deterministic factor helpers (0-100). Each takes plain data so it can be
# unit-tested without the pipeline.
# ---------------------------------------------------------------------------

# Hook words matched on word boundaries (MSA + Darija + English).
_HOOK_WORDS_RE = re.compile(
    r"(?<!\w)(?:كيف|كيفاش|لماذا|علاش|واش|شنو|اشنو|شحال|سر|أسرار|خطأ|أخطاء|حقيقة|تجربتي"
    r"|how|why|secret|mistake|mistakes|truth|never|always|stop|warning)(?!\w)",
    re.IGNORECASE)

_QUESTION_ENDINGS = ("?", "؟")


def hook_strength_heuristic(first_sentence, fallback=50.0):
    """Score the opening line: question/number/hook-word openers pull viewers in.

    Used only when the AI shipped no genuine ``hook_strength`` self-evaluation.
    """
    text = str(first_sentence or "").strip()
    if not text:
        return float(fallback)
    score = 45.0
    if text.rstrip().endswith(_QUESTION_ENDINGS):
        score += 22.0
    if re.search(r"\d", text):
        score += 12.0
    if _HOOK_WORDS_RE.search(text):
        score += 15.0
    words = text.split()
    if 3 <= len(words) <= 12:
        score += 8.0  # punchy opening line
    return round(max(0.0, min(100.0, score)), 1)


_EMOTION_WORDS_RE = re.compile(
    r"(?<!\w)(?:خطر|خوف|صدم|مفاجأ|عجيب|غريب|مذهل|رائع|رهيب|مؤلم|سعيد|حزين|مستحيل|مجنون"
    r"|ضحك|بكاء|غضب|حب|كره"
    r"|shocking|amazing|crazy|insane|unbelievable|fear|love|hate|pain|wow|never)(?!\w)",
    re.IGNORECASE)


def emotional_value_heuristic(text):
    """Emotional charge of the spoken window: emotion words + expressive punctuation."""
    text = str(text or "")
    if not text.strip():
        return 0.0
    score = 35.0
    hits = len(_EMOTION_WORDS_RE.findall(text))
    score += min(35.0, hits * 12.0)
    exclaims = text.count("!") + text.count("؟") + text.count("?")
    score += min(20.0, exclaims * 7.0)
    return round(max(0.0, min(100.0, score)), 1)


def information_density_score(word_count, duration, unique_ratio=None):
    """Words-per-second in the human speech sweet spot, adjusted by vocabulary variety."""
    try:
        duration = float(duration)
        word_count = float(word_count)
    except (TypeError, ValueError):
        return 0.0
    if duration <= 0 or word_count <= 0:
        return 0.0
    wps = word_count / duration
    if 1.2 <= wps <= 3.2:
        base = 100.0
    elif wps < 1.2:
        base = max(0.0, 100.0 * (wps / 1.2))
    else:
        base = max(20.0, 100.0 - (wps - 3.2) * 40.0)
    if unique_ratio is not None:
        try:
            base *= 0.7 + 0.3 * max(0.0, min(1.0, float(unique_ratio)))
        except (TypeError, ValueError):
            pass
    return round(max(0.0, min(100.0, base)), 1)


def completion_score(analysis):
    """Boundary completeness from a transcript_window.analyze_window() result."""
    if not isinstance(analysis, dict):
        return 0.0
    score = 0.0
    if not analysis.get("starts_mid_sentence"):
        score += 40.0
    if not analysis.get("ends_mid_sentence"):
        score += 40.0
    if analysis.get("ends_with_terminal_punctuation"):
        score += 20.0
    return round(score, 1)


def standalone_context_score(analysis):
    """Penalty-based context independence from a window analysis."""
    if not isinstance(analysis, dict):
        return NEUTRAL_SCORE
    score = 100.0
    if analysis.get("starts_with_connector"):
        score -= 35.0
    if analysis.get("starts_mid_sentence"):
        score -= 25.0
    if analysis.get("opens_with_unresolved_reference"):
        score -= 15.0
    if not analysis.get("word_count"):
        score -= 60.0
    return round(max(0.0, min(100.0, score)), 1)


def transcript_alignment_score(analysis, edge_match=1.0):
    """How well the window matches the real transcript (coverage + edge fidelity)."""
    if not isinstance(analysis, dict):
        return 0.0
    coverage = analysis.get("speech_coverage")
    try:
        coverage = max(0.0, min(1.0, float(coverage)))
    except (TypeError, ValueError):
        coverage = 0.0
    try:
        edge = max(0.0, min(1.0, float(edge_match)))
    except (TypeError, ValueError):
        edge = 1.0
    return round(max(0.0, min(100.0, coverage * 70.0 + edge * 30.0)), 1)


def audio_quality_proxy(analysis):
    """Silence-based audio proxy (no DSP here): trims and dead air hurt retention.

    Real loudness/clipping QC lives in scripts/audio_qc.py downstream; this is
    the deterministic selection-time proxy.
    """
    if not isinstance(analysis, dict):
        return NEUTRAL_SCORE
    score = 100.0
    leading = float(analysis.get("leading_silence") or 0.0)
    trailing = float(analysis.get("trailing_silence") or 0.0)
    silence_ratio = float(analysis.get("silence_ratio") or 0.0)
    score -= min(30.0, max(0.0, leading - 0.3) * 15.0)
    score -= min(30.0, max(0.0, trailing - 0.4) * 15.0)
    score -= min(40.0, max(0.0, silence_ratio - 0.35) * 80.0)
    return round(max(0.0, min(100.0, score)), 1)


def visual_quality_score(segment=None):
    """Neutral visual baseline; small bonus when the cut was scene-snapped."""
    score = NEUTRAL_SCORE
    if isinstance(segment, dict) and segment.get("scene_snapped"):
        score += 10.0
    return round(score, 1)
