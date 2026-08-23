# -*- coding: utf-8 -*-
"""ViralCutter Strike Feedback Loop — teach the tool from real outcomes.

Roadmap item 5.1. The tool can't learn from your channel's actual history on
its own, so THIS is the "علّم الأداة" (teach the tool) interface:

    * a clip took a strike / copyright claim / was rejected by the platform?
      ->  python -m scripts.strike_feedback add --term "الكلمة" --reason "..."
    * a term in the built-in list is a FALSE POSITIVE on your channel?
      ->  python -m scripts.strike_feedback allow --term "كلمة مسموحة"
    * a project got blocked by the risk scorecard / safety filter?
      ->  python -m scripts.strike_feedback from-scorecard --project VIRALS/x

What it writes:
    safety_terms.json         user custom terms — consumed AUTOMATICALLY by
                              scripts/safety_filter.load_custom_terms() on the
                              next run (no other wiring needed).
    strike_feedback.json      journal of every learned event (the memory of
                              the tool: what was learned, when, why).

Every write is atomic (temp file + rename) and never corrupts the existing
term list. Pure stdlib — works even on a machine missing all heavy deps.

Commands:
  add   --term T [--lang ar] [--severity high|medium|low] [--category C]
              [--reason R] [--project P] [--source manual|scorecard]
  allow --term T [--reason R]                 # false-positive control
  remove --term T [--allow]                   # drop from extra/allow terms
  list                                         # current custom terms
  stats                                        # journal summary
  from-scorecard --project P [--apply] [--reason R]
  export [--format json|txt]
"""
import argparse
import json
import os
import re
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TERMS_FILE = "safety_terms.json"
JOURNAL_FILE = "strike_feedback.json"
SEVERITIES = ("high", "medium", "low")
# Safety-report match keys we care about
_REPORT_SEGMENT_MATCHES = "matches"


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------
def terms_path(base_dir=None):
    return os.path.join(base_dir or APP_ROOT, TERMS_FILE)


def journal_path(base_dir=None):
    return os.path.join(base_dir or APP_ROOT, JOURNAL_FILE)


def load_terms(base_dir=None):
    """Load safety_terms.json as {'extra_terms': [...], 'allow_terms': [...]}."""
    path = terms_path(base_dir)
    if not os.path.exists(path):
        return {"extra_terms": [], "allow_terms": []}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {}
    return {
        "extra_terms": [t for t in data.get("extra_terms", [])
                        if isinstance(t, dict) and t.get("term")],
        "allow_terms": [str(t) for t in data.get("allow_terms", []) if t],
    }


def _atomic_write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def save_terms(extra_terms, allow_terms, base_dir=None):
    """Write the term file (atomic). Returns the path written."""
    path = terms_path(base_dir)
    _atomic_write_json(path, {"extra_terms": extra_terms, "allow_terms": allow_terms})
    return path


def load_journal(base_dir=None):
    path = journal_path(base_dir)
    if not os.path.exists(path):
        return {"version": 1, "events": []}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            return data
    except Exception:
        pass
    return {"version": 1, "events": []}


def save_journal(journal, base_dir=None):
    path = journal_path(base_dir)
    _atomic_write_json(path, journal)
    return path


def _now_iso():
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def log_event(journal, action, term, **extra):
    ev = {"id": re.sub(r"[^0-9]", "", _now_iso())[:14], "ts": _now_iso(),
          "action": action, "term": term}
    ev.update({k: v for k, v in extra.items() if v is not None})
    journal["events"].append(ev)
    return journal

# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_add(term, lang="ar", severity="high", category="custom",
            reason=None, source="manual", project=None, base_dir=None):
    """Add a term to extra_terms (BLOCK) + journal the event."""
    if severity not in SEVERITIES:
        raise ValueError("severity must be one of %s" % ", ".join(SEVERITIES))
    terms = load_terms(base_dir)
    entry = {"term": term, "lang": lang, "severity": severity, "category": category}
    norm = set(t["term"].strip().lower() for t in terms["extra_terms"])
    if term.strip().lower() not in norm:
        terms["extra_terms"].append(entry)
        save_terms(terms["extra_terms"], terms["allow_terms"], base_dir)
    journal = log_event(load_journal(base_dir), "add", term,
                        lang=lang, severity=severity, category=category,
                        reason=reason, source=source, project=project)
    save_journal(journal, base_dir)
    return terms_path(base_dir), entry


def cmd_allow(term, reason=None, base_dir=None):
    """Add a term to allow_terms (EXCLUDE from built-in blocklist)."""
    terms = load_terms(base_dir)
    norm = {t.strip().lower() for t in terms["allow_terms"]}
    if term.strip().lower() not in norm:
        terms["allow_terms"].append(term)
        save_terms(terms["extra_terms"], terms["allow_terms"], base_dir)
    journal = log_event(load_journal(base_dir), "allow", term,
                        reason=reason, source="manual")
    save_journal(journal, base_dir)
    return terms_path(base_dir), term


def cmd_remove(term, allow=False, base_dir=None):
    """Remove a term from extra_terms (default) or allow_terms (--allow)."""
    terms = load_terms(base_dir)
    removed = False
    if allow:
        before = len(terms["allow_terms"])
        terms["allow_terms"] = [t for t in terms["allow_terms"]
                                if t.strip().lower() != term.strip().lower()]
        removed = len(terms["allow_terms"]) < before
    else:
        before = len(terms["extra_terms"])
        terms["extra_terms"] = [t for t in terms["extra_terms"]
                                if t["term"].strip().lower() != term.strip().lower()]
        removed = len(terms["extra_terms"]) < before
    if removed:
        save_terms(terms["extra_terms"], terms["allow_terms"], base_dir)
        journal = log_event(load_journal(base_dir), "remove", term, source="manual")
        save_journal(journal, base_dir)
    return removed


def cmd_list(base_dir=None):
    return load_terms(base_dir)


def cmd_stats(base_dir=None):
    journal = load_journal(base_dir)
    events = journal["events"]
    stats = {
        "events": len(events),
        "by_action": {},
        "by_severity": {},
        "by_month": {},
        "last_event": events[-1]["ts"] if events else None,
    }
    for ev in events:
        stats["by_action"][ev["action"]] = stats["by_action"].get(ev["action"], 0) + 1
        sev = ev.get("severity")
        if sev:
            stats["by_severity"][sev] = stats["by_severity"].get(sev, 0) + 1
        month = ev.get("ts", "")[:7]
        if month:
            stats["by_month"][month] = stats["by_month"].get(month, 0) + 1
    terms = load_terms(base_dir)
    stats["active_extra_terms"] = len(terms["extra_terms"])
    stats["active_allow_terms"] = len(terms["allow_terms"])
    return stats


# --------------------------------------------------------------------------
# from-scorecard — extract patterns from a blocked project
# --------------------------------------------------------------------------
def _read_json_anywhere(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None
    return None


def extract_terms_from_project(project_folder):
    """Collect matched terms from a project's safety/risk reports.

    Returns a list of dicts: {term, severity, count, reasons:[...]} sorted by
    count desc. Reads safety_report.json (exact matched terms per segment) and
    risk_scorecard.json (blocked/danger segments that should not be published).
    """
    safety = _read_json_anywhere(os.path.join(project_folder, "safety_report.json"))
    scorecard = _read_json_anywhere(os.path.join(project_folder, "risk_scorecard.json"))

    blocked_indexes = set()
    if scorecard:
        for entry in scorecard.get("blocked", []):
            idx = entry.get("index")
            if idx is not None:
                blocked_indexes.add(idx)

    counts = {}
    reasons = {}
    severities = {}

    def _note(term, severity, reason):
        key = term.strip().lower()
        counts[key] = counts.get(key, 0) + 1
        severities.setdefault(key, severity or "high")
        if reason and reason not in reasons.setdefault(key, []):
            reasons[key].append(reason)

    if safety:
        for seg in safety.get("segments", []):
            for m in seg.get(_REPORT_SEGMENT_MATCHES, []) or []:
                if not isinstance(m, dict) or not m.get("term"):
                    continue
                _note(m["term"], m.get("severity"), "matched in segment '%s'" % (seg.get("title") or "?"))

    if scorecard:
        for entry in scorecard.get("segments", []):
            idx = entry.get("index")
            if idx is not None and (idx in blocked_indexes or entry.get("overall") == "danger"):
                # NOTE: the raw title is NOT learned as a term — a clip can be
                # blocked for reuse/visual reasons that have nothing to do with
                # its words. Only real matched terms are worth learning.
                first7 = ((entry.get("axes") or {}).get("text") or {}).get("first7s")
                if isinstance(first7, dict):
                    for t in first7.get("terms", []) or []:
                        _note(t, "high", "first-7s risk term (limited ads)")

    out = []
    for key, cnt in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append({"term": key, "severity": severities[key],
                    "count": cnt, "reasons": reasons.get(key, [])})
    return out

# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _build_parser():
    p = argparse.ArgumentParser(
        description="ViralCutter strike feedback loop — teach the tool from real outcomes (5.1)")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("add", help="add a term to the custom BLOCK list")
    a.add_argument("--term", required=True)
    a.add_argument("--lang", default="ar", help="language code (default: ar)")
    a.add_argument("--severity", choices=SEVERITIES, default="high")
    a.add_argument("--category", default="custom")
    a.add_argument("--reason", default=None, help="why? e.g. 'strike on video X'")
    a.add_argument("--project", default=None, help="project folder this came from")
    a.add_argument("--source", default="manual", choices=["manual", "scorecard"])

    al = sub.add_parser("allow", help="add a term to the ALLOW list (false-positive control)")
    al.add_argument("--term", required=True)
    al.add_argument("--reason", default=None)

    r = sub.add_parser("remove", help="remove a term")
    r.add_argument("--term", required=True)
    r.add_argument("--allow", action="store_true", help="remove from allow_terms instead")

    sub.add_parser("list", help="show current custom terms")
    sub.add_parser("stats", help="journal summary")

    fs = sub.add_parser("from-scorecard",
                        help="extract patterns from a blocked project's reports")
    fs.add_argument("--project", required=True)
    fs.add_argument("--apply", action="store_true",
                    help="add the extracted terms to safety_terms.json")
    fs.add_argument("--reason", default=None)

    e = sub.add_parser("export", help="export the journal")
    e.add_argument("--format", choices=["json", "txt"], default="txt")

    p.add_argument("--dir", default=None, help="base dir for the term/journal files "
                                                "(default: repo root)")
    return p


def _print_terms(terms):
    print("custom block terms (extra_terms): %d" % len(terms["extra_terms"]))
    for t in terms["extra_terms"]:
        print("  - %-24s lang=%-4s sev=%-6s cat=%s" % (
            t["term"], t.get("lang", "?"), t.get("severity", "?"), t.get("category", "?")))
    print("allow terms (false-positive fixes): %d" % len(terms["allow_terms"]))
    for t in terms["allow_terms"]:
        print("  - %s" % t)


def main(argv=None):
    args = _build_parser().parse_args(argv)
    base_dir = args.dir or APP_ROOT

    if args.command == "add":
        path, entry = cmd_add(args.term, lang=args.lang, severity=args.severity,
                              category=args.category, reason=args.reason,
                              source=args.source, project=args.project, base_dir=base_dir)
        print("✅ added '%s' (severity=%s) → %s" % (entry["term"], entry["severity"], path))
        print("   it is now blocked by the safety filter on every next run.")
        return 0

    if args.command == "allow":
        path, term = cmd_allow(args.term, reason=args.reason, base_dir=base_dir)
        print("✅ allowed '%s' (excluded from the built-in blocklist) → %s" % (term, path))
        return 0

    if args.command == "remove":
        removed = cmd_remove(args.term, allow=args.allow, base_dir=base_dir)
        print("✅ removed '%s'" % args.term if removed else "not found — nothing to remove")
        return 0 if removed else 1

    if args.command == "list":
        _print_terms(load_terms(base_dir))
        return 0

    if args.command == "stats":
        stats = cmd_stats(base_dir)
        print("Strike-feedback journal:")
        print("  events:           %d" % stats["events"])
        print("  by action:        %s" % stats["by_action"])
        print("  by severity:      %s" % stats["by_severity"])
        print("  by month:         %s" % stats["by_month"])
        print("  last event:       %s" % stats["last_event"])
        print("  active block:     %d terms" % stats["active_extra_terms"])
        print("  active allow:     %d terms" % stats["active_allow_terms"])
        return 0

    if args.command == "from-scorecard":
        found = extract_terms_from_project(args.project)
        if not found:
            print("No patterns found — project has no safety/risk reports or no blocked clips.")
            return 0
        print("Patterns extracted from '%s':" % args.project)
        for f in found:
            print("  - %-24s sev=%-6s x%d" % (f["term"], f["severity"], f["count"]))
            for r_ in f["reasons"][:2]:
                print("      · %s" % r_)
        if args.apply:
            added = 0
            for f in found:
                try:
                    cmd_add(f["term"], lang="auto", severity=f["severity"],
                            category="learned", reason=args.reason or "; ".join(f["reasons"][:1]),
                            source="scorecard", project=args.project, base_dir=base_dir)
                    added += 1
                except Exception:
                    pass
            print("✅ learned %d term(s) into safety_terms.json — next runs will block them." % added)
        else:
            print("(re-run with --apply to teach them to the tool)")
        return 0

    if args.command == "export":
        journal = load_journal(base_dir)
        if args.format == "json":
            print(json.dumps(journal, ensure_ascii=False, indent=2))
        else:
            for ev in journal["events"]:
                print("[%s] %-6s %s" % (ev.get("ts", "?"), ev.get("action"), ev.get("term")))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
