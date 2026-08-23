# -*- coding: utf-8 -*-
"""ViralCutter performance analytics — YouTube Analytics API (Roadmap 5.4).

The missing loop: the tool makes clips, but nothing tells you WHICH clips
actually performed. This module pulls your channel's real numbers (views,
watch time, likes, top videos) so future clip selection can learn from
outcomes — same spirit as the strike feedback loop, but for growth.

Setup (once):
  1. https://console.cloud.google.com/apis/credentials → OAuth 2.0 Client ID
     (Desktop app) → save JSON as client_secrets.json (or YT_CLIENT_SECRETS_FILE)
  2. Enable BOTH APIs in the console:
        YouTube Data API v3        (titles)
        YouTube Analytics API      (the numbers)
  3. python -m scripts.analytics --check      # verify setup, get a token
  4. python -m scripts.analytics --summary    # channel totals
     python -m scripts.analytics --top --limit 10
     python -m scripts.analytics --trends --days 28
     python -m scripts.analytics --export analytics_report.json

The OAuth token is stored at ~/.viralcutter/analytics_token.json (read-only
scopes only — this tool can never modify anything on your channel).
"""
import argparse
import json
import os
import sys

TOKEN_ENV = "YT_ANALYTICS_TOKEN_FILE"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


# --------------------------------------------------------------------------
# Auth (mirrors upload_gate's pattern)
# --------------------------------------------------------------------------
def _secrets_path():
    return os.getenv("YT_CLIENT_SECRETS_FILE") or os.path.join(os.getcwd(), "client_secrets.json")


def _token_path():
    return os.getenv(TOKEN_ENV) or os.path.join(
        os.path.expanduser("~"), ".viralcutter", "analytics_token.json")


def load_credentials():
    """Return valid credentials, running the OAuth consent flow on first use."""
    secrets = _secrets_path()
    token_path = _token_path()
    if not os.path.exists(secrets) and not os.path.exists(token_path):
        raise RuntimeError(
            "YouTube Analytics needs OAuth credentials.\n"
            "  1) Get client_secrets.json: https://console.cloud.google.com/apis/credentials\n"
            "     (OAuth 2.0 Client ID → Desktop app)\n"
            "  2) Enable 'YouTube Data API v3' + 'YouTube Analytics API' in the console\n"
            "  3) Save the JSON as client_secrets.json (or set YT_CLIENT_SECRETS_FILE)\n"
            "  Then run: python -m scripts.analytics --check")

    import google.auth.transport.requests as g_requests
    from google.oauth2.credentials import Credentials

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(g_requests.Request())
        if creds and creds.valid:
            return creds
    if not os.path.exists(secrets):
        raise RuntimeError("client_secrets.json not found (set YT_CLIENT_SECRETS_FILE).")
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(secrets, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())
    return creds


def _build_services():
    try:
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError("analytics needs: pip install -r requirements-upload.txt") from None
    creds = load_credentials()
    return (build("youtubeAnalytics", "v2", credentials=creds),
            build("youtube", "v3", credentials=creds))


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------
def _date_range(days):
    import datetime
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def fetch_summary(ya, days=28):
    start, end = _date_range(days)
    resp = ya.reports().query(
        ids="channel==MINE",
        startDate=start, endDate=end,
        metrics="views,estimatedMinutesWatched,averageViewDuration,"
                "likes,comments,subscribersGained,shares",
    ).execute()
    rows = (resp.get("rows") or [[]])[0]
    cols = [c["name"] for c in resp.get("columnHeaders", [])]
    return {"days": days, "start": start, "end": end,
            "metrics": dict(zip(cols, rows))}


def fetch_top_videos(ya, yt, days=28, limit=10):
    start, end = _date_range(days)
    resp = ya.reports().query(
        ids="channel==MINE",
        startDate=start, endDate=end,
        metrics="views,estimatedMinutesWatched,averageViewDuration",
        dimensions="video",
        sort="-views",
        maxResults=limit,
    ).execute()
    rows = resp.get("rows") or []
    video_ids = [r[0] for r in rows if r and r[0] != "(none)"]
    titles = {}
    if video_ids:
        for i in range(0, len(video_ids), 50):
            chunk = ",".join(video_ids[i:i + 50])
            try:
                vresp = yt.videos().list(id=chunk, part="snippet").execute()
                for item in vresp.get("items", []):
                    titles[item["id"]] = (item.get("snippet") or {}).get("title", "?")
            except Exception:
                pass
    out = []
    for r in rows:
        vid, views, minutes, avg_dur = (list(r) + [None, None, None, None])[:4]
        out.append({"video_id": vid, "title": titles.get(vid, "(none)"),
                    "views": views or 0, "watch_minutes": round(minutes or 0, 1),
                    "avg_duration_s": round(avg_dur or 0, 1)})
    return {"days": days, "top": out}


def fetch_trends(ya, days=28):
    start, end = _date_range(days)
    resp = ya.reports().query(
        ids="channel==MINE",
        startDate=start, endDate=end,
        metrics="views,estimatedMinutesWatched",
        dimensions="day",
        sort="day",
    ).execute()
    rows = resp.get("rows") or []
    return {"days": days, "start": start, "end": end,
            "points": [{"date": r[0], "views": r[1], "watch_minutes": round(r[2], 1)}
                       for r in rows if len(r) >= 3]}


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def _fmt_int(n):
    if n is None:
        return "0"
    n = int(n)
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000)
    if n >= 1_000:
        return "%.1fk" % (n / 1_000)
    return str(n)


def format_summary(summary):
    m = summary["metrics"]
    lines = ["📈 Channel performance (%d days: %s → %s)" % (
        summary["days"], summary["start"], summary["end"]),
        "  views:                    %s" % _fmt_int(m.get("views", 0)),
        "  watch time:               %s min" % _fmt_int(m.get("estimatedMinutesWatched", 0)),
        "  avg view duration:        %ss" % round(m.get("averageViewDuration") or 0, 1),
        "  likes / comments:         %s / %s" % (_fmt_int(m.get("likes", 0)), _fmt_int(m.get("comments", 0))),
        "  shares:                   %s" % _fmt_int(m.get("shares", 0)),
        "  new subscribers:          %s" % _fmt_int(m.get("subscribersGained", 0))]
    return "\n".join(lines)


def format_top(top):
    lines = ["🏆 Top videos (%d days):" % top["days"]]
    for i, v in enumerate(top["top"], 1):
        lines.append("  %2d. %-50s %s views · %ss avg" % (
            i, (v["title"] or "")[:50], _fmt_int(v["views"]), v["avg_duration_s"]))
    return "\n".join(lines)


def format_trends(trends):
    pts = trends["points"]
    lines = ["📅 Daily views (%d days):" % trends["days"]]
    total = sum(p["views"] for p in pts)
    if pts:
        peak = max(pts, key=lambda p: p["views"])
        lines.append("  total: %s · peak: %s on %s" % (
            _fmt_int(total), _fmt_int(peak["views"]), peak["date"]))
    else:
        lines.append("  no data yet — publish some Shorts first!")
    return "\n".join(lines)


def build_report(summary=None, top=None, trends=None):
    report = {}
    if summary is not None:
        report["summary"] = summary
    if top is not None:
        report["top_videos"] = top
    if trends is not None:
        report["trends"] = trends
    return report


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="ViralCutter performance analytics — YouTube Analytics API (5.4)")
    parser.add_argument("--check", action="store_true", help="verify OAuth setup + token")
    parser.add_argument("--summary", action="store_true", help="channel totals")
    parser.add_argument("--top", action="store_true", help="top videos")
    parser.add_argument("--trends", action="store_true", help="daily views")
    parser.add_argument("--days", type=int, default=28, help="lookback days (default 28)")
    parser.add_argument("--limit", type=int, default=10, help="top N videos (default 10)")
    parser.add_argument("--export", default=None, help="also write the report JSON to this path")
    args = parser.parse_args(argv)

    if args.check:
        try:
            token_path = _token_path()
            creds = load_credentials()
            print("✅ OAuth ready — token: %s" % token_path)
            print("   scopes: %s" % (creds.scopes or SCOPES))
            return 0
        except RuntimeError as e:
            print("❌ %s" % e)
            return 1
        except Exception as e:
            print("❌ setup check failed: %s" % e)
            return 1

    if not (args.summary or args.top or args.trends):
        parser.print_help()
        return 0

    try:
        ya, yt = _build_services()
        summary = fetch_summary(ya, args.days) if args.summary else None
        top = fetch_top_videos(ya, yt, args.days, args.limit) if args.top else None
        trends = fetch_trends(ya, args.days) if args.trends else None
    except Exception as e:
        print("❌ analytics failed: %s" % e)
        return 1

    if summary:
        print(format_summary(summary))
        print()
    if top:
        print(format_top(top))
        print()
    if trends:
        print(format_trends(trends))

    if args.export:
        report = build_report(summary=summary, top=top, trends=trends)
        with open(args.export, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print("💾 report saved → %s" % args.export)
    return 0


if __name__ == "__main__":
    sys.exit(main())
