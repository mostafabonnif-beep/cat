# -*- coding: utf-8 -*-
"""
Live-stream downloader (v7.20) — تحميل البث المباشر تلقائياً بعد انتهائه.

Handles URLs like https://youtube.com/live/gowpuk4jk5U:

* **status(url)** — asks yt-dlp for the live state without downloading:
  ``is_upcoming`` (جدول زمني مستقبلي), ``is_live`` (مباشر الآن),
  ``is_replay``/``was_live`` (انتهى — يمكن التحميل), ``not_live``.
* **wait_until_ended(url)** — polls until the stream finishes. Works for
  both upcoming streams (waits for the start, then for the end) and live
  streams (waits for the end). ``--max-wait`` bounds the total wait.
* **download_when_live_ends(url)** — waits, then hands off to the regular
  downloader (``download_video.download``) so cookies/quality/subtitles all
  keep working. The post-live VOD keeps the same video id, so the same URL
  becomes downloadable automatically once the stream ends.

All errors are graceful: no yt-dlp → clear message; network drop → retry
with backoff; timeout → informative error. Nothing raises on import.
"""

import argparse
import os
import sys
import time

try:
    import yt_dlp
    HAS_YTDLP = True
except ImportError:  # pragma: no cover - environment dependent
    yt_dlp = None
    HAS_YTDLP = False

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import download_video  # noqa: E402

DEFAULT_POLL_SECONDS = 60
DEFAULT_MAX_WAIT_SECONDS = 6 * 3600   # 6 hours
MAX_POLL_BACKOFF = 10 * 60            # cap the backoff at 10 minutes

LIVE_KEYS = ("is_live", "live_status", "was_live", "is_upcoming")


def _require_ytdlp():
    if not HAS_YTDLP:
        raise RuntimeError(
            "yt-dlp is not installed. Install it with: pip install yt-dlp[default]")
    return yt_dlp


def _info_options(cookies_from_browser=None, cookies_file=None):
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    opts.update(download_video._runtime_options())
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    if cookies_file:
        opts["cookiefile"] = cookies_file
    return opts


def fetch_status(url, cookies_from_browser=None, cookies_file=None):
    """Return a JSON-safe status dict for a (possibly live) video URL.

    Never raises for network/parse issues — those become ``{"status":
    "unknown", "error": ...}``. ``status`` is one of: upcoming, live,
    ended (replay/was_live/post_live), not_live, unknown.
    """
    _require_ytdlp()
    opts = _info_options(cookies_from_browser, cookies_file)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        return _classify_error(exc, url)

    return _classify_info(info, url)


def _classify_error(exc, url):
    """Map known yt-dlp errors (upcoming/live events raise instead of
    returning info) to a status dict."""
    msg = str(exc).lower()
    if "will begin in a few moments" in msg or "is scheduled" in msg \
            or "will premiere" in msg or "not yet started" in msg \
            or "upcoming" in msg and "live" in msg:
        return {"status": "upcoming", "url": url,
                "error": str(exc), "is_upcoming": True,
                "message": "scheduled live event — not started yet"}
    if "is live" in msg or "currently live" in msg or "streaming now" in msg:
        return {"status": "live", "url": url,
                "error": str(exc), "is_live": True}
    if "completed" in msg and ("live" in msg or "stream" in msg) \
            or "ended" in msg and "live" in msg:
        return {"status": "ended", "url": url,
                "error": str(exc), "was_live": True}
    return {"status": "unknown", "error": str(exc), "url": url}


def _classify_info(info, url):
    """Classify a successfully extracted info dict into a status dict."""
    live = bool(info.get("is_live"))
    was_live = bool(info.get("was_live"))
    upcoming = bool(info.get("is_upcoming"))
    live_status = str(info.get("live_status") or "").lower()

    if live or live_status == "is_live":
        state = "live"
    elif upcoming or live_status == "is_upcoming":
        state = "upcoming"
    elif was_live or live_status in ("was_live", "post_live", "is_replay", "ended"):
        state = "ended"
    else:
        state = "not_live"

    return {
        "status": state,
        "url": url,
        "id": str(info.get("id") or ""),
        "title": str(info.get("title") or ""),
        "channel": str(info.get("channel") or info.get("uploader") or ""),
        "duration": info.get("duration"),
        "live_status": live_status,
        "is_live": live,
        "was_live": was_live,
        "is_upcoming": upcoming,
        "available": bool(info.get("url") or info.get("requested_formats")),
    }


def wait_until_ended(url, *, poll_seconds=DEFAULT_POLL_SECONDS,
                     max_wait_seconds=DEFAULT_MAX_WAIT_SECONDS,
                     cookies_from_browser=None, cookies_file=None,
                     progress=None, sleep=None):
    """Poll until the stream is no longer live/upcoming.

    ``progress`` receives a dict after every poll (status, waited_seconds,
    elapsed, message). ``sleep`` is injectable for tests. Returns the final
    status dict (``status == "ended"`` or ``"not_live"``). Raises
    ``TimeoutError`` when ``max_wait_seconds`` elapses first.
    """
    if sleep is None:
        sleep = time.sleep
    deadline = time.monotonic() + max(0.0, float(max_wait_seconds))
    waited = 0.0
    last_state = None
    consecutive_errors = 0

    while True:
        state = fetch_status(url, cookies_from_browser, cookies_file)
        current = state.get("status")
        if current in ("ended", "not_live"):
            if progress:
                progress({"status": current, "waited_seconds": waited,
                          "elapsed": round(time.monotonic() - (deadline - max_wait_seconds), 1),
                          "message": "stream finished — ready to download"})
            return state

        if current == "live":
            message = "stream is LIVE — waiting for it to end"
            consecutive_errors = 0
        elif current == "upcoming":
            message = "stream is scheduled — waiting for start + end"
            consecutive_errors = 0
        else:
            consecutive_errors += 1
            message = "cannot read stream state ({}) — retrying".format(
                state.get("error", current))

        if progress:
            progress({"status": current or "unknown", "waited_seconds": waited,
                      "elapsed": round(time.monotonic() - (deadline - max_wait_seconds), 1),
                      "message": message})

        # backoff on repeated errors (max 10 min), fixed poll otherwise
        delay = poll_seconds if consecutive_errors == 0 else min(
            poll_seconds * (2 ** min(consecutive_errors, 4)), MAX_POLL_BACKOFF)
        sleep(max(1, delay))
        waited += delay
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "Timed out after {:.0f}s waiting for the live stream to end "
                "(last state: {}). Use --max-wait to extend.".format(
                    waited, last_state or current))


def download_when_live_ends(url, *, base_root="VIRALS", quality="best",
                            cookies_from_browser=None, cookies_file=None,
                            poll_seconds=DEFAULT_POLL_SECONDS,
                            max_wait_seconds=DEFAULT_MAX_WAIT_SECONDS,
                            progress=None, sleep=None):
    """Wait for a live stream to end, then download it as a normal VOD."""
    final = wait_until_ended(
        url, poll_seconds=poll_seconds, max_wait_seconds=max_wait_seconds,
        cookies_from_browser=cookies_from_browser, cookies_file=cookies_file,
        progress=progress, sleep=sleep)
    if progress:
        progress({"status": "downloading", "message": "stream ended — downloading VOD"})
    return download_video.download(
        url, base_root=base_root, download_subs=True, quality=quality,
        cookies_from_browser=cookies_from_browser, cookies_file=cookies_file)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Download a YouTube live stream automatically after it ends "
                    "(supports https://youtube.com/live/ID URLs).")
    parser.add_argument("url", help="YouTube URL (e.g. https://youtube.com/live/gowpuk4jk5U)")
    parser.add_argument("--check", action="store_true",
                        help="print the current live state and exit (no waiting)")
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL_SECONDS,
                        help="seconds between status polls (default: 60)")
    parser.add_argument("--max-wait", type=float, default=DEFAULT_MAX_WAIT_SECONDS,
                        help="max seconds to wait for the stream to end (default: 21600)")
    parser.add_argument("--base-root", default="VIRALS",
                        help="project root folder (default: VIRALS)")
    parser.add_argument("--quality", default="best",
                        choices=["best", "1080p", "720p", "480p"])
    parser.add_argument("--cookies-from-browser",
                        choices=["chrome", "firefox", "edge", "safari", "brave", "opera", "vivaldi"],
                        default=None)
    parser.add_argument("--cookies", default=None)
    args = parser.parse_args(argv)

    if args.check:
        state = fetch_status(args.url, args.cookies_from_browser, args.cookies)
        print("[live] status={} id={} title={!r}".format(
            state.get("status"), state.get("id"), state.get("title")))
        return 0

    def _progress(info):
        print("[live] {:.0f}s waited | {} — {}".format(
            info.get("elapsed", 0), info.get("status"), info.get("message")))

    try:
        path, folder = download_when_live_ends(
            args.url, base_root=args.base_root, quality=args.quality,
            cookies_from_browser=args.cookies_from_browser,
            cookies_file=args.cookies, poll_seconds=args.poll,
            max_wait_seconds=args.max_wait, progress=_progress)
        print("[live] downloaded: {}".format(path))
        return 0
    except TimeoutError as exc:
        print("[live] ERROR: {}".format(exc))
        return 1
    except Exception as exc:
        print("[live] ERROR: {}".format(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
