"""Local audio quality control for rendered OUSSAMA Cutter clips.

The module is deliberately dependency-free beyond the FFmpeg/ffprobe binaries
already required by the application. It measures the actual rendered files and
writes a JSON report; it never changes media, downloads assets, or makes network
calls.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Sequence

REPORT_NAME = "audio_qc_report.json"
DEFAULT_OUTPUT_DIRS = ("final_polished", "final", "cuts")
DEFAULT_TARGET_I = -16.0
DEFAULT_TARGET_TP = -1.5
DEFAULT_LRA = 11.0
DEFAULT_SILENCE_DB = -50.0
DEFAULT_SILENCE_DURATION = 0.40


def _now() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


def _float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _run(command: Sequence[str], *, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _probe_media(path: str) -> Dict[str, Any]:
    command = [
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", path,
    ]
    try:
        result = _run(command, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": "ffprobe unavailable: {}".format(exc)}
    if result.returncode != 0:
        return {"ok": False, "error": (result.stderr or "ffprobe failed").strip()[-500:]}
    try:
        payload = json.loads(result.stdout or "{}")
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": "invalid ffprobe JSON: {}".format(exc)}
    streams = payload.get("streams") if isinstance(payload, dict) else []
    streams = streams if isinstance(streams, list) else []
    audio = [item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"]
    fmt = payload.get("format") if isinstance(payload, dict) else {}
    fmt = fmt if isinstance(fmt, dict) else {}
    duration = _float(fmt.get("duration"))
    if duration is None:
        for stream in streams:
            duration = _float(stream.get("duration")) if isinstance(stream, dict) else None
            if duration is not None:
                break
    return {
        "ok": True,
        "has_audio": bool(audio),
        "audio_streams": len(audio),
        "duration": max(0.0, duration or 0.0),
        "sample_rate": audio[0].get("sample_rate") if audio else None,
        "channels": audio[0].get("channels") if audio else None,
    }


def _parse_loudnorm(stderr: str) -> Dict[str, Optional[float]]:
    """Extract the JSON object emitted by loudnorm=print_format=json."""
    candidates = re.findall(r"\{[^{}]*\}", stderr or "", flags=re.DOTALL)
    for candidate in reversed(candidates):
        try:
            data = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict) or "input_i" not in data:
            continue
        return {
            "input_i": _float(data.get("input_i")),
            "input_tp": _float(data.get("input_tp")),
            "input_lra": _float(data.get("input_lra")),
            "input_thresh": _float(data.get("input_thresh")),
            "target_offset": _float(data.get("target_offset")),
        }
    return {}


def _parse_silence(stderr: str, duration: float) -> Dict[str, Any]:
    starts: List[float] = []
    ends: List[float] = []
    for match in re.finditer(r"silence_start:\s*([-+]?\d+(?:\.\d+)?)", stderr or ""):
        value = _float(match.group(1))
        if value is not None:
            starts.append(max(0.0, value))
    for match in re.finditer(r"silence_end:\s*([-+]?\d+(?:\.\d+)?)", stderr or ""):
        value = _float(match.group(1))
        if value is not None:
            ends.append(max(0.0, value))
    silence_duration = 0.0
    for index, start in enumerate(starts):
        end = ends[index] if index < len(ends) else duration
        silence_duration += max(0.0, min(duration, end) - min(duration, start))
    ratio = (silence_duration / duration) if duration > 0 else None
    return {
        "events": len(starts),
        "duration": round(silence_duration, 3),
        "ratio": round(ratio, 4) if ratio is not None else None,
    }


def _issue(code: str, detail: str, severity: str = "review") -> Dict[str, str]:
    return {"code": code, "detail": detail, "severity": severity}


def analyze_file(
    video_path: str,
    *,
    target_i: float = DEFAULT_TARGET_I,
    target_tp: float = DEFAULT_TARGET_TP,
    silence_db: float = DEFAULT_SILENCE_DB,
    silence_duration: float = DEFAULT_SILENCE_DURATION,
) -> Dict[str, Any]:
    """Measure one rendered video and return a JSON-safe quality result."""
    path = os.path.abspath(os.path.expanduser(os.fspath(video_path)))
    report: Dict[str, Any] = {
        "video": os.path.basename(path),
        "path": path,
        "ok": False,
        "status": "block",
        "duration": 0.0,
        "metrics": {},
        "issues": [],
    }
    if not os.path.isfile(path):
        report["issues"].append(_issue("missing_file", "rendered file does not exist", "block"))
        return report

    probe = _probe_media(path)
    report["probe"] = {key: value for key, value in probe.items() if key != "ok"}
    report["duration"] = probe.get("duration", 0.0)
    if not probe.get("ok"):
        report["issues"].append(_issue("probe_failed", probe.get("error", "ffprobe failed"), "block"))
        return report
    if not probe.get("has_audio"):
        report["issues"].append(_issue("missing_audio", "rendered file has no audio stream", "block"))
        return report
    if report["duration"] <= 0:
        report["issues"].append(_issue("invalid_duration", "rendered file has no positive duration", "block"))
        return report

    loudnorm_filter = (
        "loudnorm=I={}:TP={}:LRA={}:print_format=json"
        .format(float(target_i), float(target_tp), float(DEFAULT_LRA))
    )
    try:
        loud = _run([
            "ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info",
            "-i", path, "-vn", "-af", loudnorm_filter, "-f", "null", "-",
        ], timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        loud = None
        report["issues"].append(_issue("loudness_failed", str(exc), "block"))
    loud_metrics = _parse_loudnorm(loud.stderr if loud else "")
    report["metrics"].update(loud_metrics)
    if loud is not None and loud.returncode != 0 and not loud_metrics:
        report["issues"].append(_issue(
            "loudness_failed", (loud.stderr or "ffmpeg loudnorm failed").strip()[-500:], "block"))

    try:
        silence = _run([
            "ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info",
            "-i", path, "-af", "silencedetect=noise={:.1f}dB:d={}".format(
                float(silence_db), float(silence_duration)), "-f", "null", "-",
        ], timeout=900)
        report["metrics"]["silence"] = _parse_silence(silence.stderr, report["duration"])
        if silence.returncode != 0:
            report["issues"].append(_issue(
                "silence_analysis_failed", (silence.stderr or "ffmpeg silencedetect failed").strip()[-500:], "review"))
    except (OSError, subprocess.SubprocessError) as exc:
        report["issues"].append(_issue("silence_analysis_failed", str(exc), "review"))

    input_i = _float(report["metrics"].get("input_i"))
    input_tp = _float(report["metrics"].get("input_tp"))
    if input_i is None:
        report["issues"].append(_issue(
            "loudness_unavailable", "integrated loudness could not be measured", "review"))
    elif input_i < -28.0 or input_i > -8.0:
        report["issues"].append(_issue(
            "loudness_out_of_range",
            "integrated loudness {:.2f} LUFS is outside the review range -28..-8 LUFS".format(input_i),
        ))
    if input_tp is None:
        report["issues"].append(_issue(
            "true_peak_unavailable", "true peak could not be measured", "review"))
    elif input_tp > -0.5:
        report["issues"].append(_issue(
            "true_peak_high",
            "true peak {:.2f} dBTP is close to or above clipping risk".format(input_tp),
        ))
    silence = report["metrics"].get("silence") or {}
    silence_ratio = _float(silence.get("ratio")) if isinstance(silence, dict) else None
    if silence_ratio is not None and report["duration"] >= 15.0 and silence_ratio > 0.45:
        report["issues"].append(_issue(
            "excessive_silence",
            "detected silence occupies {:.1%} of the rendered clip".format(silence_ratio),
        ))

    hard = any(item.get("severity") == "block" for item in report["issues"])
    report["status"] = "block" if hard else ("review" if report["issues"] else "pass")
    report["ok"] = report["status"] == "pass"
    return report


def _video_files(folder: str) -> List[str]:
    if not os.path.isdir(folder):
        return []
    return sorted(
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.lower().endswith((".mp4", ".mov", ".mkv", ".webm"))
        and os.path.isfile(os.path.join(folder, name))
    )


def _select_files(project_folder: str, output_dirs: Iterable[str]) -> List[str]:
    selected: List[str] = []
    seen = set()
    for folder_name in output_dirs:
        folder_files = _video_files(os.path.join(project_folder, folder_name))
        if not folder_files:
            continue
        # Match publish_panel's source precedence: use one complete output
        # bucket rather than mixing stale intermediate directories.
        for path in folder_files:
            key = os.path.normcase(os.path.abspath(path))
            if key not in seen:
                selected.append(path)
                seen.add(key)
        break
    return selected


def _report_path(project_folder: str) -> str:
    return os.path.join(os.path.abspath(project_folder), REPORT_NAME)


def _atomic_write(path: str, payload: Dict[str, Any]) -> None:
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".audio_qc_", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass


def analyze_project(
    project_folder: str,
    *,
    output_dirs: Iterable[str] = DEFAULT_OUTPUT_DIRS,
    target_i: float = DEFAULT_TARGET_I,
    target_tp: float = DEFAULT_TARGET_TP,
    silence_db: float = DEFAULT_SILENCE_DB,
    silence_duration: float = DEFAULT_SILENCE_DURATION,
    write_report: bool = True,
) -> Dict[str, Any]:
    """Analyze rendered outputs and optionally persist ``audio_qc_report.json``."""
    project = os.path.abspath(os.path.expanduser(os.fspath(project_folder)))
    output_dirs = tuple(output_dirs)
    files = _select_files(project, output_dirs)
    clips = [analyze_file(
        path, target_i=target_i, target_tp=target_tp,
        silence_db=silence_db, silence_duration=silence_duration,
    ) for path in files]
    counts = {"pass": 0, "review": 0, "block": 0}
    for clip in clips:
        status = clip.get("status", "block")
        counts[status] = counts.get(status, 0) + 1
    if not clips:
        status = "review"
    elif counts["block"]:
        status = "block"
    elif counts["review"]:
        status = "review"
    else:
        status = "pass"
    report = {
        "schema": 1,
        "generated_at": _now(),
        "project": project,
        "status": status,
        "ok": status == "pass",
        "summary": {
            "total": len(clips),
            "pass": counts.get("pass", 0),
            "review": counts.get("review", 0),
            "block": counts.get("block", 0),
            "output_dirs": list(output_dirs),
        },
        "thresholds": {
            "target_i": float(target_i),
            "target_tp": float(target_tp),
            "silence_db": float(silence_db),
            "silence_duration": float(silence_duration),
        },
        "clips": clips,
    }
    if write_report:
        _atomic_write(_report_path(project), report)
    return report


def load_report(project_folder: str) -> Optional[Dict[str, Any]]:
    try:
        with open(_report_path(project_folder), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def report_for_file(project_folder: str, video_path: str) -> Optional[Dict[str, Any]]:
    report = load_report(project_folder)
    if not report:
        return None
    wanted = os.path.normcase(os.path.abspath(os.fspath(video_path)))
    for clip in report.get("clips", []) or []:
        if isinstance(clip, dict) and os.path.normcase(os.path.abspath(str(clip.get("path", "")))) == wanted:
            return clip
    return None


def ensure_file_report(project_folder: str, video_path: str) -> Dict[str, Any]:
    """Return a current report for one file, refreshing the project report if needed."""
    existing = report_for_file(project_folder, video_path)
    if existing is not None:
        try:
            if os.path.getmtime(video_path) <= os.path.getmtime(_report_path(project_folder)):
                return existing
        except OSError:
            pass
    analyze_project(project_folder)
    return report_for_file(project_folder, video_path) or analyze_file(video_path)


def gate_allows(report: Optional[Dict[str, Any]], *, strict: bool = False) -> tuple[bool, str]:
    """Return whether a report is acceptable for publishing."""
    if not report:
        return False, "audio_qc_report.json is missing or invalid"
    status = str(report.get("status", "block"))
    if status == "pass":
        return True, ""
    if strict:
        return False, "Audio QC status is {}".format(status)
    return False, "Audio QC requires review before real publishing ({})".format(status)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run local FFmpeg Audio QC for an OUSSAMA project.")
    parser.add_argument("--project", required=True, help="Project folder containing final/final_polished/cuts")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report")
    args = parser.parse_args(argv)
    report = analyze_project(args.project)
    summary = report.get("summary", {})
    print("[audio-qc] status={} pass={} review={} block={} / {}".format(
        report.get("status"), summary.get("pass", 0), summary.get("review", 0),
        summary.get("block", 0), summary.get("total", 0)))
    print("[audio-qc] report: {}".format(_report_path(args.project)))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "pass" else 2


__all__ = [
    "REPORT_NAME", "analyze_file", "analyze_project", "ensure_file_report",
    "gate_allows", "load_report", "main", "report_for_file",
]


if __name__ == "__main__":
    raise SystemExit(main())
