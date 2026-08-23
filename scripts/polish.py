# -*- coding: utf-8 -*-
"""
Polish — one-command professional editing pass over the `final/` folder.

Runs the Sprint-3 enhancement chain on every rendered clip:

    jump cuts (silence/filler removal)  → punch-in zoom → background music
    (auto-duck) → watermark → intro/outro

Output goes to `final_polished/` (burn_subtitles now prefers that folder).
Every stage is independently optional and safe: a missing asset (music,
logo, intro/outro) only skips that stage, never the clip.
"""

import glob
import json
import os
import shutil
import subprocess
import sys

from scripts import (
    auto_sfx,
    background_music,
    branding,
    broll_engine,
    jump_cuts,
    punch_zoom,
    visual_hooks,
)

STAGE_ORDER = ["jump_cuts", "punch_zoom", "background_music", "visual_hooks", "broll", "auto_sfx", "branding"]


def _video_files(folder):
    if not os.path.isdir(folder):
        return []
    return sorted(
        f for f in glob.glob(os.path.join(folder, "*.mp4"))
        if "temp_video_no_audio" not in os.path.basename(f))


def _subs_json_for(final_dir, video_file, subs_dir):
    """Map a final/*.mp4 to its subs/*_processed.json (word timings)."""
    name = os.path.splitext(os.path.basename(video_file))[0]
    candidates = [
        os.path.join(subs_dir, name + "_processed.json"),
        os.path.join(subs_dir, name + ".json"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def _probe_duration(path):
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30)
        return float(res.stdout.strip())
    except Exception:
        return 0.0


def _stage_outcome(name, data):
    """Classify a requested stage as applied, skipped or failed."""
    if not isinstance(data, dict):
        return "failed"
    if data.get("error") or data.get("ok") is False:
        return "failed"
    if name == "jump_cuts":
        return "applied" if data.get("cuts") else "skipped"
    if name == "punch_zoom":
        return "applied" if data.get("punches") else "skipped"
    if name == "background_music":
        return "applied" if data.get("music") and not data.get("skipped") else "skipped"
    if name == "visual_hooks":
        return "applied" if data.get("count") else "skipped"
    if name == "broll":
        return "applied" if data.get("asset") else "skipped"
    if name == "auto_sfx":
        return "applied" if data.get("count") else "skipped"
    if name == "branding":
        return "applied" if data.get("watermark") or data.get("intro_outro") else "skipped"
    return "applied"


def _finalize_stage_lists(stage_report):
    requested = list(stage_report.get("requested_stages", []))
    applied, skipped, failed = [], [], []
    for name in requested:
        outcome = _stage_outcome(name, stage_report.get("stages", {}).get(name))
        if outcome == "applied":
            applied.append(name)
        elif outcome == "skipped":
            skipped.append(name)
        else:
            failed.append(name)
    stage_report["applied_stages"] = applied
    stage_report["skipped_stages"] = skipped
    stage_report["failed_stages"] = failed
    return stage_report


def retime_subs(subs_json_path, cuts, intro_duration=0.0):
    """Re-time word/segment timings after jump cuts + intro prepend.

    Keeps burned subtitles in sync with the polished video:
      new_t = (t − removed_before(t)) + intro_duration
    Words/segments fully removed by a cut are dropped.
    Overwrites the file in place (called BEFORE adjust_subtitles).
    """
    if not subs_json_path or not os.path.exists(subs_json_path):
        return None
    try:
        with open(subs_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    cuts = [(float(s), float(e)) for s, e in (cuts or []) if e > s]

    def removed_before(t):
        return sum(e - s for s, e in cuts if e <= t)

    def fully_cut(ws, we):
        return any(s <= ws and we <= e for s, e in cuts)

    new_segments = []
    for seg in data.get("segments", []):
        try:
            s0, e0 = float(seg["start"]), float(seg["end"])
        except Exception:
            continue
        if fully_cut(s0, e0):
            continue
        ns = s0 - removed_before(s0) + intro_duration
        ne = e0 - removed_before(e0) + intro_duration
        if ne <= ns:
            continue
        out_seg = dict(seg)
        out_seg["start"], out_seg["end"] = ns, ne
        words = seg.get("words")
        if words:
            kept = []
            for w in words:
                try:
                    ws, we = float(w["start"]), float(w["end"])
                except Exception:
                    continue
                if fully_cut(ws, we):
                    continue
                nws = ws - removed_before(ws) + intro_duration
                nwe = we - removed_before(we) + intro_duration
                if nwe > nws:
                    nw = dict(w)
                    nw["start"], nw["end"] = nws, nwe
                    kept.append(nw)
            if kept:
                out_seg["words"] = kept
        new_segments.append(out_seg)

    data["segments"] = new_segments
    try:
        with open(subs_json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        return None
    return data


def polish_project(project_folder, enable=None, keywords=None,
                   music_path=None, logo_path=None, intro=None, outro=None,
                   music_volume=0.15, punch_zoom_amount=1.18,
                   punch_auto_interval=0.0, zoom_keywords=None,
                   watermark_position="bottom-right", watermark_size=0.12,
                   watermark_opacity=0.9, broll_path=None, broll_query=None,
                   broll_api_key=None, broll_opacity=0.28, visual_hook_max=8,
                   visual_hook_accent="0x00d9ff", sfx_dir=None, sfx_volume=0.22,
                   verbose=True):
    """Run the enhancement chain on every clip in final/ → final_polished/.

    `enable` is a set/list of stages from STAGE_ORDER; None = all.
    Returns a list of per-clip reports.
    """
    enable = set(enable or STAGE_ORDER)
    final_dir = os.path.join(project_folder, "final")
    subs_dir = os.path.join(project_folder, "subs")
    out_dir = os.path.join(project_folder, "final_polished")
    os.makedirs(out_dir, exist_ok=True)

    files = _video_files(final_dir)
    reports = []
    for index, video_file in enumerate(files):
        base = os.path.basename(video_file)
        tmp_dir = os.path.join(project_folder, ".polish_tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        current = video_file
        subs_json = _subs_json_for(final_dir, video_file, subs_dir)
        applied_cuts = []
        intro_duration = 0.0
        stage_report = {
            "video": base,
            "input": os.path.abspath(video_file),
            "output": None,
            "requested_stages": [name for name in STAGE_ORDER if name in enable],
            "applied_stages": [],
            "skipped_stages": [],
            "failed_stages": [],
            "stages": {},
            "fallback_used": False,
            "media_validated": False,
            "degraded": False,
            "errors": [],
        }
        try:
            # 1. jump cuts
            if "jump_cuts" in enable:
                next_work = os.path.join(tmp_dir, "jc_" + base)
                rep = jump_cuts.process_file(current, subs_json=subs_json, out_path=next_work)
                stage_report["stages"]["jump_cuts"] = {
                    "ok": bool(rep.get("ok")),
                    "cuts": len(rep.get("cuts", [])),
                    "removed": rep.get("removed", 0.0),
                    "error": rep.get("error"),
                }
                if rep.get("ok"):
                    applied_cuts = rep.get("cuts", [])
                    current = next_work
                else:
                    stage_report["degraded"] = True
            # 2. punch zoom
            if "punch_zoom" in enable:
                subs = _subs_json_for(final_dir, video_file, subs_dir)
                next_work = os.path.join(tmp_dir, "pz_" + base)
                rep = punch_zoom.process_file(
                    current, subs_json=subs, out_path=next_work,
                    keywords=zoom_keywords, zoom=punch_zoom_amount,
                    auto_interval=punch_auto_interval)
                stage_report["stages"]["punch_zoom"] = {
                    "ok": bool(rep.get("ok")),
                    "punches": rep.get("count", 0),
                    "error": rep.get("error"),
                }
                if rep.get("ok"):
                    current = next_work
                else:
                    stage_report["degraded"] = True
            # 3. background music
            if "background_music" in enable:
                music = background_music.find_music_file(music_path, project_folder)
                next_work = os.path.join(tmp_dir, "bm_" + base)
                rep = background_music.apply_background_music(
                    current, music, next_work, music_volume=music_volume)
                requested_music = bool(music_path)
                music_failed = bool(rep.get("error")) or (requested_music and not rep.get("music"))
                stage_report["stages"]["background_music"] = {
                    "ok": bool(rep.get("ok")) and not music_failed,
                    "music": rep.get("music"),
                    "skipped": rep.get("skipped"),
                    "error": rep.get("error") or ("requested music was not applied" if music_failed else None),
                }
                if rep.get("ok") and not music_failed:
                    current = next_work
                elif music_failed:
                    stage_report["degraded"] = True
            # 4. Visual Hooks (transcript-timed, deterministic, no network)
            if "visual_hooks" in enable:
                next_work = os.path.join(tmp_dir, "vh_" + base)
                words = visual_hooks.load_words(subs_json)
                hooks = visual_hooks.plan_visual_hooks(words, max_hooks=visual_hook_max)
                rep = visual_hooks.apply_visual_hooks(
                    current, next_work, hooks, accent=visual_hook_accent,
                )
                stage_report["stages"]["visual_hooks"] = {
                    "ok": bool(rep.get("ok")),
                    "count": rep.get("count", 0), "hooks": hooks,
                    "error": rep.get("error"),
                }
                if rep.get("ok"):
                    current = next_work
                else:
                    stage_report["degraded"] = True

            # 5. B-Roll (optional; no key means no network request)
            if "broll" in enable:
                broll_report = {"skipped": None, "asset": None, "attribution": None}
                asset = broll_path if broll_path and os.path.exists(broll_path) else None
                query = broll_query
                if not asset and broll_api_key:
                    search = broll_engine.search_pexels_videos(
                        query or "abstract background", broll_api_key,
                    )
                    if search.get("ok") and search.get("items"):
                        selected = search["items"][0]
                        downloaded = os.path.join(tmp_dir, "broll_{}.mp4".format(index))
                        download = broll_engine.download_asset(
                            selected.get("download_url"), downloaded,
                        )
                        if download.get("ok"):
                            asset = downloaded
                            broll_report["attribution"] = {
                                key: selected.get(key)
                                for key in ("provider", "url", "photographer", "photographer_url", "id")
                                if selected.get(key) is not None
                            }
                        else:
                            broll_report["skipped"] = download.get("error")
                    else:
                        broll_report["skipped"] = search.get("error", "no_results")
                if asset:
                    next_work = os.path.join(tmp_dir, "broll_" + base)
                    rep = broll_engine.overlay_broll(
                        current, asset, next_work, start=0.0,
                        end=_probe_duration(current), opacity=broll_opacity,
                    )
                    broll_report["asset"] = os.path.basename(asset)
                    broll_report["applied"] = bool(rep.get("ok"))
                    if rep.get("ok"):
                        current = next_work
                    else:
                        broll_report["skipped"] = rep.get("error")
                else:
                    broll_report["skipped"] = broll_report["skipped"] or "no_asset_or_pexels_key"
                stage_report["stages"]["broll"] = broll_report

            # 6. Auto SFX (local assets only)
            if "auto_sfx" in enable:
                next_work = os.path.join(tmp_dir, "sfx_" + base)
                words = visual_hooks.load_words(subs_json)
                events = auto_sfx.plan_sfx(words)
                rep = auto_sfx.apply_auto_sfx(
                    current, next_work, events, sfx_dir, volume=sfx_volume,
                )
                stage_report["stages"]["auto_sfx"] = {
                    "ok": bool(rep.get("ok")),
                    "count": rep.get("count", 0),
                    "events": rep.get("events", []),
                    "missing_assets": rep.get("missing_assets", []),
                    "error": rep.get("error"),
                }
                if rep.get("ok"):
                    current = next_work
                else:
                    stage_report["degraded"] = True

            # 7. branding
            if "branding" in enable:
                if intro and os.path.exists(intro):
                    intro_duration = _probe_duration(intro)
                next_work = os.path.join(tmp_dir, "br_" + base)
                rep = branding.process_file(
                    current, out_path=next_work, logo_path=logo_path,
                    position=watermark_position, size_fraction=watermark_size,
                    opacity=watermark_opacity, intro=intro, outro=outro)
                stage_report["stages"]["branding"] = {
                    "ok": bool(rep.get("ok")),
                    "watermark": rep.get("watermark"),
                    "intro_outro": rep.get("intro_outro"),
                    "degraded": bool(rep.get("degraded")),
                    "error": rep.get("error"),
                }
                if rep.get("ok"):
                    current = next_work
                else:
                    stage_report["degraded"] = True

            # re-time subtitles for the final polished video (jump cuts + intro)
            if subs_json and (applied_cuts or intro_duration):
                retime_subs(subs_json, applied_cuts, intro_duration)
                stage_report["retimed_subs"] = True

            output_path = os.path.join(out_dir, base)
            if os.path.abspath(current) == os.path.abspath(video_file):
                shutil.copy2(video_file, output_path)
                stage_report["copy_through"] = True
            else:
                os.replace(current, output_path)
                stage_report["copy_through"] = False
            output_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
            output_duration = _probe_duration(output_path)
            stage_report["output"] = os.path.abspath(output_path)
            stage_report["output_size"] = output_size
            stage_report["output_duration"] = output_duration
            if output_size <= 0 or output_duration <= 0:
                raise RuntimeError("polish output is missing or has no readable duration")
            stage_report["media_validated"] = True
            _finalize_stage_lists(stage_report)
            stage_report["quality_status"] = (
                "enhanced" if stage_report["applied_stages"] and not stage_report.get("degraded")
                else "partial" if stage_report["applied_stages"] and stage_report.get("degraded")
                else "fallback"
            )
            stage_report["ok"] = not stage_report.get("degraded", False)
        except Exception as e:
            stage_report["ok"] = False
            stage_report["degraded"] = True
            stage_report["error"] = str(e)
            stage_report["errors"].append(str(e))
            _finalize_stage_lists(stage_report)
            try:
                fallback_path = os.path.join(out_dir, base)
                shutil.copy2(video_file, fallback_path)
                stage_report["fallback_used"] = True
                stage_report["fallback_output"] = os.path.abspath(fallback_path)
                stage_report["output"] = os.path.abspath(fallback_path)
                stage_report["output_size"] = os.path.getsize(fallback_path)
                stage_report["output_duration"] = _probe_duration(fallback_path)
                stage_report["media_validated"] = (
                    stage_report["output_size"] > 0 and stage_report["output_duration"] > 0
                )
                stage_report["quality_status"] = "fallback" if stage_report["media_validated"] else "failed"
            except OSError as copy_error:
                stage_report["fallback_error"] = str(copy_error)
                stage_report["quality_status"] = "failed"
        finally:
            for leftover in glob.glob(os.path.join(tmp_dir, "*")):
                try:
                    os.remove(leftover)
                except Exception:
                    pass
        if verbose:
            print(json.dumps(stage_report, ensure_ascii=False))
        if stage_report.get("error") and stage_report["error"] not in stage_report["errors"]:
            stage_report["errors"].append(stage_report["error"])
        _finalize_stage_lists(stage_report)
        if not stage_report.get("quality_status"):
            stage_report["quality_status"] = "failed" if not stage_report.get("media_validated") else "fallback"
        reports.append(stage_report)

    # remove empty temp dir
    try:
        os.rmdir(tmp_dir)
    except Exception:
        pass
    report_path = os.path.join(project_folder, "polish_report.json")
    report_payload = {
        "version": 1,
        "stages": list(STAGE_ORDER),
        "clips": reports,
        "summary": {
            "total": len(reports),
            "ok": sum(1 for item in reports if item.get("ok")),
            "degraded": sum(1 for item in reports if item.get("degraded")),
            "enhanced": sum(1 for item in reports if item.get("quality_status") == "enhanced"),
            "partial": sum(1 for item in reports if item.get("quality_status") == "partial"),
            "fallback": sum(1 for item in reports if item.get("quality_status") == "fallback"),
            "failed": sum(1 for item in reports if item.get("quality_status") == "failed"),
        },
    }
    temp_report = report_path + ".tmp"
    try:
        with open(temp_report, "w", encoding="utf-8") as stream:
            json.dump(report_payload, stream, ensure_ascii=False, indent=2)
        os.replace(temp_report, report_path)
    except OSError:
        try:
            if os.path.exists(temp_report):
                os.remove(temp_report)
        except OSError:
            pass
    return reports


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ViralCutter polish pass (Sprint 3/4).")
    parser.add_argument("--project", required=True, help="project folder")
    parser.add_argument("--stages", default=",".join(STAGE_ORDER),
                        help="comma-separated stages: " + ",".join(STAGE_ORDER))
    parser.add_argument("--keywords", default=None,
                        help="punch-zoom keywords (comma-separated)")
    parser.add_argument("--music", default=None, help="music file path")
    parser.add_argument("--music-volume", type=float, default=0.15)
    parser.add_argument("--logo", default=None, help="channel logo PNG")
    parser.add_argument("--intro", default=None)
    parser.add_argument("--outro", default=None)
    parser.add_argument("--zoom", type=float, default=1.18)
    parser.add_argument("--zoom-interval", type=float, default=0.0,
                        help="auto punch every N seconds")
    parser.add_argument("--watermark-position", default="bottom-right")
    parser.add_argument("--broll", default=None, help="local B-roll video asset")
    parser.add_argument("--broll-query", default=None, help="Pexels B-roll search query")
    parser.add_argument("--broll-opacity", type=float, default=0.28)
    parser.add_argument("--sfx-dir", default=None, help="folder containing pop/whoosh/impact audio assets")
    parser.add_argument("--sfx-volume", type=float, default=0.22)
    args = parser.parse_args()

    reports = polish_project(
        args.project,
        enable=[s for s in args.stages.split(",") if s.strip()],
        keywords=args.keywords,
        music_path=args.music,
        music_volume=args.music_volume,
        logo_path=args.logo,
        intro=args.intro,
        outro=args.outro,
        punch_zoom_amount=args.zoom,
        punch_auto_interval=args.zoom_interval,
        zoom_keywords=args.keywords,
        watermark_position=args.watermark_position,
        broll_path=args.broll,
        broll_query=args.broll_query,
        broll_api_key=os.getenv("PEXELS_API_KEY"),
        broll_opacity=args.broll_opacity,
        sfx_dir=args.sfx_dir,
        sfx_volume=args.sfx_volume,
    )
    ok = sum(1 for r in reports if r.get("ok"))
    print("polish: {}/{} clips ok".format(ok, len(reports)))
    return 0 if ok == len(reports) and reports else 1


if __name__ == "__main__":
    sys.exit(main())
