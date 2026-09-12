# -*- coding: utf-8 -*-
"""segment_validator — one reusable final-segment validator (v7.41, spec item F).

Before v7.41 the "is this clip actually publishable?" question was answered in
several places with slightly different rules: ``create_viral_segments.py``
rejected windows through ``_validate_segment_window``, the title stage carried
its own factual validation, and safety/duplication decisions lived in yet other
stages. This module centralizes the FINAL gate for an already-processed
segment dict — the shape produced by ``process_segments`` — so the CLI, the
WebUI and any downstream exporter can ask the same question and get the same
structured answer.

Public API (stable, versioned through ``SEGMENT_VALIDATOR_VERSION``)::

    validate_final_segment(segment, **options) -> dict
    validate_final_segments(segments, **options) -> dict
    is_publishable_segment(segment, **options) -> bool

Design rules:

* **Never raises** for malformed input — anything that is not a ``dict`` is
  treated as an empty segment and every check degrades to a coherent default.
* **Never mutates** the input segment (read-only ``.get`` access throughout).
* ``errors`` decide ``ok``; a warning never fails a segment. Recoverable
  conditions (a transcript-limited short clip, a title flagged for human
  review, a thin transcript) are warnings by design.

Pure stdlib (``math`` + ``re``), fully unit-testable.
"""

from __future__ import annotations

import math
import re

__all__ = [
    "SEGMENT_VALIDATOR_VERSION",
    "validate_final_segment",
    "validate_final_segments",
    "is_publishable_segment",
]

SEGMENT_VALIDATOR_VERSION = "1.0"

# A segment is a duplicate when either signal is set.
_DUPLICATE_CODES = ("semantic_duplicate", "temporal_duplicate")

# Safety status vocabularies (case-insensitive exact match on the normalized
# value). Anything else — including absent — is "unknown" and passes.
_SAFETY_BLOCKED = frozenset({"blocked", "rejected", "fail", "failed", "unsafe"})
_SAFETY_CLEAN = frozenset({"ok", "safe", "passed", "pass", "clean", "approved", "allow", "allowed"})

# Title validation status vocabularies.
_TITLE_VERIFIED = frozenset({"verified", "ok", "valid", "pass", "passed", "approved"})
_TITLE_REVIEW = frozenset({"review", "needs_review", "needs review", "warning", "warn", "uncertain", "pending"})
_TITLE_REJECTED = frozenset({"rejected", "reject", "failed", "fail", "invalid", "blocked"})

# Truncation markers inside ``quality_flags``. Matching is a case-insensitive
# substring test, deliberately liberal because the flags are free text written
# by several stages over time.
_WORD_TRUNCATION_MARKERS = (
    "mid word", "mid-word", "mid_word", "midword",
    "word_boundary", "word boundary", "word-boundary",
)
_SENTENCE_TRUNCATION_MARKERS = (
    "mid_sentence", "mid-sentence", "mid sentence", "midsentence",
    "ends_incomplete", "ends incomplete", "dangling",
)

_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Tolerance for the media-end bound (floating point transcript timings).
_BOUNDS_EPSILON = 1e-6


# ---------------------------------------------------------------------------
# Small coercion helpers (all total: they never raise)
# ---------------------------------------------------------------------------

def _finite_number(value):
    """Return ``value`` as a finite float, or ``None`` when it is not numeric.

    Accepts real numbers and numeric strings; rejects ``None``, ``bool``,
    empty/whitespace strings, NaN and +/-inf.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except (TypeError, ValueError):
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    return number


def _as_dict(value):
    """Return ``value`` when it is a dict, else an empty dict."""
    return value if isinstance(value, dict) else {}


def _issue(code, message, field):
    """Build one structured error/warning entry."""
    return {"code": code, "message": message, "field": field}


def _base_checks():
    """The full ``checks`` contract with coherent unknown defaults."""
    return {
        "start_time_numeric": False,
        "end_time_numeric": False,
        "end_after_start": False,
        "positive_duration": False,
        "duration_within_min": False,
        "duration_within_max": False,
        "within_media_bounds": None,
        "transcript_present": False,
        "no_word_truncation": True,
        "no_sentence_truncation": True,
        "edge_silence_ok": True,
        "title_status": "missing",
        "safety_ok": None,
        "duplicate_ok": True,
    }


def _quality_flags(segment):
    """Normalize ``quality_flags`` to a list of strings (never raises)."""
    raw = segment.get("quality_flags")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple, set)):
        return [str(item) for item in raw if item is not None]
    return []


def _flags_contain(flags, markers):
    """True when any flag contains any marker (case-insensitive substring)."""
    for flag in flags:
        lowered = str(flag).lower()
        if any(marker in lowered for marker in markers):
            return True
    return False


def _normalized_tokens(text):
    """Word tokens of ``text`` (lowercased), used for the lexical-floor check."""
    return _WORD_RE.findall(str(text or "").lower())


def _resolve_transcript(segment, transcript_text):
    """Resolve the transcript text without ever mutating the segment.

    Precedence: explicit argument, ``transcript_text``, the exact
    ``window_analysis.text``, then the raw ``text`` field.
    """
    if transcript_text is not None:
        return str(transcript_text)
    value = segment.get("transcript_text")
    if value is None:
        value = _as_dict(segment.get("window_analysis")).get("text")
    if value is None:
        value = segment.get("text")
    return str(value) if value is not None else ""


def _resolve_title_validation(segment):
    """Return the title-validation dict, or ``None`` when genuinely absent.

    Reads ``segment["title_validation"]`` first, then
    ``title_data["title_validation"]``; the historical
    ``title_data["validation"]`` block shipped by ``title_factual`` is accepted
    as a compatibility fallback.
    """
    direct = segment.get("title_validation")
    if isinstance(direct, dict) and direct:
        return direct
    title_data = _as_dict(segment.get("title_data"))
    for key in ("title_validation", "validation"):
        candidate = title_data.get(key)
        if isinstance(candidate, dict) and candidate:
            return candidate
    return None


def _title_status(validation):
    """Map a title-validation block to verified/review/rejected/missing."""
    if not isinstance(validation, dict):
        return "missing"
    raw = validation.get("status")
    if isinstance(raw, str):
        normalized = raw.strip().lower().replace("-", "_")
        if normalized in _TITLE_VERIFIED:
            return "verified"
        if normalized in _TITLE_REJECTED:
            return "rejected"
        if normalized in _TITLE_REVIEW:
            return "review"
    # Derive from the v7.40 title_factual.validate_title() contract.
    if validation.get("contains_hallucinated_fact"):
        return "rejected"
    if "matches_transcript" in validation:
        if validation.get("excessive_clickbait") or not validation.get("matches_transcript"):
            return "review"
        return "verified"
    return "review"


def _title_reasons(validation):
    """Human-readable reasons backing a rejected title (never empty)."""
    reasons = []
    raw = validation.get("reasons") or validation.get("reason")
    if isinstance(raw, str):
        reasons.append(raw)
    elif isinstance(raw, (list, tuple, set)):
        reasons.extend(str(item) for item in raw if item)
    facts = validation.get("hallucinated_facts")
    if isinstance(facts, (list, tuple, set)) and facts:
        reasons.append("unsupported facts: " + ", ".join(str(fact) for fact in facts))
    if not reasons and validation.get("excessive_clickbait"):
        reasons.append("excessive clickbait")
    if not reasons:
        reasons.append("title failed factual validation")
    return reasons


def _resolve_safety(segment):
    """Return True/False for a recognized safety status, else None (unknown)."""
    value = segment.get("safety_status")
    if value is None:
        value = _as_dict(segment.get("safety")).get("status")
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized in _SAFETY_BLOCKED:
        return False
    if normalized in _SAFETY_CLEAN:
        return True
    return None


def _invalid_segment_result(index, value):
    """Structured result for a non-dict entry encountered by the batch API."""
    return {
        "ok": False,
        "errors": [_issue(
            "invalid_segment",
            "entry {} is not a segment dict (got {})".format(index, type(value).__name__),
            "segment",
        )],
        "warnings": [],
        "checks": _base_checks(),
        "duration": None,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_final_segment(segment, *, min_duration=0.0, max_duration=float("inf"),
                           media_duration=None, transcript_text=None,
                           require_title=True, min_lexical_words=3,
                           max_edge_silence=2.0):
    """Validate one final segment dict and return a structured verdict.

    ``ok`` is True only when ``errors`` is empty; warnings never fail a clip.
    The function never raises and never mutates ``segment``. See the module
    docstring and ``checks`` in the returned dict for the full contract.
    """
    data = segment if isinstance(segment, dict) else {}
    errors = []
    warnings = []
    checks = _base_checks()

    # --- option normalization (never trust the caller blindly) -------------
    min_value = _finite_number(min_duration)
    if min_value is None or min_value < 0:
        min_value = 0.0
    max_value = _finite_number(max_duration)
    if max_value is None or max_value < 0:
        max_value = float("inf")
    try:
        min_words = int(min_lexical_words)
    except (TypeError, ValueError):
        min_words = 3
    if min_words < 0:
        min_words = 0
    edge_limit = _finite_number(max_edge_silence)
    if edge_limit is None or edge_limit < 0:
        edge_limit = 2.0

    # --- timestamps --------------------------------------------------------
    start_present = "start_time" in data and data.get("start_time") is not None
    end_present = "end_time" in data and data.get("end_time") is not None
    start_raw = data.get("start_time")
    end_raw = data.get("end_time")
    start_time = _finite_number(start_raw)
    end_time = _finite_number(end_raw)

    if not start_present:
        errors.append(_issue("missing_start_time", "segment has no start_time", "start_time"))
    elif start_time is None:
        errors.append(_issue(
            "invalid_start_time",
            "start_time is not a finite number: {!r}".format(start_raw),
            "start_time",
        ))
    if not end_present:
        errors.append(_issue("missing_end_time", "segment has no end_time", "end_time"))
    elif end_time is None:
        errors.append(_issue(
            "invalid_end_time",
            "end_time is not a finite number: {!r}".format(end_raw),
            "end_time",
        ))

    checks["start_time_numeric"] = start_time is not None
    checks["end_time_numeric"] = end_time is not None

    # --- ordering and duration --------------------------------------------
    duration = None
    if start_time is not None and end_time is not None:
        duration = end_time - start_time
        checks["end_after_start"] = end_time > start_time
        checks["positive_duration"] = duration > 0
        if end_time <= start_time:
            errors.append(_issue(
                "end_not_after_start",
                "end_time ({:.3f}s) must be after start_time ({:.3f}s)".format(end_time, start_time),
                "end_time",
            ))
        if duration <= 0:
            errors.append(_issue(
                "non_positive_duration",
                "segment duration must be positive, got {:.3f}s".format(duration),
                "duration",
            ))

    # --- min / max duration -----------------------------------------------
    if duration is not None:
        checks["duration_within_min"] = duration >= min_value
        checks["duration_within_max"] = duration <= max_value
        if duration > 0:
            transcript_limited = bool(data.get("transcript_limited")) or bool(data.get("under_min"))
            if duration < min_value:
                if transcript_limited:
                    warnings.append(_issue(
                        "duration_below_min_transcript_limited",
                        "duration {:.2f}s is below the minimum {:.2f}s but the transcript "
                        "is the limit".format(duration, min_value),
                        "duration",
                    ))
                else:
                    errors.append(_issue(
                        "duration_below_min",
                        "duration {:.2f}s is below the minimum {:.2f}s".format(duration, min_value),
                        "duration",
                    ))
            if duration > max_value:
                errors.append(_issue(
                    "duration_above_max",
                    "duration {:.2f}s exceeds the maximum {:.2f}s".format(duration, max_value),
                    "duration",
                ))

    # --- media bounds (only when a positive media duration is known) ------
    media_value = _finite_number(media_duration)
    if media_value is not None and media_value > 0:
        if start_time is None or end_time is None:
            checks["within_media_bounds"] = None
        else:
            bounds_ok = start_time >= 0 and end_time <= media_value + _BOUNDS_EPSILON
            checks["within_media_bounds"] = bounds_ok
            if not bounds_ok:
                field = "start_time" if start_time < 0 else "end_time"
                errors.append(_issue(
                    "out_of_media_bounds",
                    "segment [{:.3f}s, {:.3f}s] is outside media bounds [0s, {:.3f}s]".format(
                        start_time, end_time, media_value),
                    field,
                ))

    # --- transcript --------------------------------------------------------
    text = _resolve_transcript(data, transcript_text)
    checks["transcript_present"] = bool(text.strip())
    if not checks["transcript_present"]:
        errors.append(_issue("empty_transcript", "segment has no transcript text", "transcript_text"))
    elif len(_normalized_tokens(text)) < min_words:
        warnings.append(_issue(
            "short_transcript",
            "transcript has fewer than {} usable word(s)".format(min_words),
            "transcript_text",
        ))

    # --- truncation --------------------------------------------------------
    analysis = _as_dict(data.get("window_analysis"))
    flags = _quality_flags(data)
    starts_mid = analysis.get("starts_mid_sentence") is True
    ends_mid = analysis.get("ends_mid_sentence") is True
    sentence_truncation = starts_mid or ends_mid
    if starts_mid or ends_mid:
        labeled = []
        if starts_mid:
            labeled.append("starts_mid_sentence")
        if ends_mid:
            labeled.append("ends_mid_sentence")
        errors.append(_issue(
            "sentence_truncation",
            "segment is truncated mid-sentence at the {} edge(s)".format(", ".join(labeled)),
            "window_analysis",
        ))
    elif _flags_contain(flags, _SENTENCE_TRUNCATION_MARKERS):
        sentence_truncation = True
        errors.append(_issue(
            "sentence_truncation",
            "quality flag reports a mid-sentence/dangling boundary: {}".format(
                "; ".join(flag for flag in flags
                          if _flags_contain([flag], _SENTENCE_TRUNCATION_MARKERS))),
            "quality_flags",
        ))

    word_truncation = _flags_contain(flags, _WORD_TRUNCATION_MARKERS)
    if word_truncation:
        errors.append(_issue(
            "word_truncation",
            "quality flag reports a word-boundary cut: {}".format(
                "; ".join(flag for flag in flags
                          if _flags_contain([flag], _WORD_TRUNCATION_MARKERS))),
            "quality_flags",
        ))

    checks["no_sentence_truncation"] = not sentence_truncation
    checks["no_word_truncation"] = not word_truncation

    if analysis.get("ends_incomplete") is True:
        warnings.append(_issue(
            "incomplete_ending",
            "segment ends on an incomplete phrase (preposition/conjunction)",
            "window_analysis",
        ))

    # --- edge silence ------------------------------------------------------
    leading = _finite_number(analysis.get("leading_silence"))
    trailing = _finite_number(analysis.get("trailing_silence"))
    leading = leading if leading is not None else 0.0
    trailing = trailing if trailing is not None else 0.0
    edge_values = {"leading_silence": leading, "trailing_silence": trailing}
    excessive = [name for name, value in edge_values.items() if value > edge_limit]
    if excessive:
        checks["edge_silence_ok"] = False
        errors.append(_issue(
            "excessive_edge_silence",
            "edge silence exceeds {:.2f}s: {}".format(
                edge_limit, ", ".join("{}={:.2f}s".format(name, edge_values[name]) for name in excessive)),
            excessive[0],
        ))
    for name, value in edge_values.items():
        if edge_limit > 0 and edge_limit / 2.0 < value <= edge_limit:
            warnings.append(_issue(
                "edge_silence_warning",
                "edge silence is close to the limit: {}={:.2f}s".format(name, value),
                name,
            ))

    # --- title -------------------------------------------------------------
    title_validation = _resolve_title_validation(data)
    title_status = _title_status(title_validation)
    checks["title_status"] = title_status
    if require_title:
        if title_status == "missing":
            errors.append(_issue(
                "title_missing",
                "segment has no title validation data",
                "title_validation",
            ))
        elif title_status == "rejected":
            errors.append(_issue(
                "title_rejected",
                "title was rejected: {}".format("; ".join(_title_reasons(title_validation))),
                "title_validation",
            ))
        elif title_status == "review":
            warnings.append(_issue(
                "title_needs_review",
                "title is factual enough to keep but flagged for human review",
                "title_validation",
            ))

    # --- safety ------------------------------------------------------------
    safety_ok = _resolve_safety(data)
    checks["safety_ok"] = safety_ok
    if safety_ok is False:
        errors.append(_issue(
            "safety_blocked",
            "segment was blocked by the safety stage",
            "safety_status",
        ))

    # --- duplication -------------------------------------------------------
    duplicate_of = data.get("duplicate_of")
    semantic_duplicate = data.get("semantic_duplicate") is True or (
        duplicate_of is not None and str(duplicate_of).strip() != ""
    )
    temporal_duplicate = data.get("temporal_duplicate") is True
    if semantic_duplicate:
        errors.append(_issue(
            "semantic_duplicate",
            "segment is a semantic duplicate of an already selected clip",
            "semantic_duplicate",
        ))
    if temporal_duplicate:
        errors.append(_issue(
            "temporal_duplicate",
            "segment duplicates the time window of an already selected clip",
            "temporal_duplicate",
        ))
    checks["duplicate_ok"] = not (semantic_duplicate or temporal_duplicate)

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "duration": duration,
    }


def validate_final_segments(segments, **kwargs):
    """Validate an iterable of segments and aggregate the verdicts.

    Returns ``{"ok", "results", "invalid_indices", "error_count"}``. Non-dict
    entries are not skipped silently: each one yields an ``invalid_segment``
    error result at its index.
    """
    if segments is None:
        items = []
    elif isinstance(segments, dict):
        items = list(segments.values())
    elif isinstance(segments, (list, tuple)):
        items = list(segments)
    else:
        try:
            items = list(segments)
        except TypeError:
            items = []

    results = []
    invalid_indices = []
    error_count = 0
    for index, item in enumerate(items):
        if isinstance(item, dict):
            result = validate_final_segment(item, **kwargs)
        else:
            result = _invalid_segment_result(index, item)
        results.append(result)
        error_count += len(result["errors"])
        if not result["ok"]:
            invalid_indices.append(index)

    return {
        "ok": not invalid_indices,
        "results": results,
        "invalid_indices": invalid_indices,
        "error_count": error_count,
    }


def is_publishable_segment(segment, **kwargs):
    """Convenience predicate: True when ``validate_final_segment`` has no errors."""
    return validate_final_segment(segment, **kwargs)["ok"]


# Fatal error codes: the window itself cannot be exported at all. Everything
# else (truncation forced by max_duration, edge silence, title needing review,
# safety/duplicate flags) is a manual-review signal, not a discard reason.
FATAL_ERROR_CODES = frozenset({
    "missing_start_time", "missing_end_time", "invalid_start_time",
    "invalid_end_time", "end_not_after_start", "non_positive_duration",
    "duration_below_min", "duration_above_max", "out_of_media_bounds",
    "empty_transcript", "safety_blocked",
})


def annotate_segment_validation(payload, **kwargs):
    """Return a COPY of a segments payload with validation results attached.

    Used at the save/cut/publish boundaries: every segment gains
    ``final_validation`` (structured errors), and a segment with errors is
    flagged ``requires_review`` + ``publish_blocked_reason`` so the publish
    gates refuse it. Fatal errors additionally set ``export_blocked``. The
    caller's payload is never mutated.
    """
    if not isinstance(payload, dict):
        return payload
    segments = payload.get("segments")
    if not isinstance(segments, list):
        return payload
    annotated = dict(payload)
    new_segments = []
    for segment in segments:
        if not isinstance(segment, dict):
            new_segments.append(segment)
            continue
        copy = dict(segment)
        report = validate_final_segment(copy, **kwargs)
        copy["final_validation"] = report
        if not report["ok"]:
            copy["requires_review"] = True
            copy.setdefault("publish_blocked_reason", "manual_review_required")
            fatal = [item for item in report["errors"]
                     if item.get("code") in FATAL_ERROR_CODES]
            if fatal:
                copy["export_blocked"] = True
        new_segments.append(copy)
    annotated["segments"] = new_segments
    return annotated


def publish_block_reasons(segment, **kwargs):
    """Reasons a segment must not be auto-published ([] when publishable).

    Combines the structural validation with the pipeline's own review flags
    (``requires_review`` / ``title_review_required`` / ``export_blocked``).
    """
    reasons = []
    if not isinstance(segment, dict):
        return ["invalid_segment"]
    if segment.get("export_blocked"):
        reasons.append("export_blocked")
    if segment.get("requires_review") or segment.get("title_review_required"):
        reasons.append(segment.get("publish_blocked_reason") or "manual_review_required")
    report = validate_final_segment(segment, **kwargs)
    if not report["ok"]:
        fatal = [item.get("code") for item in report["errors"]
                 if item.get("code") in FATAL_ERROR_CODES]
        if fatal:
            reasons.extend(fatal)
        elif "manual_review_required" not in reasons:
            reasons.append("manual_review_required")
    # Preserve order, drop duplicates.
    seen, unique = set(), []
    for reason in reasons:
        if reason and reason not in seen:
            seen.add(reason)
            unique.append(reason)
    return unique
