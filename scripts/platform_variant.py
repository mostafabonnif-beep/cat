# -*- coding: utf-8 -*-
"""Cross-platform republish variance — TikTok/Reels duplicate-content defense.

Publishing the same rendered clip on YouTube Shorts and then on TikTok is a
legitimate workflow, but each platform fingerprints the content it receives;
a byte-identical (or perceptually near-identical) re-upload is exactly what
"duplicate content" / "unoriginal content" throttling is built to catch.

This module makes the second-platform copy *genuinely editorial-different*
using the deterministic seeded presets already shipped in
``scripts/originality.py``: micro speed shift, horizontal mirror, crop-offset
jitter and a micro color grade. The seed is derived from the clip path +
target platform + number of previous publishes, so:

* the same clip republished to two platforms gets two different variants;
* re-running the pipeline is reproducible for the same inputs;
* a *third* attempt at the same target gets a fresh variant instead of
  silently re-uploading the same bytes.

Everything degrades gracefully: no history, policy off, or an ffmpeg failure
falls back to the original file with an advisory — never a hard block.
"""
from __future__ import annotations

import hashlib
import os
import sys
from typing import Any

try:
    from webui import publish_history  # noqa: F401  (repo-root runs)
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from webui import publish_history  # noqa: F401

VARIANT_POLICIES = ("off", "auto", "always")
# Platforms whose feeds aggressively throttle re-uploads of identical bytes.
VARIANT_PLATFORMS = {"tiktok", "instagram", "reels"}


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_platform(platform: str | None) -> str:
    return str(platform or "").strip().lower()


def is_variant_platform(platform: str | None) -> bool:
    return normalize_platform(platform) in VARIANT_PLATFORMS


def _load_history(project_path: str) -> list[dict[str, Any]]:
    try:
        from webui import publish_history
        return publish_history.load(project_path, limit=2000)
    except Exception:
        return []


def prior_other_platform_publishes(project_path: str, video_path: str,
                                   target_platform: str,
                                   events: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Prior successful publishes of this same clip on *other* platforms.

    Match on either the file fingerprint (rendered bytes) or the clip file
    name (same source window index). YouTube-Success followed by a TikTok
    publish of the same clip is the exact scenario this module addresses.
    """
    target = normalize_platform(target_platform)
    if events is None:
        events = _load_history(project_path)
    base_name = os.path.basename(str(video_path or ""))
    fingerprint = None
    if video_path and os.path.isfile(video_path):
        try:
            from webui import publish_history
            fingerprint = publish_history.file_fingerprint(video_path)
        except Exception:
            fingerprint = None
    matches = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        platform = normalize_platform(event.get("platform"))
        if platform == target or platform not in VARIANT_PLATFORMS | {"youtube", "yt_shorts", "shorts"}:
            continue
        if str(event.get("status") or "") not in {"uploaded", "scheduled"}:
            continue
        same_file = fingerprint is not None and event.get("file_fingerprint") == fingerprint
        same_clip = base_name and event.get("video") == base_name
        if same_file or same_clip:
            matches.append(event)
    return matches


def _previous_target_attempts(project_path: str, video_path: str,
                              target_platform: str,
                              events: list[dict[str, Any]] | None = None) -> int:
    """How many successful publishes this exact clip already had on target."""
    target = normalize_platform(target_platform)
    if events is None:
        events = _load_history(project_path)
    base_name = os.path.basename(str(video_path or ""))
    count = 0
    for event in events or []:
        if not isinstance(event, dict):
            continue
        if (normalize_platform(event.get("platform")) == target
                and str(event.get("status") or "") in {"uploaded", "scheduled"}
                and event.get("video") == base_name):
            count += 1
    return count


def choose_seed(project_path: str, video_path: str, target_platform: str,
                attempt: int = 1) -> int:
    """Deterministic seed for a (clip, platform, attempt) combination."""
    raw = "|".join((os.path.abspath(str(video_path or "")),
                    normalize_platform(target_platform), str(int(attempt))))
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def plan_variant(project_path: str, video_path: str, target_platform: str,
                 policy: str = "auto",
                 events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Pure decision: should this clip get a platform-diversified copy?

    Never touches the disk. Returns a decision dict with the chosen seed and
    preset when ``action == "variate"`` so callers can preview (dry-run) or
    apply the transform themselves.
    """
    target = normalize_platform(target_platform)
    base = {
        "path": video_path, "action": "none", "platform": target,
        "policy": str(policy or "auto").strip().lower(), "seed": None,
        "transforms": [], "checked_at": _now(),
    }
    if not is_variant_platform(target):
        base["reason"] = "platform {} does not need cross-platform variance".format(target or "?")
        return base
    if not video_path or not os.path.isfile(video_path):
        base["reason"] = "clip file not found"
        return base
    policy = base["policy"]
    if policy not in VARIANT_POLICIES:
        base["reason"] = "unknown variant_policy {!r} (use off|auto|always)".format(policy)
        return base
    if policy == "off":
        base["reason"] = "variant_policy=off"
        return base

    siblings = prior_other_platform_publishes(project_path, video_path, target, events)
    if policy == "auto" and not siblings:
        base["reason"] = "no prior publish of this clip on another platform — original is fine"
        return base
    attempt = 1 + _previous_target_attempts(project_path, video_path, target, events)
    seed = choose_seed(project_path, video_path, target, attempt)
    try:
        from scripts import originality
        preset = originality.build_preset(seed)
    except Exception as error:  # pragma: no cover - optional dependency
        base["reason"] = "preset builder unavailable ({}); uploading the original".format(error)
        return base
    reason = ("republishing a clip already published on {} — diversifying the copy".format(
        ", ".join(sorted({normalize_platform(item.get("platform")) for item in siblings}))) if siblings
        else "variant_policy=always — platform-appropriate copy for {}".format(target))
    base.update({
        "action": "variate", "reason": reason, "seed": seed, "preset": preset,
        "siblings": len(siblings), "attempt": attempt,
    })
    return base


def maybe_variant(project_path: str, video_path: str, target_platform: str,
                  policy: str = "auto", ffmpeg: str = "ffmpeg",
                  variant_dir: str | None = None,
                  events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Apply ``plan_variant`` when needed and render the variant file.

    Non-fatal: any transform failure falls back to the original path with an
    explanatory ``reason`` — publishing is never hard-blocked by this module.
    """
    decision = plan_variant(project_path, video_path, target_platform, policy, events)
    if decision["action"] != "variate":
        return decision
    try:
        from scripts import originality
        directory = variant_dir or os.path.join(str(project_path), "variants")
        os.makedirs(directory, exist_ok=True)
        stem, ext = os.path.splitext(os.path.basename(str(video_path)))
        seed = int(decision["seed"])
        output_path = os.path.join(directory, "{}__{}_{}.mp4".format(stem, target_platform, seed))
        result = originality.transform_with_seed(
            str(video_path), output_path, seed=seed, preset=decision.get("preset"),
            ffmpeg=ffmpeg)
        if not result.get("ok"):
            decision["reason"] = "variant transform reported failure: {}".format(result)
            decision["action"] = "none"
            decision["path"] = video_path
            return decision
        if not os.path.isfile(output_path):
            decision["reason"] = "variant transform produced no output file"
            decision["action"] = "none"
            decision["path"] = video_path
            return decision
        decision["path"] = output_path
        decision["transforms"] = result.get("transforms") or []
        return decision
    except Exception as error:  # pragma: no cover - depends on local ffmpeg
        decision["reason"] = "variant transform unavailable ({}); uploading the original".format(error)
        decision["action"] = "none"
        decision["path"] = video_path
        return decision
