"""OUSSAMA Cutter system check.

Verifies runtime requirements BEFORE processing so users get a clear
report instead of a crash mid-pipeline.

Usage:  python scripts/doctor.py
Exit code 0 = all critical checks pass, 1 = something critical failed.
"""
import importlib
import os
import shutil
import subprocess
import sys

OK, WARN, FAIL = "ok", "warn", "fail"

ICONS = {OK: "✅", WARN: "⚠️ ", FAIL: "❌"}

MIN_PYTHON = (3, 9)
MAX_PYTHON = (3, 12)

# (module, critical?) — critical failures break the core pipeline
DEPENDENCIES = [
    ("numpy", True),
    ("yt_dlp", True),
    ("gradio", False),
    ("psutil", False),
    ("fastapi", False),
    ("uvicorn", False),
    ("cv2", False),
    ("mediapipe", False),
    ("insightface", False),
    ("torch", False),
    ("google.genai", False),
    ("deep_translator", False),
    ("g4f", False),
    ("tqdm", False),
    # v6 features (Roadmap 2.1 / 4.4) — optional but strongly recommended:
    # onnxruntime = local visual classifier, cryptography = real key encryption.
    ("onnxruntime", False),
    ("cryptography", False),
    # Full transcription stack (Roadmap "ready to run"): whisperx + torch make
    # the complete YouTube→shorts pipeline work; without them only the
    # editing/safety/polish features run.
    ("whisperx", False),
    ("torch", False),
]


def check_python():
    version = (sys.version_info.major, sys.version_info.minor)
    detail = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if version < MIN_PYTHON:
        return {"name": "Python", "status": FAIL,
                "detail": f"{detail} — required >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}"}
    if version > MAX_PYTHON:
        return {"name": "Python", "status": FAIL,
                "detail": f"{detail} — supported through Python {MAX_PYTHON[0]}.{MAX_PYTHON[1]}; use Python 3.12"}
    return {"name": "Python", "status": OK, "detail": detail}


def check_binary(name, critical=True):
    path = shutil.which(name)
    if path:
        return {"name": name, "status": OK, "detail": path}
    return {"name": name, "status": FAIL if critical else WARN,
            "detail": "not found on PATH"}


def check_dependency(module, critical):
    try:
        importlib.import_module(module)
        return {"name": module, "status": OK, "detail": "installed"}
    except Exception as exc:
        detail = "missing"
        if module in {"whisperx", "torch"}:
            detail += " — repair: .\\setup_on_d.ps1 -Mode Full -Transcription cpu"
            if str(exc):
                detail += " — " + str(exc)[:160]
        return {"name": module, "status": FAIL if critical else WARN,
                "detail": detail}


def check_ytdlp():
    """yt-dlp version — older builds fail to read Chrome cookies on Windows
    (Chrome 127+ App-Bound Encryption). Newer releases handle it."""
    try:
        import yt_dlp
        version = getattr(yt_dlp.version, '__version__', 'unknown')
        return {"name": "yt-dlp", "status": OK, "detail": version}
    except Exception as e:
        return {"name": "yt-dlp", "status": FAIL, "detail": str(e)}


def check_gpu():
    try:
        import torch
        if torch.cuda.is_available():
            return {"name": "GPU (CUDA)", "status": OK,
                    "detail": torch.cuda.get_device_name(0)}
        return {"name": "GPU (CUDA)", "status": WARN,
                "detail": "no CUDA device — CPU mode will be slow"}
    except Exception:
        return {"name": "GPU (CUDA)", "status": WARN,
                "detail": "torch not installed — cannot detect GPU"}


def check_disk_space(path="."):
    try:
        total, used, free = shutil.disk_usage(path)
        free_gb = free / (1024 ** 3)
        if free < 200 * 1024 * 1024:
            status = FAIL
        elif free_gb < 2:
            status = WARN
        else:
            status = OK
        return {"name": "Free disk space", "status": status, "detail": "{:.2f} GiB free".format(free_gb)}
    except OSError as exc:
        return {"name": "Free disk space", "status": WARN, "detail": str(exc)}


def check_ffmpeg_version():
    path = shutil.which("ffmpeg")
    if not path:
        return {"name": "FFmpeg version", "status": FAIL, "detail": "ffmpeg not found"}
    try:
        proc = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10)
        first_line = (proc.stdout or "").splitlines()[0] if proc.stdout else "unknown"
        return {"name": "FFmpeg version", "status": OK if proc.returncode == 0 else WARN, "detail": first_line[:200]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"name": "FFmpeg version", "status": WARN, "detail": str(exc)}


def check_writable(path="."):
    try:
        test_path = os.path.join(path, ".vc_write_test")
        with open(test_path, "w") as f:
            f.write("ok")
        os.remove(test_path)
        return {"name": "Working dir writable", "status": OK, "detail": os.path.abspath(path)}
    except Exception as e:
        return {"name": "Working dir writable", "status": FAIL, "detail": str(e)}


def run_checks():
    checks = [
        check_python(),
        check_binary("ffmpeg", critical=True),
        check_binary("ffprobe", critical=True),
        check_ytdlp(),
        check_ffmpeg_version(),
        check_disk_space(),
        check_writable(),
        check_gpu(),
    ]
    checks.extend(check_dependency(mod, crit) for mod, crit in DEPENDENCIES)
    return checks


def main():
    print("\n=== OUSSAMA Cutter Doctor ===\n")
    checks = run_checks()
    critical_failed = 0
    for c in checks:
        print(f"{ICONS[c['status']]} {c['name']:<24} {c['detail']}")
        if c["status"] == FAIL:
            critical_failed += 1

    print()
    if critical_failed:
        print(f"❌ {critical_failed} critical check(s) failed. Fix them before running OUSSAMA Cutter.")
        return 1
    warns = sum(1 for c in checks if c["status"] == WARN)
    print(f"✅ System ready. ({warns} optional warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
