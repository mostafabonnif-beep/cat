"""Learn & Performance panels for the WebUI.

Thin handlers that wrap the CLI modules (scripts.strike_feedback / scripts
.analytics) so the WebUI can call them IN-PROCESS — no subprocess, works in
both source runs and the packaged exe (modules are bundled).

Every handler returns a plain string to show in a Textbox and never raises
raw exceptions: failures come back as a readable message.
"""

import datetime
import os
from collections import Counter


def _strike():
    from scripts import strike_feedback
    return strike_feedback


def list_terms():
    try:
        sf = _strike()
        terms = sf.load_terms()
        lines = ["Custom BLOCK terms (extra_terms): %d" % len(terms["extra_terms"])]
        for t in terms["extra_terms"]:
            lines.append("  • %s  (lang=%s, sev=%s, cat=%s)" % (
                t["term"], t.get("lang", "?"), t.get("severity", "?"),
                t.get("category", "?")))
        lines.append("")
        lines.append("ALLOW terms (false-positive fixes): %d" % len(terms["allow_terms"]))
        for t in terms["allow_terms"]:
            lines.append("  • %s" % t)
        return "\n".join(lines)
    except Exception as e:
        return "❌ Could not read the term list: %s" % e


def add_term(term, severity="high", reason=""):
    if not term or not term.strip():
        return "❌ Enter a term first."
    try:
        sf = _strike()
        sf.cmd_add(term.strip(), severity=severity or "high",
                   reason=(reason or "").strip() or None)
        return "✅ Learned '%s' (severity=%s) — the safety filter now blocks it on every run." % (
            term.strip(), severity or "high")
    except Exception as e:
        return "❌ %s" % e


def allow_term(term, reason=""):
    if not term or not term.strip():
        return "❌ Enter a term first."
    try:
        sf = _strike()
        sf.cmd_allow(term.strip(), reason=(reason or "").strip() or None)
        return "✅ Allowed '%s' — excluded from the built-in blocklist." % term.strip()
    except Exception as e:
        return "❌ %s" % e


def remove_term(term):
    if not term or not term.strip():
        return "❌ Enter a term first."
    try:
        sf = _strike()
        ok = sf.cmd_remove(term.strip())
        if not ok:
            ok = sf.cmd_remove(term.strip(), allow=True)
        return "✅ Removed '%s'" % term.strip() if ok else "Not found — nothing to remove."
    except Exception as e:
        return "❌ %s" % e


def show_stats():
    try:
        sf = _strike()
        s = sf.cmd_stats()
        lines = [
            "📓 Learning journal:",
            "  events:          %d" % s["events"],
            "  by action:       %s" % s["by_action"],
            "  by severity:     %s" % s["by_severity"],
            "  by month:        %s" % s["by_month"],
            "  last event:      %s" % (s["last_event"] or "—"),
            "  active block:    %d terms" % s["active_extra_terms"],
            "  active allow:    %d terms" % s["active_allow_terms"],
        ]
        return "\n".join(lines)
    except Exception as e:
        return "❌ %s" % e


def extract_from_project(project_name, apply=False, virals_dir=None):
    if not project_name:
        return "❌ Select a project first."
    base = virals_dir or os.path.join(os.getcwd(), "VIRALS")
    project_folder = os.path.join(base, project_name)
    if not os.path.isdir(project_folder):
        return "❌ Project folder not found: %s" % project_folder
    try:
        sf = _strike()
        found = sf.extract_terms_from_project(project_folder)
        if not found:
            return "No patterns found — this project has no safety/risk reports or no blocked clips."
        lines = ["Patterns behind the blocked clips (%s):" % project_name]
        for f in found:
            lines.append("  • %-24s sev=%-6s x%d" % (f["term"], f["severity"], f["count"]))
        if apply:
            added = 0
            for f in found:
                try:
                    sf.cmd_add(f["term"], lang="auto", severity=f["severity"],
                               category="learned",
                               reason="learned from WebUI (project %s)" % project_name,
                               source="scorecard", project=project_folder)
                    added += 1
                except Exception:
                    pass
            lines.append("")
            lines.append("✅ Learned %d term(s) — next runs will block them earlier." % added)
        else:
            lines.append("")
            lines.append("(tick 'Apply' and run again to teach them to the tool)")
        return "\n".join(lines)
    except Exception as e:
        return "❌ %s" % e


def local_publish_summary(project_name=None, days=28, virals_dir=None):
    """Summarize local publish_history files without network access or secrets."""
    from webui import publish_history

    try:
        lookback = max(1, int(float(days or 28)))
    except (TypeError, ValueError):
        lookback = 28
    base = os.path.abspath(virals_dir or os.path.join(os.getcwd(), "VIRALS"))
    if not os.path.isdir(base):
        return "❌ VIRALS folder not found: %s" % base
    if project_name:
        candidate = os.path.abspath(os.path.join(base, os.path.basename(str(project_name))))
        try:
            if os.path.commonpath([base, candidate]) != base or not os.path.isdir(candidate):
                return "❌ Project folder not found: %s" % project_name
        except ValueError:
            return "❌ Invalid project selection."
        projects = [candidate]
        scope = os.path.basename(candidate)
    else:
        projects = [os.path.join(base, name) for name in sorted(os.listdir(base))
                    if os.path.isdir(os.path.join(base, name))]
        scope = "كل المشاريع"

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=lookback)
    events = []
    for folder in projects:
        for event in publish_history.load(folder, limit=5000):
            timestamp = event.get("timestamp")
            try:
                parsed = datetime.datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                parsed = datetime.datetime.now(datetime.timezone.utc)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            if parsed >= cutoff:
                row = dict(event)
                row["_project"] = os.path.basename(folder)
                events.append(row)

    if not events:
        return "📊 سجل الرفع المحلي (%s، %d يوماً): لا توجد أحداث بعد." % (scope, lookback)
    status_counts = Counter(str(event.get("status") or "unknown").lower() for event in events)
    platform_counts = Counter(str(event.get("platform") or "unknown") for event in events)
    privacy_counts = Counter(str(event.get("privacy_status") or "unspecified") for event in events)
    successful = [event for event in events if str(event.get("status") or "").lower() in {"uploaded", "scheduled"}]
    failed = status_counts.get("failed", 0)
    dry_runs = sum(1 for event in events if str(event.get("status") or "").lower() in {"dry-run", "dry_run", "simulated"})
    duplicates = sum(1 for event in events if "duplicate" in str(event.get("status") or "").lower())
    success_rate = (len(successful) / len(events)) * 100
    lines = [
        "📊 سجل الرفع المحلي — %s (%d يوماً)" % (scope, lookback),
        "الأحداث: %d · الناجحة/المجدولة: %d · الفاشلة: %d · المحاكاة: %d · تخطي التكرار: %d" % (
            len(events), len(successful), failed, dry_runs, duplicates),
        "نسبة النجاح: %.1f%% · المنصات: %s" % (
            success_rate, ", ".join("%s=%d" % item for item in sorted(platform_counts.items()))),
        "الخصوصية: %s" % ", ".join("%s=%d" % item for item in sorted(privacy_counts.items())),
    ]
    titled = Counter(str(event.get("title") or "(untitled)")[:80] for event in successful)
    if titled:
        lines.append("أكثر العناوين الناجحة تكراراً:")
        lines.extend("  • %s (%d)" % item for item in titled.most_common(5))
    if failed:
        lines.append("التوصية: راجع الأحداث الفاشلة في publish_history.jsonl قبل إعادة المحاولة.")
    elif successful:
        lines.append("التوصية: قارن هذه العناوين الناجحة مع YouTube Analytics عند توفر OAuth للقراءة فقط.")
    return "\n".join(lines)


def run_analytics(kind, days=28, project_name=None):
    """Run local or YouTube Analytics in-process. kind: local|insights|summary|top|trends."""
    if kind == "local":
        return local_publish_summary(days=days)
    if kind == "insights":
        try:
            from scripts import performance_loop
            from webui import library
            root = library.VIRALS_DIR
            project_path = os.path.join(root, project_name) if project_name else root
            report = performance_loop.analyze(project_path)
            performance_loop.write_report(project_path, report)
            lines = [
                "🔮 Performance loop — published: {} · with metrics: {}".format(
                    report["published_count"], report["with_metrics"]),
            ]
            lines += ["  • " + insight for insight in report["insights"]]
            return "\n".join(lines)
        except Exception as e:
            return "❌ Performance loop failed: %s" % e
    try:
        from scripts import analytics
        ya, yt = analytics._build_services()
    except Exception as e:
        return ("❌ Analytics is not configured yet: %s\n\n"
                "Setup: 1) client_secrets.json (Google OAuth desktop app)  "
                "2) enable 'YouTube Data API v3' + 'YouTube Analytics API'  "
                "3) the first run opens a browser to authorize (read-only)." % e)
    try:
        if kind == "top":
            return analytics.format_top(analytics.fetch_top_videos(ya, yt, days=days))
        if kind == "trends":
            return analytics.format_trends(analytics.fetch_trends(ya, days=days))
        return analytics.format_summary(analytics.fetch_summary(ya, days=days))
    except Exception as e:
        return "❌ Analytics query failed: %s" % e
