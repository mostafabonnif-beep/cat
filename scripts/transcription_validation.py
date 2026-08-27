"""Validation and safe repair helpers for transcription artifacts."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile

_TIMESTAMP_RE = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{1,3})"
)
_INVISIBLE_TEXT_CHARS = str.maketrans("", "", "\ufeff\u200b\u200c\u200d")


def normalize_text(value):
    """Normalize text for validation without changing visible speech content."""
    return str(value or "").translate(_INVISIBLE_TEXT_CHARS).strip()


def parse_timestamp(value):
    match = _TIMESTAMP_RE.search(str(value or ""))
    if not match:
        return None
    return (
        int(match.group("h")) * 3600
        + int(match.group("m")) * 60
        + int(match.group("s"))
        + int(match.group("ms").ljust(3, "0")) / 1000.0
    )


def read_srt_entries(path):
    if not path or not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        text = fh.read().replace("\r\n", "\n")
    entries = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        # A cue containing only an index and timing is intentionally retained;
        # the validator/repair step must be able to identify its empty text.
        if len(lines) < 2:
            continue
        timing = next((line for line in lines if "-->" in line), "")
        if not timing:
            continue
        start_raw, end_raw = [part.strip() for part in timing.split("-->", 1)]
        start = parse_timestamp(start_raw)
        end = parse_timestamp(end_raw)
        text_lines = [line for line in lines if line != timing and not line.isdigit()]
        entries.append({"start": start, "end": end, "text": normalize_text(" ".join(text_lines))})
    return entries


def read_tsv_entries(path):
    if not path or not os.path.isfile(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            try:
                start = float(row.get("start", ""))
                end = float(row.get("end", ""))
            except (TypeError, ValueError):
                continue
            rows.append({"start": start, "end": end, "text": normalize_text(row.get("text"))})
    return rows


def validate_entries(entries, *, min_entries=1):
    errors = []
    if len(entries) < min_entries:
        errors.append("no transcription entries found")
    previous_end = -1.0
    for index, entry in enumerate(entries, 1):
        start, end = entry.get("start"), entry.get("end")
        if start is None or end is None:
            errors.append("entry {} has invalid timestamps".format(index))
            continue
        if start < 0 or end <= start:
            errors.append("entry {} has non-positive duration".format(index))
        if start + 0.001 < previous_end:
            errors.append("entry {} is out of order".format(index))
        if not normalize_text(entry.get("text")):
            errors.append("entry {} has empty text".format(index))
        previous_end = max(previous_end, end)
    return {
        "ok": not errors,
        "count": len(entries),
        "duration": max((e.get("end") or 0 for e in entries), default=0),
        "errors": errors,
    }


def _atomic_write(path, text):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".transcription.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass


def _format_timestamp(seconds):
    milliseconds = max(0, int(round(float(seconds) * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds_value, millis = divmod(remainder, 1000)
    return "{:02d}:{:02d}:{:02d},{:03d}".format(
        hours, minutes, seconds_value, millis
    )


def _repair_srt(path):
    entries = read_srt_entries(path)
    kept = [entry for entry in entries if normalize_text(entry.get("text"))]
    removed = len(entries) - len(kept)
    if removed:
        # Do not rewrite a mixed file whose surviving cues have malformed
        # timestamps. Keep that defect visible to validate_transcription so
        # the caller can clear the checkpoint and re-transcribe safely.
        if any(entry.get("start") is None or entry.get("end") is None for entry in kept):
            return 0
        blocks = []
        for index, entry in enumerate(kept, 1):
            blocks.append(
                "{}\n{} --> {}\n{}".format(
                    index,
                    _format_timestamp(entry["start"]),
                    _format_timestamp(entry["end"]),
                    normalize_text(entry["text"]),
                )
            )
        _atomic_write(path, "\n\n".join(blocks) + ("\n" if blocks else ""))
    return removed


def _repair_tsv(path):
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    if not rows:
        return 0
    header = rows[0]
    try:
        text_index = [item.strip().lower() for item in header].index("text")
    except ValueError:
        return 0
    kept = [row for row in rows[1:] if len(row) > text_index and normalize_text(row[text_index])]
    removed = len(rows[1:]) - len(kept)
    if removed:
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(kept)
        _atomic_write(path, output.getvalue())
    return removed


def _repair_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        return 0
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        return 0
    segments = data["segments"]
    kept = [
        segment for segment in segments
        if not isinstance(segment, dict) or normalize_text(segment.get("text"))
    ]
    removed = len(segments) - len(kept)
    if removed:
        data["segments"] = kept
        _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return removed


def repair_transcription_artifacts(srt_path=None, tsv_path=None, json_path=None):
    """Remove only empty-text rows from existing artifacts, atomically.

    Invalid timestamps are deliberately not discarded: they remain visible to
    ``validate_transcription`` and trigger a safe re-transcription when a
    completed checkpoint points at corrupt output. This repair is therefore
    conservative and cannot turn a timestamp failure into a false success.
    """
    removed = {"srt": 0, "tsv": 0, "json": 0}
    if srt_path and os.path.isfile(srt_path):
        removed["srt"] = _repair_srt(srt_path)
    if tsv_path and os.path.isfile(tsv_path):
        removed["tsv"] = _repair_tsv(tsv_path)
    if json_path and os.path.isfile(json_path):
        removed["json"] = _repair_json(json_path)
    return {
        "changed": any(removed.values()),
        "removed": removed,
        "total_removed": sum(removed.values()),
    }


def validate_transcription(srt_path=None, tsv_path=None):
    """Prefer SRT, fall back to TSV, and return a compact diagnostic report."""
    srt_entries = read_srt_entries(srt_path)
    if srt_entries:
        report = validate_entries(srt_entries)
        report["format"] = "srt"
        report["path"] = str(srt_path)
        return report
    tsv_entries = read_tsv_entries(tsv_path)
    report = validate_entries(tsv_entries)
    report["format"] = "tsv"
    report["path"] = str(tsv_path)
    if not srt_entries:
        report["errors"].insert(
            0,
            "SRT unavailable; TSV fallback used" if tsv_entries else "SRT and TSV unavailable",
        )
        # A TSV fallback is valid when it contains entries; the informational
        # fallback message is not a validation failure.
        if tsv_entries:
            report["errors"] = [
                error for error in report["errors"]
                if error != "SRT unavailable; TSV fallback used"
            ]
    report["ok"] = not report["errors"]
    return report
