# -*- coding: utf-8 -*-
"""
Punch Zoom — dynamic punch-in on keywords / emotional beats.

Roadmap item 3.2 ("زوم Punch-in"). Gives clips the "signature" energy of
modern viral shorts: at hook moments the frame punches in ~1.2x then
returns. Timing comes from word timings + the segment's virality score.

    plan_punches()  — pure logic: which moments to punch (defaults:
                      the hook = first words, plus optional keywords,
                      plus an optional auto-interval rhythm)
    apply_punch_zoom() — ffmpeg zoompan keyed on output frame number `on`,
                      so punches land exactly at the word timestamps.

Uses only ffmpeg + stdlib. A clip without any punch target is copied
unchanged. Keyword list can come from the segment title / hook.
"""

import json
import os
import re
import shutil
import subprocess

PUNCH_ZOOM = 1.18
PUNCH_DURATION = 0.55   # seconds the punch holds
PUNCH_RAMP = 0.12       # quick ease so it doesn't feel like a jump-cut zoom
HOOK_PUNCH_DURATION = 1.0
FPS = 30                # zoompan needs a fixed fps

# Words that usually carry emotional weight (used when no keywords given).
EMOTIONAL_WORDS = {
    "wow", "no", "yes", "never", "always", "stop", "look", "watch", "listen",
    "wait", "crazy", "insane", "unbelievable", "amazing", "shocking", "mind",
    "secret", "truth", "lie", "money", "free", "win", "lose", "danger",
    "va", "regarde", "écoute", "incroyable", "incrível", "olha", "espera",
}


def load_words(subs_json_path):
    """Reuse the same word loader as jump_cuts."""
    from scripts.jump_cuts import load_words as _load
    return _load(subs_json_path)


def _normalize_keywords(keywords):
    if not keywords:
        return set()
    if isinstance(keywords, str):
        keywords = re.split(r"[,\s]+", keywords)
    return {str(k).lower() for k in keywords if str(k).strip()}


def plan_punches(words, keywords=None, emotional=True, hook=True,
                 auto_interval=0.0, punch_duration=PUNCH_DURATION,
                 hook_duration=HOOK_PUNCH_DURATION, duration=None):
    """Compute punch windows [(start, end), ...].

    Order of preference:
      1. words matching `keywords` (explicit, from segment title/hook)
      2. emotional words (if emotional=True)
      3. hook punch at the very start (if hook=True)
      4. auto rhythm every `auto_interval` seconds (if > 0 and nothing found)

    Punches closer than 1.2s are merged into one longer punch.
    """
    if not words:
        words = []
    kw = _normalize_keywords(keywords)

    # 1+2: word hits
    hits = []
    for w in words:
        text = (w.get("word", "") or "").strip(".,!?… ")
        low = text.lower()
        if low in kw:
            hits.append((w["start"], w["end"]))
        elif emotional and low in EMOTIONAL_WORDS:
            hits.append((w["start"], w["end"]))

    # merge close hits
    punches = []
    for s, e in hits:
        if punches and s - punches[-1][1] < 1.2:
            punches[-1] = (punches[-1][0], max(punches[-1][1], e))
        else:
            punches.append((s, e))
    punches = [(s, s + punch_duration) for s, e in punches]

    # 3: hook punch at the start
    if hook and (not punches or punches[0][0] > 0.3):
        start = 0.05
        if punches and punches[0][0] < start + hook_duration:
            punches[0] = (start, max(punches[0][1], start + hook_duration))
        else:
            punches.insert(0, (start, start + hook_duration))

    # 4: auto rhythm fallback
    if auto_interval > 0 and len(punches) <= 1:
        total = duration or (words[-1]["end"] if words else 0.0)
        t = auto_interval
        while t < total - punch_duration:
            punches.append((t, t + punch_duration))
            t += auto_interval

    # drop punches beyond duration, merge overlapping, keep sorted
    result = []
    for s, e in sorted(punches):
        if duration is not None and s >= duration:
            continue
        e = min(e, duration) if duration is not None else e
        if result and s - result[-1][1] < PUNCH_RAMP:
            result[-1] = (result[-1][0], max(result[-1][1], e))
        else:
            result.append((s, round(e, 3)))
    return [(round(s, 3), e) for s, e in result]


def _probe_size(video_path):
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=s=x:p=0", video_path],
            capture_output=True, text=True, timeout=30)
        parts = res.stdout.strip().split("x")
        return int(parts[0]), int(parts[1])
    except Exception:
        return None


def apply_punch_zoom(video_path, out_path, punches, zoom=PUNCH_ZOOM,
                     punch_duration=PUNCH_DURATION):
    """Render punches via zoompan. Returns number of punches applied."""
    punches = [p for p in (punches or []) if p[1] > p[0]]
    if not punches or not os.path.exists(video_path):
        if os.path.exists(video_path):
            shutil.copy2(video_path, out_path)
        return 0

    size = _probe_size(video_path) or (1080, 1920)
    w, h = size
    # ensure even dims for h264
    w -= w % 2
    h -= h % 2

    # zoompan z-expression: punch to `zoom` during [s,e] in output frames
    conditions = []
    for s, e in punches:
        conditions.append(
            "between(on,{},{})".format(int(round(s * FPS)), int(round(e * FPS))))
    z_expr = "if(" + ",".join(
        [conditions[0] + "," + "{:.3f}".format(zoom)] +
        ["between(on,{},{})".format(int(round(s * FPS)), int(round(e * FPS)))
         + "," + "{:.3f}".format(zoom) for s, e in punches[1:]] +
        ["1"]) + ")"
    # x/y keep the center of the frame (already face-cropped)
    vf = (
        "fps={fps},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        ":d=1:s={w}x{h}".format(fps=FPS, z=z_expr, w=w, h=h)
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=1800)
    return len(punches)


def process_file(video_path, subs_json=None, out_path=None, keywords=None,
                 zoom=PUNCH_ZOOM, auto_interval=0.0, emotional=True,
                 hook=True, punch_duration=PUNCH_DURATION):
    """Full pipeline for one clip. Returns a report dict (never raises)."""
    report = {"video": os.path.basename(video_path), "ok": False,
              "punches": []}
    if out_path is None:
        out_path = video_path
    try:
        words = load_words(subs_json) if subs_json else []
        if words:
            duration = words[-1]["end"]
        else:
            duration = None
        punches = plan_punches(words, keywords=keywords, emotional=emotional,
                               hook=hook, auto_interval=auto_interval,
                               punch_duration=punch_duration, duration=duration)
        n = apply_punch_zoom(video_path, out_path, punches, zoom=zoom)
        report.update({"ok": True, "punches": punches, "count": n})
    except Exception as e:
        report["error"] = str(e)
        if os.path.exists(video_path) and out_path != video_path:
            shutil.copy2(video_path, out_path)
    return report


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ViralCutter punch-in zoom.")
    parser.add_argument("--video", required=True)
    parser.add_argument("--subs", default=None, help="subs JSON with word timings")
    parser.add_argument("--out", default=None)
    parser.add_argument("--keywords", default=None,
                        help="comma-separated words that trigger a punch")
    parser.add_argument("--zoom", type=float, default=PUNCH_ZOOM)
    parser.add_argument("--auto-interval", type=float, default=0.0,
                        help="punch every N seconds when no keyword hits")
    parser.add_argument("--no-hook", action="store_true", help="disable the opening hook punch")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = process_file(args.video, args.subs, args.out,
                          keywords=args.keywords, zoom=args.zoom,
                          auto_interval=args.auto_interval, hook=not args.no_hook)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("{}: {} punch(es)".format(report["video"], report.get("count", 0)))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
