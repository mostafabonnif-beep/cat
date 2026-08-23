# -*- coding: utf-8 -*-
"""
Background Music + Auto-Duck.

Roadmap item 3.3 ("موسيقى خلفية + Auto-Duck"). Lifts the pacing of clips
with a royalty-free music bed that automatically ducks (sidechain
compression) whenever the person is speaking:

    apply_background_music(video_path, music_path, out_path, ...)
      * loops the music to the clip length (no fade-out required by user)
      * sidechaincompress: music volume drops ~12 dB under speech
      * amix keeps the original speech at full volume on top

Safe-by-default: when the music file is missing the clip is copied
unchanged and the report says why. Music is looked up in, in order:
explicit --music path → <project>/music/ folder → ./music/ folder.
"""

import os
import shutil
import subprocess


def find_music_file(music_path=None, project_folder=None, music_dir=None):
    """Locate a music file. Returns a path or None."""
    candidates = []
    if music_path:
        candidates.append(music_path)
    base = music_dir or (os.path.join(project_folder, "music") if project_folder else None)
    if base and os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            if name.lower().endswith((".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac")):
                candidates.append(os.path.join(base, name))
    if project_folder is None:
        local_music = os.path.join(os.getcwd(), "music")
        if os.path.isdir(local_music):
            for name in sorted(os.listdir(local_music)):
                if name.lower().endswith((".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac")):
                    candidates.append(os.path.join(local_music, name))
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def _has_audio_stream(video_path):
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=30)
        return bool(res.stdout.strip())
    except Exception:
        return None


def apply_background_music(video_path, music_path, out_path,
                           music_volume=0.15, duck_threshold=0.02,
                           duck_ratio=10, duck_attack=80, duck_release=500):
    """Add music with speech ducking. Returns a report dict (never raises)."""
    report = {"video": os.path.basename(video_path), "ok": False}
    try:
        if not os.path.exists(video_path):
            return report
        if not music_path or not os.path.exists(music_path):
            report["skipped"] = "no music file found"
            shutil.copy2(video_path, out_path)
            report["ok"] = True
            return report
        has_audio = _has_audio_stream(video_path)
        if has_audio is None:
            report["error"] = "could not verify the clip audio stream with ffprobe"
            shutil.copy2(video_path, out_path)
            return report
        if not has_audio:
            report["skipped"] = "clip has no audio stream"
            shutil.copy2(video_path, out_path)
            report["ok"] = True
            return report

        music_volume = max(0.02, min(float(music_volume), 0.35))
        duck_threshold = max(0.001, min(float(duck_threshold), 0.5))
        duck_ratio = max(1.0, min(float(duck_ratio), 20.0))
        fc = (
            "[1:a]volume={vol},asplit=2[bg1][bg2];"
            "[0:a][bg1]sidechaincompress="
            "threshold={thr}:ratio={ratio}:attack={att}:release={rel}[duck];"
            "[duck][bg2]amix=inputs=2:duration=first:dropout_transition=0[mixed];"
            "[mixed]alimiter=limit=0.95:attack=5:release=50[a]"
        ).format(vol=music_volume, thr=duck_threshold, ratio=duck_ratio,
                 att=duck_attack, rel=duck_release)
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", video_path,
            "-stream_loop", "-1", "-i", music_path,
            "-filter_complex", fc,
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart",
            "-shortest",
            out_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=1800)
        report.update({"ok": True, "music": os.path.basename(music_path),
                       "duck": {"threshold": duck_threshold, "ratio": duck_ratio},
                       "limiter": {"limit": 0.95, "attack_ms": 5, "release_ms": 50}})
    except Exception as e:
        report["error"] = str(e)
        if os.path.exists(video_path) and out_path != video_path:
            shutil.copy2(video_path, out_path)
    return report


def main():
    import argparse
    import json
    parser = argparse.ArgumentParser(description="ViralCutter background music + auto-duck.")
    parser.add_argument("--video", required=True)
    parser.add_argument("--music", default=None, help="music file (or use music/ folder)")
    parser.add_argument("--project", default=None, help="project folder (music/ lookup)")
    parser.add_argument("--out", default=None)
    parser.add_argument("--volume", type=float, default=0.15)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    music = find_music_file(args.music, args.project)
    out = args.out or args.video
    report = apply_background_music(args.video, music, out, music_volume=args.volume)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        if report.get("skipped"):
            print("{}: {}".format(report["video"], report["skipped"]))
        else:
            print("{}: music added {}".format(
                report["video"], report.get("music", "?")))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
