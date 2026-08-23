# -*- coding: utf-8 -*-
"""
Jump Cuts — automatic silence & filler-word removal.

Roadmap item 3.1 ("حذف الصمت والحشو"). Keeps viewers by removing the
dead air ("آآآ… هممم… صمت") that makes shorts feel slow:

    1. detect_silences()  — ffmpeg silencedetect (noise floor dB threshold)
    2. find_filler_spans()— transcript words that are fillers (um, uh, ah, …)
    3. plan_cuts()        — merge + filter spans into safe cut intervals
    4. apply_jump_cuts()  — ffmpeg select/aselect with between(t) filters;
                            both streams are re-timestamped → perfect A/V sync

Design: pure stdlib + ffmpeg, unit-testable, and safe — a clip with no
detected dead air is copied untouched. Configurable via CLI/JSON:
silence threshold, minimum cut length, maximum cut length, filler words.
"""

import json
import os
import re
import subprocess

DEFAULT_FILLERS = {
    "um", "uh", "ah", "er", "err", "hmm", "mm", "mhm", "emm", "uhh", "umm",
    "euh", "euhh", "aah",
}
MIN_SILENCE = 0.40        # silence shorter than this is left alone
MIN_FILLER_SPAN = 0.25    # filler runs shorter than this are left alone
MAX_CUT = 4.0             # never remove more than this at once
PADDING = 0.10            # extend filler spans by this margin on each side
MIN_KEEP = 0.35           # never leave a gap shorter than this between kept speech


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_silences(video_path, threshold_db=-35.0, min_duration=MIN_SILENCE):
    """Return [(start, end, duration), ...] via ffmpeg silencedetect."""
    if not os.path.exists(video_path):
        return []
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", video_path,
        "-af", "silencedetect=noise={}dB:d={}".format(threshold_db, min_duration),
        "-f", "null", "-",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except Exception:
        return []
    text = (res.stdout or "") + "\n" + (res.stderr or "")
    starts, ends = [], []
    for m in re.finditer(r"silence_start:\s*([\d.]+)", text):
        starts.append(float(m.group(1)))
    for m in re.finditer(r"silence_end:\s*([\d.]+)", text):
        ends.append(float(m.group(1)))
    if len(ends) == len(starts) - 1:  # trailing silence never ended
        ends.append(None)
    out = []
    for s, e in zip(starts, ends):
        if e is None:
            continue
        out.append((s, e, round(e - s, 3)))
    return out


def load_words(subs_json_path):
    """Flatten word timings from a subs/*_processed.json file."""
    if not subs_json_path or not os.path.exists(subs_json_path):
        return []
    try:
        with open(subs_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    words = []
    for seg in data.get("segments", []):
        seg_words = seg.get("words")
        if seg_words:
            for w in seg_words:
                try:
                    words.append({"word": str(w.get("word", "")).lower(),
                                  "start": float(w.get("start", 0)),
                                  "end": float(w.get("end", 0))})
                except Exception:
                    continue
        else:
            # no word-level data → approximate the whole segment as one word
            try:
                words.append({"word": str(seg.get("text", "")).lower(),
                              "start": float(seg.get("start", 0)),
                              "end": float(seg.get("end", 0))})
            except Exception:
                continue
    words.sort(key=lambda w: w["start"])
    return words


def find_filler_spans(words, fillers=None):
    """[(start, end), ...] merged runs of consecutive filler words."""
    if not words:
        return []
    fillers = fillers or DEFAULT_FILLERS

    def is_filler(w):
        w = (w or "").strip(".,!?… ").lower()
        return w in fillers

    spans = []
    run_start = None
    run_end = None
    for w in words:
        if is_filler(w.get("word", "")):
            if run_start is None:
                run_start, run_end = w["start"], w["end"]
            else:
                run_end = w["end"]
        else:
            if run_start is not None:
                spans.append((run_start, run_end))
                run_start = run_end = None
    if run_start is not None:
        spans.append((run_start, run_end))
    # merge runs closer than 0.15s
    merged = []
    for s, e in spans:
        if merged and s - merged[-1][1] < 0.15:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged


def _probe_duration(video_path):
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30)
        return float(res.stdout.strip())
    except Exception:
        return None


def plan_cuts(silences, filler_spans, duration=None,
              min_silence=MIN_SILENCE, min_filler_span=MIN_FILLER_SPAN,
              max_cut=MAX_CUT, padding=PADDING, min_keep=MIN_KEEP):
    """Merge silences + fillers into safe cut intervals.

    Rules:
      * silence spans shorter than min_silence are dropped
      * filler spans shorter than min_filler_span are dropped
      * filler spans are padded by `padding` on each side (never beyond bounds)
      * cuts longer than max_cut are clipped
      * cuts closer together than min_keep are merged (never leave a tiny sliver)
    Returns a sorted list of (start, end).
    """
    spans = []
    for s, e, _ in silences or []:
        if (e - s) >= min_silence:
            spans.append((s, e))
    for s, e in filler_spans or []:
        if (e - s) >= min_filler_span:
            spans.append((max(0.0, s - padding), e + padding))
    if not spans:
        return []

    spans.sort()
    merged = [list(spans[0])]
    for s, e in spans[1:]:
        if s - merged[-1][1] < min_keep:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    # clip to duration and max_cut
    cuts = []
    for s, e in merged:
        if duration is not None:
            e = min(e, float(duration))
        if e - s < min_silence:
            continue
        if e - s > max_cut:
            e = s + max_cut
        cuts.append((round(s, 3), round(e, 3)))
    return cuts


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------

def _has_audio_stream(video_path):
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=30)
        return bool(res.stdout.strip())
    except Exception:
        return True


def apply_jump_cuts(video_path, out_path, cuts, audio=True):
    """Cut the given intervals out with ffmpeg select/aselect.

    Both streams are re-timestamped (setpts/asetpts) so A/V stays in sync.
    Returns the number of seconds removed.
    """
    removed = sum(e - s for s, e in cuts)
    if not cuts or not os.path.exists(video_path):
        if os.path.exists(video_path):
            import shutil
            shutil.copy2(video_path, out_path)
        return 0.0

    expr_parts = ["between(t,{:.3f},{:.3f})".format(s, e) for s, e in cuts]
    sel = "not(" + "+".join(expr_parts) + ")"
    if audio and _has_audio_stream(video_path):
        fc = ("[0:v]select='{}',setpts=N/FRAME_RATE/TB[v];"
              "[0:a]aselect='{}',asetpts=N/SR/TB[a]").format(sel, sel)
        maps = ["-map", "[v]", "-map", "[a]"]
    else:
        fc = "[0:v]select='{}',setpts=N/FRAME_RATE/TB[v]".format(sel)
        maps = ["-map", "[v]"]

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", video_path,
        "-filter_complex", fc,
    ] + maps + [
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=1200)
    return round(removed, 3)


def process_file(video_path, subs_json=None, out_path=None,
                 threshold_db=-35.0, min_silence=MIN_SILENCE, fillers=None,
                 max_cut=MAX_CUT):
    """Full pipeline for one clip. Returns a report dict (never raises)."""
    report = {"video": os.path.basename(video_path), "ok": False,
              "cuts": [], "removed": 0.0}
    if out_path is None:
        out_path = video_path
    try:
        duration = _probe_duration(video_path)
        silences = detect_silences(video_path, threshold_db, min_silence)
        words = load_words(subs_json) if subs_json else []
        filler_spans = find_filler_spans(words, fillers)
        cuts = plan_cuts(silences, filler_spans, duration=duration,
                         max_cut=max_cut)
        removed = apply_jump_cuts(video_path, out_path, cuts)
        report.update({"ok": True, "cuts": cuts, "removed": removed,
                       "silences": len(silences), "fillers": len(filler_spans)})
    except Exception as e:
        report["error"] = str(e)
        if os.path.exists(video_path) and out_path != video_path:
            import shutil
            shutil.copy2(video_path, out_path)  # fail-safe: keep original
    return report


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ViralCutter jump cuts (silence/filler removal).")
    parser.add_argument("--video", required=True)
    parser.add_argument("--subs", default=None, help="subs/*_processed.json with word timings")
    parser.add_argument("--out", default=None)
    parser.add_argument("--threshold", type=float, default=-35.0)
    parser.add_argument("--min-silence", type=float, default=MIN_SILENCE)
    parser.add_argument("--max-cut", type=float, default=MAX_CUT)
    parser.add_argument("--fillers", default=None, help="comma-separated extra filler words")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    fillers = None
    if args.fillers:
        fillers = DEFAULT_FILLERS | {f.strip().lower() for f in args.fillers.split(",") if f.strip()}
    report = process_file(args.video, args.subs, args.out,
                          threshold_db=args.threshold, min_silence=args.min_silence,
                          fillers=fillers, max_cut=args.max_cut)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("{}: {} cuts, {:.2f}s removed".format(
            report["video"], len(report.get("cuts", [])), report.get("removed", 0.0)))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
