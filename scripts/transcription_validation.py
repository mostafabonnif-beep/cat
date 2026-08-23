# -*- coding: utf-8 -*-
"""Validation helpers for transcription artifacts."""
from __future__ import annotations

import csv
import os
import re

_TIMESTAMP_RE = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{1,3})"
)


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
        if len(lines) < 3:
            continue
        timing = next((line for line in lines if "-->" in line), "")
        if not timing:
            continue
        start_raw, end_raw = [part.strip() for part in timing.split("-->", 1)]
        start = parse_timestamp(start_raw)
        end = parse_timestamp(end_raw)
        text_lines = [line for line in lines if line != timing and not line.isdigit()]
        entries.append({"start": start, "end": end, "text": " ".join(text_lines)})
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
            rows.append({"start": start, "end": end, "text": (row.get("text") or "").strip()})
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
        if not str(entry.get("text") or "").strip():
            errors.append("entry {} has empty text".format(index))
        previous_end = max(previous_end, end)
    return {"ok": not errors, "count": len(entries), "duration": max((e.get("end") or 0 for e in entries), default=0), "errors": errors}


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
        report["errors"].insert(0, "SRT unavailable; TSV fallback used" if tsv_entries else "SRT and TSV unavailable")
        # A TSV fallback is valid when it contains entries; the informational
        # fallback message is not a validation failure.
        if tsv_entries:
            report["errors"] = [e for e in report["errors"] if e != "SRT unavailable; TSV fallback used"]
    report["ok"] = not report["errors"]
    return report
