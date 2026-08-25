"""Windows-focused, read-only diagnostics for OUSSAMA Cutter.

This module deliberately performs no installation, login, upload, model download,
or video processing. It is safe to run from the project root with the same Python
interpreter that launches the WebUI:

    .\\.venv\\Scripts\\python.exe -m scripts.windows_diagnostics --root D:\\SS
    .\\.venv\\Scripts\\python.exe -m scripts.windows_diagnostics --json \
        --output .windows-diagnostics.json

Exit codes are 0 when critical checks pass, 1 when a critical check fails, and 2
when only optional checks are missing or warnings are present.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata as metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

OK = "ok"
WARN = "warn"
FAIL = "fail"


def _check(name: str, status: str, detail: str, *, critical: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "critical": bool(critical),
    }


def _version(dist: str) -> str:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return "?"
    except Exception as exc:  # pragma: no cover - defensive on damaged metadata
        return "? (%s)" % exc


def _import_probe(module_name: str, distribution: str | None = None) -> tuple[bool, str]:
    try:
        module = importlib.import_module(module_name)
        version = getattr(module, "__version__", None)
        if version is None and distribution:
            version = _version(distribution)
        return True, str(version or "imported")
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, str(exc)[:240])


def _free_mb(path: str | os.PathLike[str]) -> int | None:
    try:
        return int(shutil.disk_usage(str(path)).free // (1024 * 1024))
    except Exception:
        return None


def _path_check(name: str, path: str | os.PathLike[str], *, critical: bool = False) -> dict[str, Any]:
    resolved = Path(path).expanduser()
    if not resolved.exists():
        return _check(name, FAIL if critical else WARN, "not found: %s" % resolved, critical=critical)
    free = _free_mb(resolved)
    suffix = " (%d MB free)" % free if free is not None else ""
    return _check(name, OK, "%s%s" % (resolved, suffix), critical=critical)


def _binary_check(name: str, *, critical: bool = False) -> dict[str, Any]:
    path = shutil.which(name)
    if path:
        version = ""
        try:
            proc = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=8)
            version = (proc.stdout or proc.stderr or "").splitlines()[0].strip()
        except Exception:
            pass
        return _check(name, OK, "%s%s" % (path, (" — " + version) if version else ""), critical=critical)
    return _check(name, FAIL if critical else WARN, "not found on PATH", critical=critical)


def _disk_checks(root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    free = _free_mb(root)
    if free is None:
        checks.append(_check("Project drive space", WARN, "unable to read disk usage"))
    else:
        checks.append(
            _check(
                "Project drive space",
                OK if free >= 1024 else FAIL,
                "%d MB free on %s (minimum 1024 MB; full transcription recommended >= 8192 MB)"
                % (free, root),
                critical=free < 1024,
            )
        )

    temp = Path(os.environ.get("TEMP") or os.environ.get("TMP") or str(root / ".runtime-tmp"))
    checks.append(_path_check("Runtime TEMP", temp, critical=False))
    temp_free = _free_mb(temp if temp.exists() else root)
    if temp_free is not None and temp_free < 1024:
        checks.append(_check("Runtime TEMP space", FAIL, "%d MB free; move TEMP/TMP to the project drive" % temp_free, critical=True))
    else:
        checks.append(_check("Runtime TEMP space", OK, "%d MB free" % temp_free if temp_free is not None else "unavailable"))

    if os.name == "nt":
        root_drive = os.path.splitdrive(str(root))[0].upper()
        temp_drive = os.path.splitdrive(str(temp))[0].upper()
        if root_drive and temp_drive and root_drive != temp_drive:
            checks.append(_check("D-drive temp policy", WARN, "project is on %s but TEMP is on %s; use setup_on_d.ps1" % (root_drive, temp_drive)))
        else:
            checks.append(_check("D-drive temp policy", OK, "TEMP/TMP are on the project drive (%s)" % (root_drive or "same drive")))
    return checks


def _torch_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    ok, detail = _import_probe("torch", "torch")
    if not ok:
        checks.append(_check("Torch", WARN, "missing or failed import — %s" % detail))
        checks.append(_check("CUDA / NVIDIA GPU", WARN, "cannot probe CUDA because Torch is unavailable"))
        for module_name, label, dist in (("torchaudio", "Torchaudio", "torchaudio"), ("whisperx", "WhisperX", "whisperx")):
            imported, info = _import_probe(module_name, dist)
            checks.append(_check(label, OK if imported else WARN, info))
        imported, info = _import_probe("huggingface_hub", "huggingface-hub")
        checks.append(_check("huggingface-hub", OK if imported else WARN, info))
        return checks

    try:
        torch = importlib.import_module("torch")
        cuda_build = getattr(getattr(torch, "version", None), "cuda", None) or "CPU build"
        cuda_ok = bool(torch.cuda.is_available())
        gpu_name = ""
        if cuda_ok:
            try:
                gpu_name = str(torch.cuda.get_device_name(0))
            except Exception:
                gpu_name = "GPU detected"
        checks.append(_check("Torch", OK, "%s; CUDA build: %s" % (detail, cuda_build)))
        checks.append(
            _check(
                "CUDA / NVIDIA GPU",
                OK if cuda_ok else WARN,
                "%s%s" % (
                    "available" if cuda_ok else "not available (CPU mode)",
                    (" — " + gpu_name) if gpu_name else "",
                ),
            )
        )
    except Exception as exc:  # pragma: no cover - only damaged torch installs
        checks.append(_check("CUDA / NVIDIA GPU", WARN, "Torch imported but CUDA probe failed: %s" % exc))

    for module_name, label, dist in (("torchaudio", "Torchaudio", "torchaudio"), ("whisperx", "WhisperX", "whisperx")):
        imported, info = _import_probe(module_name, dist)
        checks.append(_check(label, OK if imported else WARN, info))
    try:
        imported, info = _import_probe("huggingface_hub", "huggingface-hub")
        if imported:
            major = int(info.split(".", 1)[0]) if info[:1].isdigit() else 0
            checks.append(_check("huggingface-hub", OK if major < 1 else WARN, info if major < 1 else "%s — WhisperX requires <1.0" % info))
        else:
            checks.append(_check("huggingface-hub", WARN, info))
    except Exception as exc:
        checks.append(_check("huggingface-hub", WARN, str(exc)))
    return checks


def _oauth_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for module_name, label in (
        ("google_auth_oauthlib", "google-auth-oauthlib"),
        ("googleapiclient", "google-api-python-client"),
        ("google.oauth2", "google-auth"),
    ):
        imported, info = _import_probe(module_name, label)
        checks.append(_check(label, OK if imported else WARN, info))
    return checks


def _telegram_check(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Read optional Telegram environment settings without exposing secrets."""
    env = environ if environ is not None else os.environ
    enabled = str(env.get("VIRALCUTTER_TELEGRAM_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return _check("Telegram Control", WARN, "disabled - local polling will not start")
    token = str(env.get("VIRALCUTTER_TELEGRAM_BOT_TOKEN", "") or "").strip()
    raw_ids = str(env.get("VIRALCUTTER_TELEGRAM_CHAT_IDS", "") or "")
    chat_ids = {
        item for item in re.split(r"[\s,;]+", raw_ids.strip())
        if re.fullmatch(r"-?\d+", item)
    }
    if not token:
        return _check("Telegram Control", WARN, "enabled but bot token is missing (value is never shown)")
    if not chat_ids:
        return _check("Telegram Control", WARN, "enabled but no valid allowlisted Chat ID is configured")
    if not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{20,}", token):
        return _check("Telegram Control", WARN, "enabled with a token whose format could not be validated")
    return _check("Telegram Control", OK, "ready; local long polling; %d allowlisted Chat ID(s)" % len(chat_ids))


def collect(root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Collect a JSON-serializable diagnostic report without side effects."""
    root_path = Path(root or Path(__file__).resolve().parents[1]).expanduser().resolve()
    checks: list[dict[str, Any]] = [
        _check("Operating system", OK, "%s %s" % (platform.system(), platform.release())),
        _check("Python", OK if (3, 9) <= sys.version_info[:2] <= (3, 12) else FAIL, platform.python_version(), critical=not ((3, 9) <= sys.version_info[:2] <= (3, 12))),
        _path_check("Project root", root_path, critical=True),
        _path_check("Virtual environment", root_path / ".venv" / ("Scripts" if os.name == "nt" else "bin"), critical=False),
    ]
    checks.extend(_disk_checks(root_path))
    checks.extend([_binary_check("ffmpeg", critical=True), _binary_check("ffprobe", critical=True), _binary_check("deno")])
    checks.extend(_torch_checks())
    checks.extend(_oauth_checks())
    checks.append(_telegram_check())
    checks.extend(
        _check(label, OK if imported else WARN, info)
        for label, module_name, dist in (
            ("Gradio", "gradio", "gradio"),
            ("yt-dlp", "yt_dlp", "yt-dlp"),
        )
        for imported, info in [_import_probe(module_name, dist)]
    )
    critical = [item for item in checks if item["status"] == FAIL and item.get("critical")]
    warnings = [item for item in checks if item["status"] == WARN]
    return {
        "schema": 1,
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "app": "OUSSAMA Cutter",
        "root": str(root_path),
        "python": sys.executable,
        "checks": checks,
        "summary": {
            "critical_failures": len(critical),
            "warnings": len(warnings),
            "status": FAIL if critical else (WARN if warnings else OK),
        },
    }


def render(report: dict[str, Any]) -> str:
    icons = {OK: "✅", WARN: "⚠️", FAIL: "❌"}
    lines = ["=== OUSSAMA Cutter Windows diagnostics ===", "Root: %s" % report.get("root", "")]
    for item in report.get("checks", []):
        lines.append("%s %-28s %s" % (icons.get(item.get("status"), "  "), item.get("name", ""), item.get("detail", "")))
    summary = report.get("summary", {})
    lines.append("Summary: %s critical failure(s), %s warning(s)" % (summary.get("critical_failures", 0), summary.get("warnings", 0)))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Windows diagnostics for OUSSAMA Cutter")
    parser.add_argument("--root", default=None, help="project root; defaults to the repository root")
    parser.add_argument("--json", action="store_true", help="print JSON instead of the human-readable report")
    parser.add_argument("--output", default=None, help="also save the JSON report to this path")
    parser.add_argument("--strict", action="store_true", help="return 1 for optional warnings too")
    args = parser.parse_args(argv)
    report = collect(args.root)
    if args.output:
        output = Path(args.output).expanduser()
        if not output.is_absolute():
            output = Path(args.root or Path.cwd()) / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else render(report))
    if report["summary"]["critical_failures"]:
        return 1
    if args.strict and report["summary"]["warnings"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
