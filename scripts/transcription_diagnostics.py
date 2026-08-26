"""تشخيص وإصلاح مكوّنات التفريغ في OUSSAMA Cutter.

هذه الوحدة خفيفة ولا تستورد Torch أو WhisperX مباشرة أثناء الإقلاع. تفحصهما
بشكل آمن، وتوفر إصلاحاً صريحاً يختاره المستخدم بدلاً من تنزيل حزم ثقيلة
تلقائياً داخل WebUI.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class TranscriptionUnavailableError(ImportError):
    """خطأ قابل للتمييز حتى لا يعيد التطبيق تشغيل العملية بلا فائدة."""

    dependency_error = True


APP_NAME = "OUSSAMA Cutter"
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_FREE_MB = 4096


def _probe(module_name: str) -> Dict[str, Any]:
    try:
        module = importlib.import_module(module_name)
        return {
            "module": module_name,
            "ok": True,
            "version": str(getattr(module, "__version__", "installed")),
            "error": "",
        }
    except Exception as exc:  # optional stack: report, never crash diagnostics
        return {
            "module": module_name,
            "ok": False,
            "version": "",
            "error": str(exc),
        }


def _torch_runtime() -> Dict[str, Any]:
    """Probe the actual Torch backend without failing diagnostics."""
    try:
        torch = importlib.import_module("torch")
        cuda_version = str(getattr(getattr(torch, "version", None), "cuda", "") or "")
        cuda_available = bool(torch.cuda.is_available())
        device_name = ""
        if cuda_available:
            try:
                device_name = str(torch.cuda.get_device_name(0))
            except Exception:
                device_name = "NVIDIA GPU"
        return {"torch_version": str(getattr(torch, "__version__", "installed")), "cuda_version": cuda_version, "cuda_available": cuda_available, "device_name": device_name, "error": ""}
    except Exception as exc:
        return {"torch_version": "", "cuda_version": "", "cuda_available": False, "device_name": "", "error": str(exc)}


def diagnose(base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Return a JSON-serialisable health report for the transcription stack."""
    base_dir = os.path.abspath(base_dir or os.getcwd())
    try:
        free_mb = shutil.disk_usage(base_dir).free // (1024 * 1024)
    except Exception:
        free_mb = None
    probes = {name: _probe(name) for name in ("torch", "torchaudio", "whisperx", "faster_whisper")}
    primary_ready = bool(probes["torch"]["ok"] and probes["torchaudio"]["ok"] and probes["whisperx"]["ok"])
    fallback_ready = bool(probes["faster_whisper"]["ok"])
    missing = [name for name, result in probes.items() if not result["ok"]]
    torch_runtime = _torch_runtime()
    return {
        "app": APP_NAME,
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": sys.platform,
        "base_dir": base_dir,
        "free_disk_mb": free_mb,
        "disk_ok_for_repair": free_mb is None or free_mb >= MIN_FREE_MB,
        "ready": bool(primary_ready or fallback_ready),
        "primary_ready": primary_ready,
        "fallback_ready": fallback_ready,
        "backend": "whisperx" if primary_ready else ("faster-whisper" if fallback_ready else "none"),
        "gpu_ready": bool((primary_ready or fallback_ready) and torch_runtime["cuda_available"] and torch_runtime["cuda_version"]),
        "missing": missing,
        "packages": probes,
        "torch_runtime": torch_runtime,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def build_error_message(
    whisperx_error: str = "",
    torch_error: str = "",
    base_dir: Optional[str] = None,
) -> str:
    """Create a concise Arabic recovery message for the pipeline and CLI."""
    report = diagnose(base_dir=base_dir)
    lines = ["التفريغ الصوتي غير متاح حالياً في OUSSAMA Cutter."]
    if report["missing"]:
        lines.append("المكوّنات الناقصة أو التي فشل استيرادها: {}.".format(
            ", ".join(report["missing"])))
    if torch_error:
        lines.append("تفاصيل Torch: {}".format(torch_error))
    if whisperx_error:
        lines.append("تفاصيل WhisperX: {}".format(whisperx_error))
    combined_error = "{} {}".format(whisperx_error, torch_error).lower()
    if "huggingface-hub" in combined_error or "huggingface_hub" in combined_error:
        lines.extend([
            "يوجد تعارض بين Transformers وhuggingface-hub داخل بيئة التفريغ.",
            "نفّذ من مجلد المشروع: uv pip install --python .\\.venv\\Scripts\\python.exe --upgrade \\\"huggingface-hub>=0.34.0,<1.0\\\"",
        ])
    if report.get("fallback_ready"):
        lines.append("يتوفر faster-whisper كمسار احتياطي مستقل؛ شغّل OUSSAMA مجدداً بعد تثبيته لاستخدامه تلقائياً.")
    else:
        lines.extend([
            "لتجنب توقف التفريغ عند تعطل WhisperX، ثبّت المسار الاختياري faster-whisper من requirements-transcribe-fallback.txt.",
            "    .\\.venv\\Scripts\\python.exe -m scripts.transcription_diagnostics --repair-fallback",
        ])
    lines.extend([
        "هذا لا يعني أن المشروع تالف؛ وضع المونتاج والأمان يعملان بدون حزمة التفريغ.",
        "لإصلاح المسار الكامل على Windows من مجلد المشروع:",
        "    .\\setup_on_d.ps1 -Mode Full -Transcription cpu",
        "أو مباشرة:",
        "    .\\.venv\\Scripts\\python.exe -m scripts.transcription_diagnostics --repair cpu",
        "للجهاز الذي يملك NVIDIA استخدم: --repair gpu أو -Transcription gpu.",
        "بعد التثبيت أعد تشغيل OUSSAMA Cutter. لا تستخدم الترجمة الوهمية في إنتاج حقيقي.",
        "تم إيقاف العملية بأمان بدلاً من إعادة المحاولة تلقائياً بلا فائدة.",
    ])
    return "\n".join(lines)


def write_report(
    project_folder: Optional[str],
    error: Optional[BaseException] = None,
) -> Optional[str]:
    """Persist a local diagnostic report beside the project when possible."""
    target_dir = os.path.abspath(project_folder or os.getcwd())
    try:
        os.makedirs(target_dir, exist_ok=True)
        report = diagnose(base_dir=target_dir)
        if error is not None:
            report["pipeline_error"] = str(error)
        path = os.path.join(target_dir, "transcription_diagnostic.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        return path
    except Exception:
        return None


def _pip_command(args: list[str]) -> list[str]:
    """Build a pip install command, falling back to uv for pip-less venvs."""
    if importlib.util.find_spec("pip") is not None:
        return [sys.executable, "-m", "pip", "install"] + args
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--python", sys.executable] + args
    return [sys.executable, "-m", "pip", "install"] + args


def _run_install(command: list[str], timeout: int = 1800) -> Dict[str, Any]:
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "command": command, "error": str(exc), "output": ""}
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return {
        "ok": proc.returncode == 0,
        "command": command,
        "returncode": proc.returncode,
        "error": "" if proc.returncode == 0 else output[-3000:],
        "output": output[-3000:],
    }


def repair_fallback(base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Install only the independent faster-whisper recovery backend."""
    requirement_file = os.path.join(APP_ROOT, "requirements-transcribe-fallback.txt")
    if not os.path.exists(requirement_file):
        return {"ok": False, "mode": "fallback", "steps": [], "error": "requirements-transcribe-fallback.txt is missing"}
    steps = [_run_install(_pip_command(["--upgrade", "-r", requirement_file]))]
    final = diagnose(base_dir=base_dir)
    ok = bool(final.get("fallback_ready"))
    return {
        "ok": ok,
        "mode": "fallback",
        "diagnostic": final,
        "steps": steps,
        "error": "" if ok else "faster-whisper was not importable after installation",
    }


def repair(mode: str = "cpu", base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Install the transcription stack explicitly in CPU or NVIDIA CUDA mode."""
    mode = str(mode or "cpu").lower().strip()
    if mode not in {"cpu", "gpu"}:
        raise ValueError("mode must be cpu or gpu")
    report = diagnose(base_dir=base_dir)
    if report["free_disk_mb"] is not None and report["free_disk_mb"] < MIN_FREE_MB:
        return {
            "ok": False,
            "mode": mode,
            "error": "مساحة القرص غير كافية؛ يلزم 4GB على الأقل لإصلاح مكوّنات التفريغ.",
            "diagnostic": report,
            "steps": [],
        }
    index_url = (
        "https://download.pytorch.org/whl/cu124"
        if mode == "gpu" else "https://download.pytorch.org/whl/cpu"
    )
    steps = [
        _run_install(_pip_command(["--upgrade", "pip"])),
        _run_install(_pip_command([
            "--upgrade", "torch", "torchaudio", "--index-url", index_url,
        ])),
        _run_install(_pip_command([
            "--upgrade", "whisperx", "huggingface-hub>=0.34.0,<1.0", "numpy<2",
        ])),
    ]
    if mode == "gpu":
        # WhisperX may resolve a CPU torch wheel from PyPI. Restore CUDA last.
        steps.append(_run_install(_pip_command([
            "--upgrade", "--force-reinstall", "torch", "torchaudio", "--index-url", index_url,
        ])))
    final = diagnose(base_dir=base_dir)
    ok = bool(final["ready"] and (mode != "gpu" or final["gpu_ready"]))
    return {
        "ok": ok,
        "mode": mode,
        "diagnostic": final,
        "steps": steps,
        "error": "" if ok else (build_error_message(base_dir=base_dir) if not final["ready"] else "Torch CUDA أو تعريف NVIDIA غير جاهز؛ تحقق من torch.version.cuda وtorch.cuda.is_available()."),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="OUSSAMA Cutter transcription diagnostics")
    parser.add_argument("--repair", choices=["cpu", "gpu"], help="install the WhisperX transcription stack")
    parser.add_argument("--repair-fallback", action="store_true", help="install only the faster-whisper fallback")
    parser.add_argument("--report", help="write JSON report to this folder")
    parser.add_argument("--json", action="store_true", help="print machine-readable output")
    args = parser.parse_args(argv)
    if args.repair_fallback:
        result = repair_fallback()
    elif args.repair:
        result = repair(args.repair)
    else:
        result = {"ok": diagnose()["ready"], "diagnostic": diagnose()}
    if args.report:
        path = write_report(args.report)
        result["report_path"] = path
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        diagnostic = result.get("diagnostic", {})
        print("=== {} — فحص التفريغ ===".format(APP_NAME))
        for name, item in diagnostic.get("packages", {}).items():
            print("{} {}: {}".format("✅" if item.get("ok") else "❌", name, item.get("version") or item.get("error")))
        if result.get("ok"):
            print("✅ مكوّنات التفريغ جاهزة.")
        elif result.get("error"):
            print(result["error"])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
