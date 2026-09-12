"""Review & select AI-suggested viral segments before rendering.

Pure logic (no gradio imports) so it stays unit-testable:
- load segments from a project's viral_segments.txt
- convert to table rows for the UI
- apply a selection: keep only chosen segments, back up the original file,
  and invalidate stale cuts so the next render reflects the new selection.
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from i18n.i18n import DEFAULT_LANGUAGE, I18nAuto

i18n = I18nAuto(DEFAULT_LANGUAGE)

SEGMENTS_FILENAME = "viral_segments.txt"
BACKUP_FILENAME = "viral_segments.full_backup.json"
SAFETY_REPORT_FILENAME = "safety_report.json"

# Legacy columns remain first; the last two columns explain the new ranking.
HEADERS = [
    "✓", i18n("Title"), i18n("Rating"), i18n("Start"), i18n("End"),
    i18n("Duration (s)"), i18n("Why Viral?"), i18n("Publish Caption"),
    i18n("Safety"), i18n("Safety reason"), i18n("Safe alternative"),
    i18n("Selection score"), i18n("A/B title alternatives"),
]


def _atomic_write_json(path, payload):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=4)
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


def segments_file_path(project_path):
    return os.path.join(project_path, SEGMENTS_FILENAME)


def backup_file_path(project_path):
    return os.path.join(project_path, BACKUP_FILENAME)


def load_segments(project_path):
    """Return the segments list from a project, or [] if none/invalid."""
    return (_read_payload(project_path) or {}).get("segments", []) or []


def _read_payload(project_path):
    """Return the full viral_segments.txt payload dict, or None if invalid.

    The file carries top-level metadata (source_meta with source/config
    fingerprints) next to ``segments``. Writers must preserve those keys —
    rewriting only ``{"segments": [...]}`` used to silently drop them.
    """
    path = segments_file_path(project_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_payload(project_path, payload):
    _atomic_write_json(segments_file_path(project_path), payload)


def _fmt_time(seconds):
    try:
        seconds = float(seconds)
    except Exception:
        return str(seconds)
    m, s = divmod(int(round(seconds)), 60)
    return f"{m:02d}:{s:02d}"


def load_safety_map(project_path):
    """Read safety_report.json into rich per-segment details for the UI."""
    path = os.path.join(project_path, SAFETY_REPORT_FILENAME)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        result = {}
        entries = data.get("segments", []) if isinstance(data, dict) else []
        if not isinstance(entries, list):
            entries = []
        entries += data.get("ai_review", []) if isinstance(data, dict) and isinstance(data.get("ai_review"), list) else []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            key = (entry.get("title", ""), entry.get("start_time"))
            semantic = entry.get("semantic") or {}
            matches = entry.get("matches") or []
            terms = [str(match.get("term", "")).strip() for match in matches if isinstance(match, dict) and match.get("term")]
            reason = semantic.get("explanation") or entry.get("reason") or ", ".join(dict.fromkeys(terms))
            status = entry.get("status", "safe")
            if status in {"blocked", "semantic_blocked", "ai_blocked"}:
                alternative = i18n("احذف هذا الجزء أو أعد صياغته بعيداً عن استهداف فئة أو الدعوة إلى العنف، ثم راجعه يدوياً قبل النشر.")
            elif status in {"manual_review", "flagged", "censor", "ai_flagged"}:
                alternative = i18n("احتفظ به للمراجعة فقط، أو استبدل العبارات الحساسة بصياغة تعليمية محايدة مع مصدر واضح.")
            else:
                alternative = ""
            result[key] = {
                "status": status,
                "reason": reason or i18n("لم يظهر سبب تفصيلي في التقرير."),
                "alternative": alternative,
                "terms": terms,
            }
        return result
    except Exception:
        return {}


def _safety_info(seg, safety_map):
    info = safety_map.get((seg.get("title", ""), seg.get("start_time")), {}) if safety_map else {}
    if isinstance(info, str):
        info = {"status": info}
    annotation = seg.get("safety") or {}
    if annotation.get("ai_flagged"):
        info = dict(info, status="ai_flagged")
    elif annotation.get("flagged"):
        info = dict(info, status=annotation.get("action", "flagged"))
    if annotation.get("terms") and not info.get("reason"):
        info["reason"] = ", ".join(str(term) for term in annotation["terms"])
    return info


def _safety_badge(seg, safety_map):
    status = _safety_info(seg, safety_map).get("status", "safe")
    return {
        "safe": "✅",
        "flagged": "⚠️",
        "manual_review": "⚠️ " + i18n("Review"),
        "blocked": "⛔",
        "semantic_blocked": "⛔",
        "censor": "🔇 " + i18n("Muted"),
        "ai_flagged": "🤖⚠️",
        "ai_blocked": "🤖⛔",
    }.get(status, "✅")


def rows_from_segments(segments, safety_map=None):
    """Build rows with safety explanation and a practical safer alternative."""
    rows = []
    for seg in segments:
        start = seg.get("start_time", seg.get("start", 0))
        end = seg.get("end_time", seg.get("end", 0))
        try:
            duration = round(float(end) - float(start), 1)
        except Exception:
            duration = seg.get("duration", "")
        safety = _safety_info(seg, safety_map or {})
        title = seg.get("recommended_title") or seg.get("title", seg.get("hook", ""))
        alternatives = seg.get("alt_titles") or []
        if isinstance(alternatives, str):
            alternatives = [alternatives]
        alternatives = [str(item).strip() for item in alternatives if str(item).strip() and str(item).strip() != title]
        reasoning = seg.get("reasoning", "")
        if seg.get("quality_missing"):
            # The AI shipped no editorial self-evaluation; hook/clarity/…
            # values are copies of the virality score. Say so where the
            # reviewer looks, instead of letting them read as measurements.
            marker = i18n("⚠️ التقييم التحريري من الذكاء الاصطناعي مفقود — القيم منسوخة من درجة الانتشار.")
            reasoning = (reasoning + "\n" if reasoning else "") + marker
        rows.append([
            True,
            title,
            seg.get("score", 0),
            _fmt_time(start),
            _fmt_time(end),
            duration,
            reasoning,
            seg.get("caption", ""),
            _safety_badge(seg, safety_map or {}),
            safety.get("reason", ""),
            safety.get("alternative", ""),
            seg.get("selection_score", seg.get("score", 0)),
            " | ".join(alternatives),
        ])
    return rows


def _renumber_candidate_ranks(segments):
    """Renumber ``candidate_rank`` 1..N after a manual selection.

    The export order (finalize_top_segments in create_viral_segments) sorts by
    ``candidate_rank`` first. Deselecting rows used to leave sparse ranks, so
    the persisted file no longer matched the review order. This restores the
    invariant: kept order == ranked order == exported order.

    Ranking entries first (by their existing rank), then legacy entries
    without a rank ordered by selection_score/score — the same ordering
    contract the exporter uses. Stable.
    """
    ranked, legacy = [], []
    for index, item in enumerate(segments):
        if isinstance(item, dict) and item.get("candidate_rank") is not None:
            ranked.append((index, item))
        else:
            legacy.append((index, item))

    def _key_score(item, field):
        try:
            return float(item.get(field, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _rank_value(pair):
        try:
            return float(pair[1].get("candidate_rank"))
        except (TypeError, ValueError):
            return float("inf")

    ranked.sort(key=lambda pair: (_rank_value(pair), pair[0]))
    legacy.sort(key=lambda pair: (_key_score(pair[1], "selection_score"),
                                  _key_score(pair[1], "score")), reverse=True)
    ordered = [item for _, item in ranked + legacy]
    for rank, item in enumerate(ordered, 1):
        item["candidate_rank"] = rank
    return ordered


def _rows_to_bool_list(rows):
    """Normalize a Gradio Dataframe value (pandas or list) to a bool list."""
    if rows is None:
        return []
    # pandas DataFrame (gradio default) — avoid importing pandas
    if hasattr(rows, "iloc"):
        return [bool(x) for x in rows.iloc[:, 0].tolist()]
    return [bool(r[0]) for r in rows]


def apply_selection(project_path, rows):
    """Keep only selected segments. Returns (kept, total, cuts_invalidated)."""
    segments = load_segments(project_path)
    if not segments:
        return 0, 0, False

    selected = _rows_to_bool_list(rows)
    # If the table has fewer rows than segments, default the rest to selected
    if len(selected) < len(segments):
        selected += [True] * (len(segments) - len(selected))

    kept_segments = [s for s, keep in zip(segments, selected) if keep]
    if not kept_segments:
        kept_segments = segments  # never write an empty selection
        selected = [True] * len(segments)

    # Restore the rank invariant (kept order == export order) and renumber
    # ranks 1..N so the persisted file matches the review table exactly.
    kept_segments = _renumber_candidate_ranks(kept_segments)

    changed = len(kept_segments) != len(segments)

    seg_path = segments_file_path(project_path)
    bak_path = backup_file_path(project_path)
    if changed and not os.path.exists(bak_path):
        shutil.copy2(seg_path, bak_path)

    # Preserve the top-level metadata (source_meta fingerprints, settings)
    # that rides next to ``segments`` in the file.
    payload = _read_payload(project_path) or {"segments": kept_segments}
    payload["segments"] = kept_segments
    _write_payload(project_path, payload)

    # Invalidate stale cuts so the next render respects the new selection
    cuts_invalidated = False
    if changed:
        cuts_dir = os.path.join(project_path, "cuts")
        if os.path.isdir(cuts_dir):
            shutil.rmtree(cuts_dir, ignore_errors=True)
            cuts_invalidated = True

    return len(kept_segments), len(segments), cuts_invalidated


def restore_all(project_path):
    """Restore the original full segments file from backup."""
    bak_path = backup_file_path(project_path)
    seg_path = segments_file_path(project_path)
    if not os.path.exists(bak_path):
        return False
    # Copy through an atomic replace so a failed disk write cannot leave a
    # half-written viral_segments.txt behind.
    with open(bak_path, "rb") as source:
        data = json.load(source)
    _atomic_write_json(seg_path, data)
    cuts_dir = os.path.join(project_path, "cuts")
    if os.path.isdir(cuts_dir):
        shutil.rmtree(cuts_dir, ignore_errors=True)
    return True


def title_choices_for(project_path, index):
    """Return (options, current) selectable titles for one segment row.

    ``index`` is the row position (0-based) in the review table / file order.
    Options = the shipped ``recommended_title``/``title`` plus its A/B
    ``alt_titles`` — exactly what the LLM offered for that segment.
    Returns ([], "") when the segment does not exist.
    """
    segments = load_segments(project_path)
    if index is None or index < 0 or index >= len(segments):
        return [], ""
    seg = segments[index]
    current = seg.get("recommended_title") or seg.get("title") or ""
    alternatives = seg.get("alt_titles") or []
    if isinstance(alternatives, str):
        alternatives = [alternatives]
    options = []
    for candidate in [current] + list(alternatives):
        text = str(candidate or "").strip()
        if text and text not in options:
            options.append(text)
    return options, current


def choose_title(project_path, index, title):
    """Persist an A/B (or main) title choice as the segment's publish title.

    The review table displayed ``alt_titles`` but discarding edited cells
    meant the choice never survived: this is the persisted pick. Returns
    (ok, message). Only titles the LLM actually offered for that segment are
    accepted — the choice can never drift from the segment's own content.
    """
    segments = load_segments(project_path)
    if index is None or index < 0 or index >= len(segments):
        return False, i18n("Segment not found.")
    title = str(title or "").strip()
    if not title:
        return False, i18n("Title is empty.")
    options, current = title_choices_for(project_path, index)
    if title not in options:
        return False, i18n("That title is not among the A/B alternatives of this segment.")
    # v7.41: the reviewer's pick is factually validated against the EXACT clip
    # transcript before it can become the shipped title — an A/B option that
    # the validator rejects (invented number/name/claim) cannot be promoted.
    validation = None
    seg = segments[index]
    window_text = str(seg.get("transcript_text")
                      or (seg.get("window_analysis") or {}).get("text") or "")
    if window_text:
        try:
            from scripts import title_factual
            validation = title_factual.validate_title_vs_clip(
                title, window_text,
                str((seg.get("title_data") or {}).get("title_language") or "auto"))
            if validation.get("status") == "rejected":
                return False, i18n("That title is not supported by this clip: {}").format(
                    "; ".join(validation.get("reasons") or ["factual validation failed"]))
        except Exception:
            validation = None
    if title == current:
        return True, i18n("Title unchanged: {}").format(title)
    payload = _read_payload(project_path)
    if payload is None:
        return False, i18n("No viral segments file for this project.")
    payload["segments"][index]["recommended_title"] = title
    # Keep the *chosen* title visible everywhere the reviewer looks next.
    payload["segments"][index]["title_chosen"] = title
    if validation is not None:
        payload["segments"][index]["title_validation"] = validation
        payload["segments"][index]["title_review_required"] = (
            validation.get("status") != "verified")
    _write_payload(project_path, payload)
    return True, i18n("Title saved: {}").format(title)


def export_publish_metadata(project_path):
    """Write publish_metadata.txt: per segment title + caption + hashtags.

    Returns (path, text) or (None, "") when there are no segments.
    """
    segments = load_segments(project_path)
    if not segments:
        return None, ""

    lines = []
    for i, seg in enumerate(segments, 1):
        title = seg.get("recommended_title") or seg.get("title", "") or f"Segment {i}"
        caption = seg.get("caption", "")
        hashtags = seg.get("hashtags", []) or []
        if isinstance(hashtags, str):
            hashtags = [hashtags]
        tags = " ".join("#" + str(h).lstrip("#") for h in hashtags if str(h).strip())

        lines.append(f"━━━ {i}. {title} ━━━")
        if caption:
            lines.append(caption)
        if tags:
            lines.append(tags)
        alternatives = seg.get("alt_titles") or []
        if isinstance(alternatives, str):
            alternatives = [alternatives]
        alternatives = [str(item).strip() for item in alternatives if str(item).strip() and str(item).strip() != title]
        if alternatives:
            lines.append("A/B titles: " + " | ".join(alternatives))
        lines.append("")

    text = "\n".join(lines).strip() + "\n"
    path = os.path.join(project_path, "publish_metadata.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path, text
