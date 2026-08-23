# -*- coding: utf-8 -*-
"""
Censor Engine — "Bleep" mode for the ViralCutter safety filter.

Instead of dropping a whole segment because of one policy-violating word,
this engine mutes ONLY the offending words in the audio and masks them in
the subtitles, keeping the viral clip alive.

How it fits in the pipeline
---------------------------
1. ``cut_segments`` produces ``cuts/XXX_*_original_scale.mp4`` and
   ``subs/XXX_*_processed.json`` (word-level, cut-relative time).
2. ``censor_project`` (called right after cutting when
   ``--safety-mode censor``) then:
     * locates offending words with absolute timestamps from the WhisperX
       ``input.json`` (word-level),
     * converts them to cut-relative spans,
     * mutes those spans in each cut video's audio track
       (``volume=0:enable='between(t,a,b)'`` — pure ffmpeg, no re-encode of
       video),
     * masks the same words as ``████`` in the subtitle JSONs,
     * writes ``censor_map.json`` documenting every muted word.
3. Downstream steps (face crop edit, subtitle burn) reuse the cut audio and
   subtitle JSONs, so the censoring survives to the final render.
"""

import glob
import json
import os
import subprocess

from scripts.safety_filter import (
    SEVERITY_ORDER,
    _build_index,
    load_custom_terms,
    load_remote_terms,
    normalize_text,
)

# seconds of padding added around each muted word (word timestamps from
# WhisperX are tight; a little margin avoids leaking syllables)
PAD_SECONDS = 0.08
MASK_TEXT = "████"
CENSOR_MAP_FILENAME = "censor_map.json"


# ---------------------------------------------------------------------------
# Word-level transcript
# ---------------------------------------------------------------------------

def load_word_transcript(project_folder):
    """Return the word list from the WhisperX input.json: [{word,start,end}].

    Falls back to [] when the file is missing or has no word-level data
    (e.g. transcription came from YouTube subtitles — in that case bleep
    mode cannot locate words and stays a no-op).
    """
    path = os.path.join(project_folder, "input.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[censor] Could not read input.json: {e}")
        return []

    words = []
    for seg in data.get("segments", []):
        for w in seg.get("words", []) or []:
            try:
                words.append({
                    "word": str(w.get("word", w.get("text", ""))),
                    "start": float(w.get("start", 0)),
                    "end": float(w.get("end", 0)),
                })
            except Exception:
                continue
    words.sort(key=lambda w: w["start"])
    return words


# ---------------------------------------------------------------------------
# Span computation
# ---------------------------------------------------------------------------

def _word_matches_blocklist(norm_words, index, min_rank, allow_norms):
    """Yield (word_start_idx, word_end_idx, entry) for each blocklist hit.

    ``norm_words`` is the list of normalized word strings. Multi-token
    blocklist phrases match across consecutive words.
    """
    n = len(norm_words)
    for entry in index:
        if SEVERITY_ORDER.get(entry["severity"], 3) < min_rank:
            continue
        if entry["norm"] in allow_norms:
            continue
        tokens = entry["tokens"]
        k = len(tokens)
        for i in range(0, n - k + 1):
            if norm_words[i:i + k] == tokens:
                yield i, i + k - 1, entry


def compute_censor_spans(segment, words, index=None, min_severity="medium",
                         allow_terms=None, pad=PAD_SECONDS):
    """Find offending word spans (ABSOLUTE time) inside one viral segment.

    Returns a list of dicts: {start, end, term, category, severity}.
    Overlapping spans are merged.
    """
    if index is None:
        index = _build_index()
    allow_norms = {normalize_text(t) for t in (allow_terms or [])}

    seg_start = float(segment.get("start_time", 0) or 0)
    seg_end = float(segment.get("end_time", seg_start) or seg_start)
    min_rank = SEVERITY_ORDER.get(min_severity, 2)

    # words whose center falls inside the segment window
    window = [w for w in words
              if (w["start"] + w["end"]) / 2.0 >= seg_start - 0.05
              and (w["start"] + w["end"]) / 2.0 <= seg_end + 0.05]
    if not window:
        return []

    norm_words = [normalize_text(w["word"]).replace(" ", "") for w in window]

    spans = []
    for i, j, entry in _word_matches_blocklist(norm_words, index, min_rank, allow_norms):
        start = max(seg_start, window[i]["start"] - pad)
        end = min(seg_end, window[j]["end"] + pad)
        if end <= start:
            continue
        spans.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "term": entry["term"],
            "category": entry["category"],
            "severity": entry["severity"],
        })

    # merge overlapping/touching spans
    spans.sort(key=lambda s: s["start"])
    merged = []
    for span in spans:
        if merged and span["start"] <= merged[-1]["end"] + 0.01:
            merged[-1]["end"] = max(merged[-1]["end"], span["end"])
            merged[-1]["term"] += " + " + span["term"]
        else:
            merged.append(dict(span))
    return merged


def spans_to_relative(spans, segment_start, segment_end=None):
    """Convert absolute spans to cut-relative time (clamped to >= 0)."""
    rel = []
    for s in spans:
        start = max(0.0, s["start"] - segment_start)
        end = max(0.0, s["end"] - segment_start)
        if segment_end is not None:
            limit = max(0.0, segment_end - segment_start)
            start = min(start, limit)
            end = min(end, limit)
        if end > start:
            item = dict(s)
            item["start"] = round(start, 3)
            item["end"] = round(end, 3)
            rel.append(item)
    return rel


# ---------------------------------------------------------------------------
# Audio muting (ffmpeg)
# ---------------------------------------------------------------------------

def build_mute_filter(spans):
    """Build the ffmpeg audio filter string that zeroes the given spans."""
    parts = ["between(t,{:.3f},{:.3f})".format(s["start"], s["end"]) for s in spans]
    return "volume=0:enable='{}'".format("+".join(parts))


def apply_audio_censor(video_path, spans, i18n=lambda k: k):
    """Mute the given (cut-relative) spans in a video file, in place."""
    if not spans or not os.path.exists(video_path):
        return False

    af = build_mute_filter(spans)
    tmp_path = video_path + ".censor_tmp.mp4"
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", video_path,
        "-af", af,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        tmp_path,
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
            os.replace(tmp_path, video_path)
            return True
        print(i18n("[censor] ffmpeg produced no output for {}").format(video_path))
    except subprocess.CalledProcessError as e:
        print(i18n("[censor] ffmpeg failed for {}: {}").format(video_path, (e.stderr or "")[-300:]))
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return False


# ---------------------------------------------------------------------------
# Subtitle masking
# ---------------------------------------------------------------------------

def mask_subtitle_json(json_path, spans, mask=MASK_TEXT):
    """Replace offending words with the mask inside a cut subtitle JSON.

    Returns the number of masked words.
    """
    if not spans or not os.path.exists(json_path):
        return 0
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[censor] Could not read subtitle JSON {json_path}: {e}")
        return 0

    masked = 0
    for seg in data.get("segments", []):
        for w in seg.get("words", []) or []:
            try:
                w_start = float(w.get("start", 0))
                w_end = float(w.get("end", 0))
            except Exception:
                continue
            for span in spans:
                # overlap test (cut-relative time on both sides)
                if w_end > span["start"] - 0.02 and w_start < span["end"] + 0.02:
                    if "word" in w:
                        w["word"] = mask
                    elif "text" in w:
                        w["text"] = mask
                    masked += 1
                    break

    if masked:
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[censor] Could not write subtitle JSON {json_path}: {e}")
            return 0
    return masked


# ---------------------------------------------------------------------------
# Project-level orchestration
# ---------------------------------------------------------------------------

def censor_project(project_folder, viral_segments, min_severity="medium",
                   extra_terms_path=None, i18n=lambda k: k):
    """Apply bleep-censoring to every cut segment of a project.

    Returns the censor map (also written to ``censor_map.json``).
    """
    segments = (viral_segments or {}).get("segments", [])
    if not segments:
        return {"segments": {}}

    words = load_word_transcript(project_folder)
    if not words:
        print(i18n("[censor] No word-level transcript (input.json) found — bleep mode cannot locate words. Skipping censoring."))
        return {"segments": {}, "error": "no_word_transcript"}

    custom = load_custom_terms(project_folder, extra_terms_path)
    index = _build_index(custom.get("extra_terms", []) + load_remote_terms())
    allow_terms = custom.get("allow_terms", [])

    cuts_folder = os.path.join(project_folder, "cuts")
    subs_folder = os.path.join(project_folder, "subs")

    censor_map = {"mode": "censor", "min_severity": min_severity, "segments": {}}
    total_muted = 0

    for i, segment in enumerate(segments):
        spans_abs = compute_censor_spans(
            segment, words, index=index,
            min_severity=min_severity, allow_terms=allow_terms)
        if not spans_abs:
            continue

        seg_start = float(segment.get("start_time", 0) or 0)
        seg_end = float(segment.get("end_time", seg_start) or seg_start)
        spans_rel = spans_to_relative(spans_abs, seg_start, seg_end)
        if not spans_rel:
            continue

        entry = {
            "title": segment.get("title", f"Segment_{i}"),
            "start_time": seg_start,
            "end_time": seg_end,
            "spans": spans_rel,
            "muted_words": len(spans_rel),
        }

        # 1) mute audio in the cut video
        cut_matches = sorted(glob.glob(os.path.join(
            cuts_folder, "{:03d}_*_original_scale.mp4".format(i))))
        if cut_matches:
            entry["video_censored"] = apply_audio_censor(cut_matches[0], spans_rel, i18n=i18n)
        else:
            entry["video_censored"] = False
            print(i18n("[censor] Cut video not found for segment {} — audio not muted.").format(i))

        # 2) mask subtitle JSON
        sub_matches = sorted(glob.glob(os.path.join(
            subs_folder, "{:03d}_*_processed.json".format(i))))
        entry["subtitle_masked"] = 0
        for sub_path in sub_matches:
            entry["subtitle_masked"] += mask_subtitle_json(sub_path, spans_rel)

        censor_map["segments"][str(i)] = entry
        total_muted += len(spans_rel)
        terms = ", ".join(sorted({s["term"] for s in spans_rel}))
        print(i18n("[censor] Segment {} ({}): muted {} word(s) — {}").format(
            i, entry["title"], len(spans_rel), terms))

    censor_map["total_muted_words"] = total_muted
    try:
        map_path = os.path.join(project_folder, CENSOR_MAP_FILENAME)
        with open(map_path, "w", encoding="utf-8") as f:
            json.dump(censor_map, f, ensure_ascii=False, indent=2)
        print(i18n("[censor] Censor map saved to {}").format(map_path))
    except Exception as e:
        print(f"[censor] Could not save censor map: {e}")

    if total_muted == 0:
        print(i18n("[censor] No policy-violating words found — nothing to mute ✔"))

    return censor_map
