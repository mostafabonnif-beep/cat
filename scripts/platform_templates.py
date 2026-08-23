# -*- coding: utf-8 -*-
"""
Platform Templates — per-platform output presets.

Roadmap item 5.2 ("قوالب لكل منصة"). Today every platform gets the same
output; these templates encode the specs that actually matter for each
destination so the CLI can enforce sane duration/resolution defaults:

    yt_shorts    YouTube Shorts   9:16   ≤ 60s  1080x1920
    tiktok       TikTok          9:16   ≤ 90s  1080x1920
    reels        Instagram Reels 9:16   ≤ 90s  1080x1920
    yt_standard  YouTube         16:9   ≤ 10m  1920x1080

Integration: `main_improved.py --platform <name>` resolves the template,
applies its min/max duration as defaults (user CLI values still win),
records it in process_config.json, and prints the aspect hint.
"""

import json
import os
import tempfile

TEMPLATES = {
    "yt_shorts": {
        "name": "YouTube Shorts",
        "aspect": "9:16",
        "min_duration": 15,
        "max_duration": 60,
        "recommended_resolution": "1080x1920",
        "caption_note": "Keep the hook in the first 3 seconds; end-screen friendly.",
    },
    "tiktok": {
        "name": "TikTok",
        "aspect": "9:16",
        "min_duration": 15,
        "max_duration": 90,
        "recommended_resolution": "1080x1920",
        "caption_note": "TikTok favors loops — avoid hard endings.",
    },
    "reels": {
        "name": "Instagram Reels",
        "aspect": "9:16",
        "min_duration": 15,
        "max_duration": 90,
        "recommended_resolution": "1080x1920",
        "caption_note": "Reels ≤ 90s; first 3 seconds decide reach.",
    },
    "yt_standard": {
        "name": "YouTube Standard",
        "aspect": "16:9",
        "min_duration": 30,
        "max_duration": 600,
        "recommended_resolution": "1920x1080",
        "caption_note": "Long-form: use chapters and a strong thumbnail title.",
    },
}

DEFAULT_TEMPLATE = "yt_shorts"


def get_template(name):
    """Return the template dict or None for an unknown name."""
    return TEMPLATES.get((name or "").strip().lower())


def list_templates():
    return {k: v["name"] for k, v in TEMPLATES.items()}


def resolve_durations(template_name, min_duration=None, max_duration=None,
                      fallback_min=15, fallback_max=90):
    """Apply template defaults, letting explicit user values win.

    Returns (min_duration, max_duration, template).
    """
    tpl = get_template(template_name)
    if tpl is None:
        # unknown template → behave like no template
        return (min_duration or fallback_min, max_duration or fallback_max, None)
    try:
        min_d = int(min_duration if min_duration is not None else tpl["min_duration"])
        max_d = int(max_duration if max_duration is not None else tpl["max_duration"])
    except (TypeError, ValueError):
        min_d, max_d = tpl["min_duration"], tpl["max_duration"]
    min_d = max(0, min_d)
    max_d = max(min_d, max(0, max_d))
    return (min_d, max_d, tpl)


def describe(template_name):
    """Human-readable one-liner for console output."""
    tpl = get_template(template_name)
    if tpl is None:
        return "unknown platform template '{}'".format(template_name)
    return "{} ({} · {}s–{}s · {})".format(
        tpl["name"], tpl["aspect"], tpl["min_duration"],
        tpl["max_duration"], tpl["recommended_resolution"])


def save_template_choice(project_folder, template_name):
    """Record the template in the project's process_config.json (best-effort)."""
    if not template_name:
        return
    path = os.path.join(project_folder, "process_config.json")
    try:
        data = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        data["platform_template"] = template_name
        if get_template(template_name):
            data["platform_template_info"] = {
                k: v for k, v in get_template(template_name).items()
                if k != "caption_note"}
        directory = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    except Exception:
        pass


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ViralCutter platform templates.")
    parser.add_argument("--list", action="store_true", help="list templates")
    parser.add_argument("--show", default=None, help="show one template")
    parser.add_argument("--resolve", default=None,
                        help="resolve durations for a template (with --min/--max)")
    parser.add_argument("--min", type=int, default=None)
    parser.add_argument("--max", type=int, default=None)
    args = parser.parse_args()

    if args.list:
        for k, v in list_templates().items():
            print("  {:<12} {}".format(k, v))
        return 0
    if args.show:
        tpl = get_template(args.show)
        if not tpl:
            print("unknown template '{}'".format(args.show))
            return 1
        print(json.dumps(tpl, ensure_ascii=False, indent=2))
        return 0
    if args.resolve:
        min_d, max_d, tpl = resolve_durations(args.resolve, args.min, args.max)
        print("min={} max={} template={}".format(
            min_d, max_d, tpl["name"] if tpl else None))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
