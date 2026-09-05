"""Fail-closed readiness checks for the strict AI safety autopilot."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _binary(name: str) -> str | None:
    return shutil.which(name)


def _tesseract_languages(binary: str | None) -> set[str]:
    if not binary:
        return set()
    try:
        result = subprocess.run([binary, "--list-langs"], capture_output=True, text=True, timeout=10)
        return {line.strip() for line in result.stdout.splitlines()[1:] if line.strip()}
    except Exception:
        return set()


def check_readiness(args: Any, ai_backend: str, api_key: str | None = None) -> dict[str, Any]:
    """Return blocking issues and warnings before expensive media processing."""
    issues: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    binaries = {name: _binary(name) for name in ("ffmpeg", "ffprobe", "tesseract", "fpcalc")}
    for name in ("ffmpeg", "ffprobe"):
        if not binaries[name]:
            issues.append({"code": f"missing_{name}", "detail": f"{name} is required for autopilot."})

    if str(getattr(args, "ocr_check", "off")) == "on":
        if not binaries["tesseract"]:
            issues.append({"code": "missing_tesseract", "detail": "Tesseract is required for Arabic/English on-screen text safety scanning."})
        else:
            languages = _tesseract_languages(binaries["tesseract"])
            missing = sorted({"ara", "eng"} - languages)
            if missing:
                issues.append({"code": "missing_ocr_languages", "detail": "Install Tesseract language packs: {}.".format(", ".join(missing))})

    if str(getattr(args, "visual_check", "off")) == "on":
        model = getattr(args, "visual_model", None)
        if not model:
            try:
                from scripts import visual_check
                model = visual_check.default_model_path()
            except Exception:
                model = None
        if not model or not os.path.isfile(model):
            issues.append({"code": "missing_visual_model", "detail": "A local ONNX visual safety model is required when visual safety is strict."})

    backend = str(ai_backend or "").lower()
    if backend not in {"gemini", "g4f", "openai-moderation"}:
        issues.append({"code": "missing_ai_backend", "detail": "Autopilot requires Gemini, G4F, or OpenAI moderation for contextual policy review."})
    if backend in {"gemini", "openai-moderation"} and not str(api_key or "").strip():
        label = "Gemini" if backend == "gemini" else "OpenAI moderation"
        issues.append({"code": "missing_ai_key", "detail": "{} API key is missing.".format(label)})
    if not binaries["fpcalc"]:
        warnings.append({"code": "missing_fpcalc", "detail": "Music fingerprinting will remain unavailable until fpcalc is installed."})

    return {
        "generated_at": _now(),
        "mode": "autopilot",
        "ok": not issues,
        "issues": issues,
        "warnings": warnings,
        "binaries": binaries,
        "ai_backend": backend,
    }


def write_report(project_folder: str | None, report: dict[str, Any]) -> str | None:
    if not project_folder:
        return None
    os.makedirs(project_folder, exist_ok=True)
    path = os.path.join(project_folder, "autopilot_readiness.json")
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)
    return path
