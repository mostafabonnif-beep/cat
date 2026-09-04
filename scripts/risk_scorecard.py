# -*- coding: utf-8 -*-
"""
Risk Scorecard — per-clip YouTube compliance report.

Turns every cut clip into a mini trust-and-safety report card so nothing
risky gets published silently:

    hate_speech : the text layers (keyword filter + AI review) verdicts
    visual      : frame-level red flags (letterboxed/pillarboxed look,
                  optional local ONNX nudity model if installed)
    reuse       : how similar the final clip still is to the raw source
                  window — the "reused content" (Content ID / monetization)
                  risk. This is THE big one for channels that cut videos.
    monetization: advertiser-friendliness (profanity inside the first 7s
                  = limited ads, even without a strike)

``compute_transformation_score`` compares frames of the final clip against
frames of the matching source window using a tiny perceptual hash (dHash).
No extra Python dependencies: ffmpeg scales to 9x8 grayscale and we read
raw bytes from a pipe.
"""

import glob
import json
import os
import subprocess

from scripts import visual_check as visual_check_module
from scripts.safety_filter import find_matches
from scripts import content_ledger
from scripts import provenance

SCORECARD_FILENAME = "risk_scorecard.json"
PUBLISH_BLOCKLIST_FILENAME = "publish_blocklist.json"
FIRST_SECONDS_PROFANITY = 7.0

# reuse score >= this → the clip is effectively a repost of the source
HIGH_REUSE_THRESHOLD = 70.0
VISUAL_GRAPHIC_THRESHOLD = 70.0


# ---------------------------------------------------------------------------
# ffmpeg frame helpers (pure stdlib)
# ---------------------------------------------------------------------------

def _grab_gray_frame(video_path, at_seconds, width=9, height=8):
    """Sample one grayscale frame (width*height bytes) via ffmpeg rawvideo pipe."""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", "{:.3f}".format(at_seconds), "-i", video_path,
        "-frames:v", "1", "-vf", "scale={}:{},format=gray".format(width, height),
        "-f", "rawvideo", "pipe:1",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=60)
        raw = res.stdout
        if len(raw) < width * height:
            return None
        return raw[:width * height]
    except Exception:
        return None


def _dhash(frame, width=9, height=8):
    """Perceptual hash: 64-bit int from adjacent-pixel brightness."""
    h = 0
    for row in range(height):
        for col in range(width - 1):
            left = frame[row * width + col]
            right = frame[row * width + col + 1]
            h = (h << 1) | (1 if left > right else 0)
    return h


def _hamming(a, b):
    return bin(a ^ b).count("1")


def frame_similarity(video_a, video_b, sample_points, other_points=None):
    """Compare frames at independent timestamps in two video files."""
    left_points = list(sample_points or [])
    right_points = list(other_points) if other_points is not None else left_points
    if len(left_points) != len(right_points):
        raise ValueError("sample_points and other_points must have equal length")
    if not left_points:
        return None
    hashes = []
    for left_point, right_point in zip(left_points, right_points):
        ha = _grab_gray_frame(video_a, left_point)
        hb = _grab_gray_frame(video_b, right_point)
        if ha is None or hb is None:
            continue
        hashes.append(1.0 - _hamming(_dhash(ha), _dhash(hb)) / 64.0)
    if not hashes:
        return None
    return round(100.0 * sum(hashes) / len(hashes), 1)


def _letterbox_ratio(video_path, at_seconds):
    """Detect letterbox/pillarbox black bars on a frame (repurposed look).

    Returns a fraction of the frame that is dead black bars (0..1).
    """
    frame = _grab_gray_frame(video_path, at_seconds, width=64, height=36)
    if frame is None:
        return 0.0
    w, h = 64, 36

    def row_mean(r):
        row = frame[r * w:(r + 1) * w]
        return sum(row) / len(row)

    def col_mean(c):
        return sum(frame[r * w + c] for r in range(h)) / h

    top = sum(row_mean(r) for r in range(3)) / 3
    bottom = sum(row_mean(r) for r in range(h - 3, h)) / 3
    mid = sum(row_mean(r) for r in range(h // 2 - 2, h // 2 + 2)) / 4

    def dark(v):
        return v < 20.0

    bars = 0.0
    if mid > 35.0 and dark(top) and dark(bottom):
        bars += 0.35
    left = sum(col_mean(c) for c in range(3)) / 3
    right = sum(col_mean(c) for c in range(w - 3, w)) / 3
    mid_col = sum(col_mean(c) for c in range(w // 2 - 2, w // 2 + 2)) / 4
    if mid_col > 35.0 and dark(left) and dark(right):
        bars += 0.35
    return round(bars, 2)


# ---------------------------------------------------------------------------
# Text signals
# ---------------------------------------------------------------------------

def profanity_in_first_seconds(segment, words, seconds=FIRST_SECONDS_PROFANITY):
    """Any policy-violating word inside the first N seconds of the clip?

    Returns (any_offense, profanity_only_list) where profanity_only_list are
    the matched terms with category in {profanity, harassment} (the ones that
    mainly hurt ad revenue rather than causing strikes).
    """
    seg_start = float(segment.get("start_time", 0) or 0)
    window_end = seg_start + seconds
    in_window = []
    for w in words:
        try:
            ws = float(w["start"])
        except Exception:
            continue
        if ws >= seg_start - 0.05 and ws <= window_end:
            in_window.append(w["word"])
        elif ws > window_end:
            break
    if not in_window:
        return False, []

    text = " ".join(in_window)
    matches = find_matches(text, min_severity="low")
    if not matches:
        return False, []
    profanity = [m for m in matches if m["category"] in ("profanity", "harassment")]
    return True, profanity


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------

def _find_source_video(project_folder):
    for name in ("input.mp4", "input_video.mp4"):
        p = os.path.join(project_folder, name)
        if os.path.isfile(p):
            return p

    # New local projects keep the original file outside VIRALS and store only
    # its path in project_manifest.json. Resolve it read-only for risk analysis.
    manifest_path = os.path.join(project_folder, "project_manifest.json")
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        source = manifest.get("source") or {}
        if isinstance(source, dict):
            for key in ("path", "local_path", "original_path"):
                candidate = source.get(key)
                if isinstance(candidate, str) and os.path.isfile(candidate):
                    return os.path.abspath(candidate)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return None


def _find_clip_video(project_folder, index):
    """Prefer the final edited video, fall back to the raw cut."""
    final = sorted(glob.glob(os.path.join(
        project_folder, "final", "*{0:03d}*.mp4".format(index)))) + sorted(
        glob.glob(os.path.join(project_folder, "final",
                               "final-output{0:03d}_processed.mp4".format(index))))
    if final:
        return final[0]
    cuts = sorted(glob.glob(os.path.join(
        project_folder, "cuts", "{0:03d}_*_original_scale.mp4".format(index))))
    return cuts[0] if cuts else None


def _load_words(project_folder):
    path = os.path.join(project_folder, "input.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    words = []
    for seg in data.get("segments", []):
        for w in seg.get("words", []) or []:
            try:
                words.append({"word": str(w.get("word", "")),
                              "start": float(w.get("start", 0)),
                              "end": float(w.get("end", 0))})
            except Exception:
                continue
    words.sort(key=lambda w: w["start"])
    return words


def score_segment(segment, index, project_folder, words, source_video,
                  visual_model_path=None, visual_classifier=None, safety_entry=None,
                  provenance_entry=None, visual_frames=4, visual_threshold=VISUAL_GRAPHIC_THRESHOLD):
    """Compute the risk axes for one segment. Returns a dict (never raises)."""
    entry = {
        "index": index,
        "title": segment.get("title", f"Segment_{index}"),
        "start_time": segment.get("start_time"),
        "end_time": segment.get("end_time"),
        "axes": {"provenance": provenance_entry or {}},
        "overall": "unknown",
        "overall_score": 0,
    }
    seg_start = float(segment.get("start_time", 0) or 0)
    seg_end = float(segment.get("end_time", seg_start) or seg_start)
    duration = max(0.0, seg_end - seg_start)

    # --- text axis (recap of keyword layer + first-N-seconds monetization) ---
    text_scores = {"hate_speech": 0, "first7s": 0, "first7_profanity": 0}
    semantic = (safety_entry or {}).get("semantic") or {}
    semantic_action = semantic.get("action", "allow")
    semantic_score = {"allow": 0, "review": 75, "block": 100}.get(semantic_action, 0)
    text_scores["semantic_policy"] = semantic_score
    text_scores["hate_speech"] = max(text_scores["hate_speech"], semantic_score)
    any_off, profanity = profanity_in_first_seconds(segment, words)
    if any_off:
        text_scores["first7s"] = 60 if profanity else 80
    if profanity:
        text_scores["first7_profanity"] = 50
        text_scores["first7s"] = max(text_scores["first7s"], 50)
    entry["axes"]["text"] = text_scores

    # --- reuse / transformation axis (the Content ID + reused-content risk) ---
    reuse = {
        "similarity": None,
        "letterboxed": 0.0,
        "clip_sample_points": [],
        "source_sample_points": [],
        "score": 0,
    }
    clip = _find_clip_video(project_folder, index)
    if clip and source_video and os.path.exists(clip):
        points = [0.15, 0.5, 0.85]
        clip_points = [duration * f for f in points]
        source_points = [seg_start + duration * f for f in points]
        sim = frame_similarity(clip, source_video, clip_points, source_points)
        if sim is not None:
            reuse["similarity"] = sim
            reuse["clip_sample_points"] = clip_points
            reuse["source_sample_points"] = source_points
            reuse["letterboxed"] = _letterbox_ratio(clip, 0.5 * duration)
            score = 0.75 * sim + 15.0 * reuse["letterboxed"]
            if duration > 60:
                score += 10  # long raw excerpts are riskier
            reuse["score"] = round(min(100.0, score), 1)
    entry["axes"]["reuse"] = reuse

    # --- visual axis (graphic content; optional local ONNX classifier) ---
    visual = {
        "letterboxed": reuse["letterboxed"],
        "model": None,
        "score": 0,
        "available": False,
        "status": "not_configured",
        "error": None,
    }
    if visual_model_path and os.path.exists(visual_model_path):
        visual["model"] = os.path.basename(visual_model_path)
    # Real ONNX inference (NudeNet-lite style). A missing/failed optional model
    # is recorded explicitly so an "on" + "block" policy can fail closed.
    if visual_classifier is not None:
        visual["available"] = bool(visual_classifier.available)
        visual["status"] = "ready" if visual_classifier.available else "unavailable"
        visual["error"] = visual_classifier.error
    if visual_classifier is not None and visual_classifier.available and clip and os.path.exists(clip):
        visual["status"] = "scanned"
        vreport = visual_classifier.analyze_video(
            clip, num_frames=max(1, int(visual_frames or 4)))
        if vreport.get("graphic_score") is not None:
            visual["score"] = round(vreport["graphic_score"], 1)
            visual["graphic"] = vreport["graphic"]
            visual["top_class"] = vreport["top_class"]
            visual["frames"] = vreport["frames"]
            visual["model"] = vreport["model"] or visual["model"]
            if visual["score"] >= float(visual_threshold):
                visual["flag"] = "graphic content ({}% probability)".format(visual["score"])
    elif visual_classifier is not None and visual_classifier.available:
        visual["status"] = "no_clip"
    entry["axes"]["visual"] = visual

    # --- overall ---
    scores = [text_scores["first7s"], text_scores["semantic_policy"], reuse["score"], visual["score"]]
    overall = max(scores)
    entry["overall_score"] = round(overall, 1)
    entry["overall"] = ("danger" if overall >= 85 else
                        "high" if overall >= 70 else
                        "medium" if overall >= 40 else "low")
    return entry


def analyze_project(project_folder, viral_segments=None, gate_threshold=HIGH_REUSE_THRESHOLD,
                    visual_model_path=None, auto_download_visual=False, i18n=lambda k: k,
                    visual_check="auto", visual_gate="warn", visual_frames=4,
                    visual_threshold=VISUAL_GRAPHIC_THRESHOLD, provenance_gate="warn"):
    """Score every segment, persist risk_scorecard.json + publish_blocklist.json.

    Returns {"segments": [...], "blocked": [...], "summary": {...}}.
    """
    if viral_segments is None:
        path = os.path.join(project_folder, "viral_segments.txt")
        if not os.path.exists(path):
            return {"segments": [], "blocked": [], "summary": {}}
        with open(path, "r", encoding="utf-8") as f:
            viral_segments = json.load(f)

    # Real visual classifier (Roadmap 2.1): explicit path > default models dir.
    visual_mode = str(visual_check or "auto").lower()
    if visual_mode not in {"off", "auto", "on"}:
        visual_mode = "auto"
    visual_policy = str(visual_gate or "warn").lower()
    if visual_policy not in {"off", "warn", "block"}:
        visual_policy = "warn"
    classifier = None
    model_path = visual_model_path
    if visual_mode != "off":
        if not model_path or not os.path.exists(model_path):
            default = visual_check_module.default_model_path()
            if os.path.exists(default):
                model_path = default
        if auto_download_visual and (not model_path or not os.path.exists(model_path)):
            try:
                model_path = visual_check_module.download_model()
            except Exception as e:
                print("[risk] visual model download skipped: {}".format(e))
        if model_path and os.path.exists(model_path):
            classifier = visual_check_module.NudeNetClassifier(model_path)
            if not classifier.available:
                print("[risk] visual classifier unavailable ({}); continuing text-only".format(
                    classifier.error))

    segments = (viral_segments or {}).get("segments", [])
    source_video = _find_source_video(project_folder)
    words = _load_words(project_folder)
    safety_entries = {}
    safety_path = os.path.join(project_folder, "safety_report.json")
    if os.path.exists(safety_path):
        try:
            with open(safety_path, "r", encoding="utf-8") as f:
                safety_data = json.load(f)
            safety_entries = {
                item.get("index"): item
                for item in safety_data.get("segments", [])
                if isinstance(item, dict) and item.get("index") is not None
            }
        except Exception:
            safety_entries = {}

    entries = []
    for i, seg in enumerate(segments):
        entries.append(score_segment(seg, i, project_folder, words,
                                     source_video, visual_model_path=model_path,
                                     visual_classifier=classifier,
                                     safety_entry=safety_entries.get(i),
                                     visual_frames=visual_frames,
                                     visual_threshold=visual_threshold))

    provenance_reviews = []
    for entry in entries:
        evidence = provenance.assess_clip(
            project_folder, entry.get("index"), policy=provenance_gate)
        entry["axes"]["provenance"] = evidence
        if evidence.get("action") != "allow":
            provenance_reviews.append(entry)

    def _non_visual_score(entry):
        axes = entry.get("axes") or {}
        text = axes.get("text") or {}
        values = [
            text.get("first7s", 0), text.get("semantic_policy", 0),
            (axes.get("reuse") or {}).get("score", 0),
        ]
        return max(float(v or 0) for v in values)

    def _visual_blocked(entry):
        visual = (entry.get("axes") or {}).get("visual") or {}
        return visual_policy != "off" and float(visual.get("score", 0) or 0) >= float(visual_threshold)

    blocked = [e for e in entries if
               ((e.get("axes") or {}).get("reuse") or {}).get("score", 0) >= gate_threshold
               or _non_visual_score(e) >= 70.0
               or _visual_blocked(e)
               or ((e.get("axes") or {}).get("provenance") or {}).get("action") == "block"]

    summary = {
        "total": len(entries),
        "low": sum(1 for e in entries if e["overall"] == "low"),
        "medium": sum(1 for e in entries if e["overall"] == "medium"),
        "high": sum(1 for e in entries if e["overall"] == "high"),
        "danger": sum(1 for e in entries if e["overall"] == "danger"),
        "blocked_for_publish": len(blocked),
        "gate_threshold": gate_threshold,
        "visual_model": os.path.basename(model_path) if model_path and os.path.exists(model_path) else None,
        "visual_check": visual_mode,
        "visual_gate": visual_policy,
        "visual_available": bool(classifier is not None and classifier.available),
        "visual_unavailable": visual_mode == "on" and not bool(classifier is not None and classifier.available),
        "visual_gate_failed": visual_mode == "on" and visual_policy == "block" and not bool(classifier is not None and classifier.available),
        "provenance_gate": provenance_gate,
        "provenance_review": len(provenance_reviews),
        "provenance_blocked": sum(
            ((e.get("axes") or {}).get("provenance") or {}).get("action") == "block"
            for e in entries),
    }

    report = {"summary": summary, "segments": entries, "blocked": blocked}
    try:
        with open(os.path.join(project_folder, SCORECARD_FILENAME), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[risk] could not save scorecard: {e}")

    try:
        for entry in entries:
            content_ledger.record_clip_audit(
                project_folder, entry.get("index"), entry,
                _find_clip_video(project_folder, entry.get("index")))
        summary["database"] = content_ledger.ledger_summary(project_folder)["database"]
    except Exception as e:
        print("[risk] ledger write skipped: {}".format(e))

    report["summary"] = summary
    try:
        with open(os.path.join(project_folder, SCORECARD_FILENAME), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[risk] could not update scorecard metadata: {e}")

    try:
        provenance_report = provenance.analyze_project(project_folder, policy=provenance_gate)
        summary["provenance"] = provenance_report.get("summary", {})
        report["summary"] = summary
        with open(os.path.join(project_folder, SCORECARD_FILENAME), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[risk] provenance report skipped: {}".format(e))

    blocklist_path = os.path.join(project_folder, PUBLISH_BLOCKLIST_FILENAME)
    if blocked:
        try:
            with open(blocklist_path, "w", encoding="utf-8") as f:
                json.dump({"blocked": blocked}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    else:
        # Do not let a previous run's blocklist keep affecting a clean rerun.
        try:
            if os.path.exists(blocklist_path):
                os.remove(blocklist_path)
        except Exception:
            pass

    # console summary
    print(i18n("[risk] Scorecard: {} total — {} low / {} medium / {} high / {} danger — {} blocked for publish").format(
        summary["total"], summary["low"], summary["medium"], summary["high"],
        summary["danger"], summary["blocked_for_publish"]))
    for e in entries:
        reuse = e["axes"]["reuse"].get("score")
        reuse_s = "{:.0f}".format(reuse) if reuse is not None else "n/a"
        print(i18n("[risk]   {} '{}': overall={} reuse={} first7s={}").format(
            "⛔" if e in blocked else "  ",
            e["title"], e["overall"], reuse_s, e["axes"]["text"]["first7s"]))
        if e in blocked:
            print(i18n("[risk]       → DO NOT PUBLISH: {}").format(
                "clip is still ~{:.0f}% identical to the source (reused content risk)".format(e["axes"]["reuse"]["score"])
                if e["axes"]["reuse"].get("score") and e["axes"]["reuse"]["score"] >= gate_threshold
                else "high overall risk"))
    return report


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Per-clip YouTube risk scorecard.")
    parser.add_argument("--project", required=True, help="Project folder")
    parser.add_argument("--gate-threshold", type=float, default=HIGH_REUSE_THRESHOLD)
    parser.add_argument("--visual-model", default=None,
                        help="Path to an optional local ONNX visual classifier (e.g. models/nudenet_lite.onnx)")
    parser.add_argument("--auto-download-visual", action="store_true",
                        help="Download the default small visual classifier into models/ if missing")
    parser.add_argument("--visual-check", choices=["off", "auto", "on"], default="auto",
                        help="Visual safety scan: off, auto when a local model exists, or on (fail closed if unavailable)")
    parser.add_argument("--visual-gate", choices=["off", "warn", "block"], default="warn",
                        help="Visual policy: off, warn, or block graphic visual findings")
    parser.add_argument("--visual-frames", type=int, default=4,
                        help="Frames sampled per clip for the visual scan (default: 4)")
    parser.add_argument("--exit-on-blocked", action="store_true",
                        help="Exit code 1 if any clip is blocked for publish")
    parser.add_argument("--html-report", action="store_true",
                        help="Also write a readable risk_report.html next to the scorecard")
    args = parser.parse_args()

    report = analyze_project(args.project, gate_threshold=args.gate_threshold,
                             visual_model_path=args.visual_model,
                             auto_download_visual=args.auto_download_visual,
                             visual_check=args.visual_check,
                             visual_gate=args.visual_gate,
                             visual_frames=args.visual_frames)
    if args.html_report:
        path = render_html_report(args.project)
        print("[risk] HTML report → {}".format(path or "failed"))
    blocked = len(report.get("blocked", []))
    if args.exit_on_blocked and blocked:
        raise SystemExit(1)


if __name__ == "__main__":
    main()


# --------------------------------------------------------------------------
# Human-readable HTML report (functional completeness: JSON for machines,
# HTML for the channel owner — shown in the WebUI Review tab too).
# --------------------------------------------------------------------------
BADGE = {
    "low": ("#16a34a", "منخفض"), "medium": ("#f59e0b", "متوسط"),
    "high": ("#f97316", "مرتفع"), "danger": ("#dc2626", "خطير"),
}


def build_scorecard_html(report):
    """Render a risk scorecard dict as readable HTML. Pure + testable.

    Returns the HTML fragment (no <html> wrapper — embeddable in the WebUI).
    """
    summary = report.get("summary") or {}
    segments = report.get("segments") or []
    blocked = report.get("blocked") or []
    blocked_ids = {id(e) for e in blocked}

    def badge(overall):
        color, label = BADGE.get(overall, ("#64748b", overall or "?"))
        return '<span style="background:{}22;color:{};border:1px solid {}55;border-radius:999px;padding:2px 10px;font-weight:700;font-size:0.8em;">{}</span>'.format(
            color, color, color, label)

    rows = []
    for e in segments:
        is_blocked = id(e) in blocked_ids or e.get("overall") in ("high", "danger")
        axes = e.get("axes") or {}
        reuse = (axes.get("reuse") or {})
        visual = (axes.get("visual") or {})
        text = (axes.get("text") or {})
        first7 = text.get("first7s")
        if isinstance(first7, dict):
            f7 = "{} /100".format(first7.get("score", 0))
        else:
            f7 = "—"
        sim = reuse.get("similarity")
        sim_txt = "{:.0f}%".format(sim * 100) if isinstance(sim, (int, float)) else "—"
        vis_txt = "{:.0f}".format(visual.get("score") or 0) + (" ⚠️" if visual.get("flag") else "")
        rows.append(
            '<tr style="border-bottom:1px solid rgba(255,255,255,0.06);">'
            '<td style="padding:8px 10px;">{}</td>'
            '<td style="padding:8px 10px;">{}</td>'
            '<td style="padding:8px 10px;text-align:center;">{}</td>'
            '<td style="padding:8px 10px;text-align:center;">{}</td>'
            '<td style="padding:8px 10px;text-align:center;">{}</td>'
            '<td style="padding:8px 10px;text-align:center;">{}</td>'
            '<td style="padding:8px 10px;text-align:center;">{}</td>'
            '</tr>'.format(
                e.get("index", "?"), (e.get("title") or "")[:60],
                badge(e.get("overall")), f7, sim_txt, vis_txt,
                "⛔ محجوب" if is_blocked else "✅ مسموح"))

    visual_notice = ""
    if summary.get("visual_gate_failed"):
        visual_notice = (
            '<div style="background:#dc262615;border:1px solid #dc262655;border-radius:10px;'
            'padding:10px 14px;margin:10px 0;color:#fca5a5;">'
            '⛔ الفحص البصري إلزامي، لكن النموذج المحلي غير متاح؛ لا يُسمح بالنشر.</div>'
        )
    elif summary.get("visual_unavailable"):
        visual_notice = (
            '<div style="background:#f59e0b15;border:1px solid #f59e0b55;border-radius:10px;'
            'padding:10px 14px;margin:10px 0;color:#fbbf24;">'
            '⚠️ لم يُنفذ الفحص البصري لغياب نموذج ONNX؛ النتائج الحالية نصية/تقنية فقط.</div>'
        )
    elif summary.get("visual_available"):
        visual_notice = (
            '<div style="background:#16a34a15;border:1px solid #16a34a55;border-radius:10px;'
            'padding:10px 14px;margin:10px 0;color:#86efac;">'
            '✅ تم تنفيذ الفحص البصري المحلي على المقاطع.</div>'
        )

    blocked_html = ""
    if blocked:
        items = "".join(
            '<li style="padding:6px 0;">⛔ <b>{}</b> — <span style="color:#94a3b8;">السبب: {}</span></li>'.format(
                (e.get("title") or "?"), _block_reason(e))
            for e in blocked[:10])
        blocked_html = (
            '<div style="background:#dc262612;border:1px solid #dc262655;border-radius:10px;'
            'padding:10px 14px;margin-top:10px;"><b style="color:#fca5a5;">⛔ ممنوع النشر: '
            '{}</b><ul style="margin:6px 0 0;padding-right:18px;">{}</ul></div>'.format(
                len(blocked), items))

    return """
<div style="direction:rtl;text-align:right;font-family:inherit;">
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;">
    <span style="background:#16a34a22;color:#4ade80;border-radius:999px;padding:3px 12px;font-size:0.8em;">{low} منخفض</span>
    <span style="background:#f59e0b22;color:#fbbf24;border-radius:999px;padding:3px 12px;font-size:0.8em;">{med} متوسط</span>
    <span style="background:#f9731622;color:#fb923c;border-radius:999px;padding:3px 12px;font-size:0.8em;">{high} مرتفع</span>
    <span style="background:#dc262622;color:#f87171;border-radius:999px;padding:3px 12px;font-size:0.8em;">{danger} خطير</span>
    <span style="background:#ffffff11;border-radius:999px;padding:3px 12px;font-size:0.8em;">⛔ {blocked} ممنوع النشر</span>
  </div>
  <table style="width:100%;border-collapse:collapse;background:rgba(255,255,255,0.03);border-radius:10px;overflow:hidden;">
    <tr style="background:rgba(255,255,255,0.06);color:#e2e8f0;">
      <th style="padding:8px 10px;text-align:right;">#</th>
      <th style="padding:8px 10px;text-align:right;">المقطع</th>
      <th style="padding:8px 10px;">المخاطر</th>
      <th style="padding:8px 10px;">أول 7 ثوانٍ</th>
      <th style="padding:8px 10px;">تشابه المصدر</th>
      <th style="padding:8px 10px;">بصري</th>
      <th style="padding:8px 10px;">الحالة</th>
    </tr>
    {rows}
  </table>
  {visual_notice}
  {blocked_html}
</div>
""".format(low=summary.get("low", 0), med=summary.get("medium", 0),
           high=summary.get("high", 0), danger=summary.get("danger", 0),
           blocked=summary.get("blocked_for_publish", 0),
           visual_notice=visual_notice,
           rows="".join(rows) or '<tr><td colspan="7" style="padding:12px;color:#94a3b8;">لا توجد بيانات</td></tr>',
           blocked_html=blocked_html)


def _block_reason(entry):
    axes = entry.get("axes") or {}
    parts = []
    reuse = axes.get("reuse") or {}
    if (reuse.get("similarity") or 0) >= HIGH_REUSE_THRESHOLD:
        parts.append("محتوى مُعاد استخدامه ({:.0f}% تشابه)".format(reuse["similarity"] * 100))
    if axes.get("text") and isinstance(axes["text"].get("first7s"), dict) and (axes["text"]["first7s"].get("score") or 0) >= 50:
        parts.append("كلمة مخالفة في أول 7 ثوانٍ")
    if axes.get("visual") and (axes["visual"].get("flag")):
        parts.append(axes["visual"]["flag"])
    return "، ".join(parts) if parts else "مخاطر مرتفعة/خطيرة"


def render_html_report(project_folder):
    """Write risk_report.html next to risk_scorecard.json. Returns path or None."""
    path = os.path.join(project_folder, SCORECARD_FILENAME)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            report = json.load(fh)
        html = build_scorecard_html(report)
    except Exception:
        return None
    out = os.path.join(project_folder, "risk_report.html")
    page = """<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<title>تقرير مخاطر النشر — OUSSAMA Cutter</title>
<style>body{{font-family:Segoe UI,Tahoma,Arial,sans-serif;background:#0b0b0b;color:#e2e8f0;max-width:900px;margin:24px auto;padding:0 16px;}}
h1{{font-size:1.4em;}} .muted{{color:#94a3b8;}}</style></head>
<body><h1>🛡️ تقرير مخاطر النشر</h1>
<p class="muted">OUSSAMA Cutter — تحقق من أن كل مقطع آمن للنشر قبل الرفع.</p>
{body}</body></html>""".format(body=html)
    try:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(page)
        return out
    except Exception:
        return None
