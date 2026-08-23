# -*- coding: utf-8 -*-
"""
Branding — watermark + intro/outro.

Roadmap item 3.4 ("شعار/براند القناة"). Gives every clip the channel's
identity and adds an extra layer against "reused content" flags:

    apply_watermark(video_path, out_path, logo_path, ...)
        overlay the channel logo (PNG with alpha) in a corner.
    apply_intro_outro(video_path, out_path, intro, outro, ...)
        prepend/append short branded clips, normalized to the main video
        (same fps/size/audio) and concatenated losslessly-ish.

Safe-by-default: any missing asset (logo/intro/outro) is silently skipped
and the clip is still produced. Use pure ffmpeg + stdlib.
"""

import os
import shutil
import subprocess

POSITIONS = {
    "top-left": "10:10", "top-right": "W-w-10:10",
    "bottom-left": "10:H-h-10", "bottom-right": "W-w-10:H-h-10",
    "center": "(W-w)/2:(H-h)/2",
}


def _probe_streams(video_path):
    """Return (width, height, fps, has_audio) or (None,)*4."""
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error",
             "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=30)
        w, h, fps = None, None, None
        line = res.stdout.strip()
        if line:
            parts = line.split(",")
            if len(parts) >= 3:
                w, h = int(parts[0]), int(parts[1])
                num, den = parts[2].split("/")
                fps = float(num) / float(den) if float(den) else 30.0
    except Exception:
        return None, None, None, None
    has_audio = True
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=30)
        has_audio = bool(res.stdout.strip())
    except Exception:
        pass
    return w, h, fps, has_audio


def apply_watermark(video_path, out_path, logo_path, position="bottom-right",
                    size_fraction=0.12, opacity=0.9):
    """Overlay the logo. Returns True when applied (or copied fallback)."""
    if not os.path.exists(video_path):
        return False
    if not logo_path or not os.path.exists(logo_path):
        shutil.copy2(video_path, out_path)
        return False
    try:
        size = _probe_streams(video_path)
        w = size[0] or 1080
        logo_w = max(40, int(w * size_fraction))
        pos = POSITIONS.get(position, POSITIONS["bottom-right"])
        # scale logo, force RGBA, apply opacity
        logo_vf = ("[1:v]scale={lw}:-1,format=rgba,"
                   "colorchannelmixer=aa={op}[logo]").format(lw=logo_w, op=opacity)
        fc = "{};[0:v][logo]overlay={}:format=auto[v]".format(logo_vf, pos)
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", video_path, "-i", logo_path,
            "-filter_complex", fc,
            "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-c:a", "copy",
            "-movflags", "+faststart",
            out_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=1800)
        if not os.path.isfile(out_path) or os.path.getsize(out_path) <= 0:
            raise RuntimeError("watermark ffmpeg produced no output")
        return True
    except Exception:
        try:
            shutil.copy2(video_path, out_path)
        except OSError:
            pass
        return False


def _normalize_to(video_path, out_path, ref_size):
    """Re-encode to a uniform format (for concat)."""
    w, h, fps, has_audio = ref_size
    vf = ("scale={}:{}:force_original_aspect_ratio=decrease,"
          "pad={}:{}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={}").format(w, h, w, h, fps or 30)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac" if has_audio else "an",
        "-ar", "44100",
        "-ac", "2",
        "-shortest",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=1800)


def _has_audio(video_path):
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=30)
        return bool(res.stdout.strip())
    except Exception:
        return True


def apply_intro_outro(video_path, out_path, intro=None, outro=None):
    """Prepend/append intro & outro clips. Returns True when applied."""
    if not os.path.exists(video_path):
        return False
    assets = [a for a in (intro, outro) if a and os.path.exists(a)]
    if not assets:
        shutil.copy2(video_path, out_path)
        return False
    try:
        work = os.path.dirname(out_path) or "."
        os.makedirs(work, exist_ok=True)
        base = os.path.splitext(os.path.basename(out_path))[0]
        ref = _probe_streams(video_path)
        # Normalize the main clip too so concat is valid
        main_norm = os.path.join(work, "{}_main_norm.mp4".format(base))
        _normalize_to(video_path, main_norm, ref)

        files = []
        for label, asset in (("intro", intro), ("main", main_norm), ("outro", outro)):
            if label == "main" or (asset and os.path.exists(asset)):
                norm = os.path.join(work, "{}_{}_final_norm.mp4".format(base, label))
                if label == "main":
                    # main_norm is already normalized — just reuse it
                    norm = main_norm
                else:
                    _normalize_to(asset, norm, ref)
                files.append(norm)

        list_file = os.path.join(work, "{}_concat.txt".format(base))
        with open(list_file, "w", encoding="utf-8") as f:
            for fp in files:
                f.write("file '{}'\n".format(os.path.abspath(fp).replace("\\", "/")))
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", list_file,
            "-c", "copy",
            out_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=1800)
        if not os.path.isfile(out_path) or os.path.getsize(out_path) <= 0:
            raise RuntimeError("intro/outro ffmpeg produced no output")
        for fp in files + [list_file, main_norm]:
            try:
                os.remove(fp)
            except Exception:
                pass
        return True
    except Exception:
        try:
            shutil.copy2(video_path, out_path)
        except OSError:
            pass
        return False


def process_file(video_path, out_path=None, logo_path=None, position="bottom-right",
                 size_fraction=0.12, opacity=0.9, intro=None, outro=None):
    """Apply branding steps in order: watermark → intro/outro.

    ``ok`` means the requested branding operations completed. A valid
    copy-through output is still produced on failure, but ``degraded=True``
    makes that fallback visible to the caller instead of pretending success.
    """
    report = {"video": os.path.basename(video_path), "ok": False,
              "watermark": False, "intro_outro": False, "degraded": False}
    if out_path is None:
        out_path = video_path
    try:
        if not logo_path and not intro and not outro:
            if os.path.abspath(video_path) != os.path.abspath(out_path):
                shutil.copy2(video_path, out_path)
            report["copy_through"] = True
            report["ok"] = True
            return report
        staged = out_path
        if logo_path:
            if not os.path.exists(logo_path):
                report["degraded"] = True
                report["error"] = "logo file not found"
                if os.path.abspath(video_path) != os.path.abspath(out_path):
                    shutil.copy2(video_path, out_path)
            else:
                tmp = out_path + ".wm.mp4"
                report["watermark"] = apply_watermark(
                    video_path, tmp, logo_path, position, size_fraction, opacity)
                staged = tmp
                if not report["watermark"]:
                    report["degraded"] = True
                    report["error"] = "watermark rendering failed"
        if intro or outro:
            missing = [path for path in (intro, outro) if path and not os.path.exists(path)]
            if missing:
                report["degraded"] = True
                report["error"] = "intro/outro file not found"
            report["intro_outro"] = apply_intro_outro(staged, out_path, intro, outro)
            if not report["intro_outro"]:
                report["degraded"] = True
                report.setdefault("error", "intro/outro rendering failed")
        else:
            report["intro_outro"] = False
            if staged != out_path:
                os.replace(staged, out_path)
                staged = out_path
        if staged != out_path and os.path.exists(staged):
            try:
                os.remove(staged)
            except Exception:
                pass
        report["ok"] = not report["degraded"]
    except Exception as e:
        report["error"] = str(e)
        report["degraded"] = True
        if os.path.exists(video_path) and out_path != video_path:
            shutil.copy2(video_path, out_path)
    return report


def main():
    import argparse
    import json
    parser = argparse.ArgumentParser(description="ViralCutter branding (watermark + intro/outro).")
    parser.add_argument("--video", required=True)
    parser.add_argument("--out", default=None)
    parser.add_argument("--logo", default=None, help="channel logo PNG")
    parser.add_argument("--position", default="bottom-right",
                        choices=list(POSITIONS))
    parser.add_argument("--size-fraction", type=float, default=0.12)
    parser.add_argument("--opacity", type=float, default=0.9)
    parser.add_argument("--intro", default=None, help="intro clip (mp4)")
    parser.add_argument("--outro", default=None, help="outro clip (mp4)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = process_file(args.video, args.out, args.logo, args.position,
                          args.size_fraction, args.opacity, args.intro, args.outro)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("{}: watermark={} intro/outro={}".format(
            report["video"], report["watermark"], report["intro_outro"]))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
