# -*- coding: utf-8 -*-
"""ViralCutter output reframe — final aspect-ratio stage (Roadmap "more
framing formats", safe version).

The face-tracking crop pipeline renders 9:16 (1080x1920). This stage runs
AFTER subtitle burning on the FINAL clips and converts them to another aspect
ratio in one ffmpeg pass — no touching of the crop/tracking logic (which was
the source of the v6.6 A/V-sync fix; changing it is high-risk).

Modes:
    crop  — fill the target frame and center-crop (loses sides/top-bottom).
            Default for 4:5 and 1:1 (little loss from 9:16).
    pad   — fit the full frame with a blurred background (bars).
            Default for 16:9 (cropping 9:16 to 16:9 would destroy the shot).

Usage:
  python -m scripts.reframe --project VIRALS/x --aspect 4:5 [--mode crop] [--dry-run]
  python -m scripts.reframe --aspect 9:16 --project VIRALS/x   # no-op, verifies

The reframed clip REPLACES the subtitled clip in place (atomic: temp file +
rename), so the risk scorecard, publish gate and organize_output all see the
true final file. The original is kept as <name>.orig.mp4.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys

# aspect -> (width, height)
ASPECTS = {
    "9:16": (1080, 1920),
    "4:5": (1080, 1350),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
}
DEFAULT_MODE = {"16:9": "pad"}  # other aspects default to "crop"


def resolve_aspect(aspect):
    """Normalize '4:5' / '45' / '1x1' style input → (w, h) or None."""
    if not aspect:
        return None
    a = str(aspect).strip().lower()
    if a in ASPECTS:
        return ASPECTS[a]
    a = a.replace("x", ":").replace("_", ":")
    if a in ASPECTS:
        return ASPECTS[a]
    try:
        w, h = a.split(":")
        return (int(w), int(h))
    except Exception:
        return None


def find_subtitled_clips(project_folder):
    """Final deliverable clips in final/ and final_polished/ (subtitled)."""
    out = []
    for sub in ("final", "final_polished"):
        folder = os.path.join(project_folder, sub)
        if not os.path.isdir(folder):
            continue
        for pattern in ("*_subtitled.mp4", "*_subtitled_*.mp4"):
            for f in sorted(glob.glob(os.path.join(folder, pattern))):
                if f not in out:
                    out.append(f)
    return out


def build_ffmpeg_filter(target, mode):
    """Build the ffmpeg -vf filter string for (w,h) + crop|pad."""
    w, h = target
    if mode == "crop":
        return "scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d" % (w, h, w, h)
    # blur-pad: blurred full-frame background + full video centered on top
    return (
        "split[bg][fg];"
        "[bg]scale=%d:%d:force_original_aspect_ratio=increase,"
        "crop=%d:%d,boxblur=20:2[bg];"
        "[fg]scale=%d:%d:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2" % (w, h, w, h, w, h)
    )


def reframe_file(clip, target, mode, ffmpeg="ffmpeg", dry_run=False):
    """Reframe one clip in place (atomic replace; .orig.mp4 backup kept)."""
    w, h = target
    vf = build_ffmpeg_filter(target, mode)
    tmp = clip + ".reframe_tmp.mp4"
    cmd = [ffmpeg, "-y", "-i", clip,
           "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-c:a", "copy",
           "-movflags", "+faststart",
           tmp]
    if dry_run:
        return {"ok": True, "dry_run": True, "cmd": cmd, "clip": clip}
    if shutil.which(ffmpeg) is None:
        return {"ok": False, "error": "ffmpeg not found on PATH", "clip": clip}
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout)[-400:], "clip": clip}
    # atomic replace, keep original as backup
    orig = clip + ".orig.mp4"
    if os.path.exists(orig):
        os.remove(orig)
    os.replace(clip, orig)
    os.replace(tmp, clip)
    return {"ok": True, "clip": clip, "size": os.path.getsize(clip)}


def reframe_project(project_folder, aspect, mode=None, dry_run=False,
                    ffmpeg="ffmpeg"):
    """Reframe every subtitled clip in a project. Returns list of results."""
    target = resolve_aspect(aspect)
    if target is None:
        raise ValueError("unknown aspect '%s' (use 9:16, 4:5, 1:1 or 16:9)" % aspect)
    mode = mode or DEFAULT_MODE.get(aspect, "crop")
    clips = find_subtitled_clips(project_folder)
    results = []
    for clip in clips:
        results.append(reframe_file(clip, target, mode, ffmpeg=ffmpeg, dry_run=dry_run))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="ViralCutter output reframe — convert final clips to another aspect ratio")
    parser.add_argument("--project", required=True, help="project folder (VIRALS/<name>)")
    parser.add_argument("--aspect", required=True, help="9:16 | 4:5 | 1:1 | 16:9")
    parser.add_argument("--mode", choices=["crop", "pad"], default=None,
                        help="crop=fill+center-crop (default for 4:5/1:1), pad=blur bars (default for 16:9)")
    parser.add_argument("--dry-run", action="store_true", help="print commands, change nothing")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg binary (default: ffmpeg)")
    args = parser.parse_args(argv)

    try:
        results = reframe_project(args.project, args.aspect, args.mode,
                                  dry_run=args.dry_run, ffmpeg=args.ffmpeg)
    except ValueError as e:
        print("❌ %s" % e)
        return 1

    if not results:
        print("No subtitled clips found in %s (looked in final/ and final_polished/)." % args.project)
        return 1
    ok_n = sum(1 for r in results if r.get("ok"))
    for r in results:
        if r.get("ok"):
            note = " (dry-run)" if r.get("dry_run") else ""
            print("✅ %s → %s%s" % (os.path.basename(r["clip"]), args.aspect, note))
        else:
            print("❌ %s: %s" % (os.path.basename(r.get("clip", "?")), r.get("error", "?")))
    print("%d/%d clips reframed to %s" % (ok_n, len(results), args.aspect))
    return 0 if ok_n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
