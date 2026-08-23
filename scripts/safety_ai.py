# -*- coding: utf-8 -*-
"""
AI second-pass policy review (YouTube hate-speech / harassment / violence).

The keyword blocklist (safety_filter.py) catches obvious violations. This
module sends the SURVIVING segments to the configured LLM (Gemini or G4F)
for a contextual review — catching things keywords cannot, e.g.
"these people don't deserve to exist" with no slur in it.

Design rules:
* Never blocks the pipeline: any API/parse failure → review is skipped and
  the keyword-filter result stands.
* One batched call for all segments (cheap).
* AI-flagged segments are removed in ``block``/``censor`` modes (a
  context-level violation cannot be fixed by bleeping single words) and
  annotated in ``flag`` mode.
"""

import json
import re

AI_CAPABLE_BACKENDS = {"gemini", "g4f"}

REVIEW_PROMPT_TEMPLATE = """You are a YouTube Trust & Safety reviewer. You review short video clips extracted from a longer video before they are published as YouTube Shorts.

For EACH clip below, decide if publishing it risks violating YouTube policies, especially:
- Hate speech (attacks/dehumanization against protected groups: race, religion, ethnicity, nationality, gender, sexual orientation, disability, ...)
- Harassment or threats against individuals
- Incitement to violence or harm
- Severe profanity / sexual slurs

Context matters: quoting hate speech to condemn it, educational/news/documentary framing, or religious recitation is ALLOWED. Attacking, endorsing or celebrating it is a VIOLATION.

Clips to review (index, title, transcript text):
{clips}

Answer with VALID JSON ONLY — a list with one object per clip, no markdown, no commentary:
[{{"index": 0, "violation": false, "reason": ""}},
 {{"index": 1, "violation": true, "reason": "short reason here"}}]"""


def build_review_prompt(clips):
    """clips: list of {index, title, text}. Returns the prompt string."""
    lines = []
    for c in clips:
        text = (c.get("text") or "").strip()
        if len(text) > 1500:
            text = text[:1500] + " …"
        lines.append("--- CLIP {} | title: {} ---\n{}".format(
            c.get("index"), c.get("title", ""), text))
    return REVIEW_PROMPT_TEMPLATE.format(clips="\n".join(lines))


def parse_review_response(response_text):
    """Extract the JSON list of verdicts from an LLM response.

    Tolerant to markdown fences and surrounding prose. Returns a dict
    {index: {"violation": bool, "reason": str}}.
    """
    if not response_text:
        return {}
    text = str(response_text)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    candidates = []
    fence = re.search(r"```(?:json)?(.*?)```", text, re.DOTALL)
    if fence:
        candidates.append(fence.group(1))
    candidates.append(text)

    for cand in candidates:
        start = cand.find("[")
        if start == -1:
            continue
        try:
            obj, _ = json.JSONDecoder().raw_decode(cand[start:])
            if isinstance(obj, list):
                verdicts = {}
                for item in obj:
                    if not isinstance(item, dict) or "index" not in item:
                        continue
                    try:
                        idx = int(item["index"])
                    except Exception:
                        continue
                    verdicts[idx] = {
                        "violation": bool(item.get("violation", False)),
                        "reason": str(item.get("reason", ""))[:300],
                    }
                return verdicts
        except Exception:
            continue
    return {}


def should_run_ai_review(ai_backend, safety_ai_flag):
    """AI review only makes sense with an API-capable backend."""
    return (safety_ai_flag == "on") and (ai_backend in AI_CAPABLE_BACKENDS)


def review_segments(clips, ai_backend, api_key=None, model_name=None):
    """Run the review. ``clips`` = [{index, title, text}]. Returns
    {index: {violation, reason}} — empty dict on any failure."""
    if not clips:
        return {}
    prompt = build_review_prompt(clips)
    try:
        from scripts import create_viral_segments
        if ai_backend == "gemini":
            response = create_viral_segments.call_gemini(
                prompt, api_key,
                model_name=model_name or "gemini-2.5-flash-lite-preview-09-2025")
        elif ai_backend == "g4f":
            response = create_viral_segments.call_g4f(
                prompt, model_name=model_name or "gpt-4o-mini")
        else:
            return {}
    except Exception as e:
        print(f"[safety-ai] Review call failed (skipped): {e}")
        return {}

    verdicts = parse_review_response(response)
    if not verdicts:
        print("[safety-ai] Could not parse AI review response — skipped.")
    return verdicts


def apply_ai_review(segments, clips, verdicts, mode):
    """Apply AI verdicts to the segment list.

    Returns (kept_segments, ai_report_entries). AI-flagged segments are
    removed in block/censor modes and annotated in flag mode.
    """
    kept = []
    report = []
    for pos, seg in enumerate(segments):
        verdict = verdicts.get(pos) or verdicts.get(str(pos))
        if verdict and verdict.get("violation"):
            report.append({
                "index": pos,
                "title": seg.get("title", ""),
                "start_time": seg.get("start_time"),
                "end_time": seg.get("end_time"),
                "status": "ai_blocked" if mode in ("block", "censor") else "ai_flagged",
                "reason": verdict.get("reason", ""),
            })
            if mode in ("block", "censor"):
                continue  # drop the segment
            seg = dict(seg)
            safety = dict(seg.get("safety", {}))
            safety["ai_flagged"] = True
            safety["ai_reason"] = verdict.get("reason", "")
            seg["safety"] = safety
            kept.append(seg)
        else:
            kept.append(seg)
    return kept, report
