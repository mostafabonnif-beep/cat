# -*- coding: utf-8 -*-
"""ViralCutter pre-flight check & auto-repair.

Runs BEFORE the app (CLI or WebUI) so every missing piece is detected and —
when possible — installed/repaired automatically. The goal: when the app
starts, everything is in place and nothing is missing.

Design rules:
  * Pure Python stdlib — it must run even on a machine where NOTHING is
    installed yet (that is exactly the case it has to fix).
  * Exit codes are machine-readable:
        0 = ready (all critical checks pass)
        1 = critical problems remain after repair
        2 = warnings only (optional things missing)

Usage:
  python -m scripts.preflight               # read-only check
  python -m scripts.preflight --fix         # interactive repair (asks y/n)
  python -m scripts.preflight --auto-fix    # non-interactive repair
  python -m scripts.preflight --json        # machine-readable report
  python -m scripts.preflight --off         # exit 0 immediately (CI escape)

The same module is imported by main_improved.py / webui/app.py through
run_preflight(mode) — see the integration notes there.
"""
import argparse
import importlib
import importlib.metadata as _pkg_meta
import importlib.util as _iu
import json
import os
import re
import shutil
import subprocess
import sys

try:
    from app_brand import APP_NAME
except Exception:
    APP_NAME = "OUSSAMA Cutter"

OK, WARN, FAIL = "ok", "warn", "fail"
ICONS = {OK: "✅", WARN: "⚠️ ", FAIL: "❌"}

MIN_PYTHON = (3, 9)
MIN_PYTHON_RECOMMENDED = (3, 10)
# The project pins NumPy <2 for WhisperX/pyannote compatibility. NumPy 1.26
# wheels support Python through 3.12; fail early on 3.13+ instead of letting
# pip attempt a source build that requires MSVC.
MAX_PYTHON = (3, 12)
MIN_FREE_DISK_MB = 1024  # transcription + ffmpeg temp files need headroom

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --------------------------------------------------------------------------
# Dependency catalogue
# --------------------------------------------------------------------------
# requirement file -> (label, optional?, install hint)
REQ_FILES = {
    "requirements.txt": ("Core", False, "required for the app to boot"),
    "requirements-transcribe.txt": ("Transcription (whisperx + torch)", True,
                                    "needed for the FULL YouTube->shorts pipeline (~2 GB)"),
    "requirements-transcribe-fallback.txt": ("Transcription fallback (faster-whisper)", True,
                                              "optional recovery path when WhisperX is unavailable"),
    "requirements-upload.txt": ("Upload (YouTube OAuth)", True,
                                "needed only for direct uploads"),
}

# Distribution name -> import module. Covers the dotted-import packages.
DIST_TO_MODULE = {
    "yt-dlp": "yt_dlp",
    "opencv-python": "cv2",
    "ffmpeg-python": "ffmpeg",
    "faster-whisper": "faster_whisper",
    "google-genai": "google.genai",
    "google-generativeai": "google.generativeai",
    "google-api-python-client": "googleapiclient",
    "google-auth-oauthlib": "google_auth_oauthlib",
    "google-auth-httplib2": "google_auth_httplib2",
    "pyacoustid": "pyacoustid",
    "Pillow": "PIL",
    "PyYAML": "yaml",
    "python-multipart": "multipart",
    "numpy": "numpy",
    "g4f": "g4f",
    "mediapipe": "mediapipe",
    "insightface": "insightface",
    "gradio": "gradio",
    "psutil": "psutil",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "deep-translator": "deep_translator",
    "tqdm": "tqdm",
    "onnxruntime": "onnxruntime",
    "cryptography": "cryptography",
    "whisperx": "whisperx",
    "torch": "torch",
    "torchaudio": "torchaudio",
}

# Version pins that MUST be enforced at runtime (import alone is not enough).
PINNED_RULES = [
    # (dist, violated_if, fix_note)
    ("numpy", "numpy<2", "whisperx/pyannote break on NumPy 2.x - installing numpy==1.26.4"),
    # transformers (the 4.x / <=5.14 line this project pins, via
    # huggingface-hub<1.0) requires tokenizers>=0.22.0,<=0.23.0, and
    # tokenizers 0.23.0 was never released on PyPI (the jump went 0.22.2 ->
    # 0.23.1). tokenizers 0.23.1 makes transformers fail its import check:
    #   ImportError: tokenizers>=0.22.0,<=0.23.0 is required ... but found
    #   tokenizers==0.23.1
    # so cap below 0.23.1 (resolves to the known-good 0.22.2).
    ("tokenizers", "tokenizers<0.23.1",
     "transformers<=5.14 caps tokenizers at 0.23.0 and 0.23.0 does not exist - installing tokenizers==0.22.2"),
    # transformers 4.x (the line forced by the huggingface-hub<1.0 pin in
    # requirements-transcribe.txt / install_dependencies.bat) refuses to
    # import with huggingface-hub>=1.0:
    #   ImportError: huggingface-hub>=0.34.0,<1.0 is required ... but found
    #   huggingface-hub==1.29.0
    # A partial upgrade that bumps huggingface-hub alone produces this. Note:
    # transformers 5.14+/5.16 (the uv.lock world) legitimately needs hub>=1.5,
    # so the cap is only enforced below 5.16 (see check_pin).
    ("huggingface-hub", "huggingface-hub<1.0",
     "transformers 4.x requires huggingface-hub<1.0 - installing huggingface-hub>=0.34.0,<1.0"),
]

# --------------------------------------------------------------------------
# Asset catalogue (files that must ship with the repo/app)
# --------------------------------------------------------------------------
ASSETS = [
    "fonts/Montserrat-Regular.ttf",
    "fonts/Montserrat-Bold.ttf",
    "fonts/Montserrat-ExtraBold.ttf",
    "safety_blocklist.json",
    "safety_terms.example.json",
    "i18n/locale/en_US.json",
    "i18n/locale/ar_SA.json",
    "i18n/locale/pt_BR.json",
    "i18n/locale/tr_TR.json",
]

API_CONFIG_TEMPLATE = {
    "selected_api": "gemini",
    "gemini": {"api_key": "", "model": "gemini-2.5-flash-lite-preview-09-2025", "chunk_size": 20000},
    "g4f": {"model": "gpt-4o-mini", "chunk_size": 2000},
}

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _is_frozen():
    return bool(getattr(sys, "frozen", False))


def _app_dir():
    if _is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return APP_ROOT


def _parse_req_line(line):
    """Parse one requirements.txt line -> (dist_name, raw_spec) or None."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    # strip inline comments (careful: urls contain #)
    if " #" in line:
        line = line.split(" #", 1)[0].strip()
    if line.startswith(("-e ", "--", "-r ", "-c ")) or "://" in line:
        return None
    # "name[extra]==1.0" -> name; "name @ url" -> skip url form
    if " @ " in line:
        line = line.split(" @ ", 1)[0]
    name = line.split("==")[0].split("<")[0].split(">")[0].split("~=")[0].split("!=")[0].strip()
    name = name.split("[")[0].strip()
    if not name:
        return None
    return name, line


def _read_req_names(req_file):
    """Return ordered list of (dist_name, raw_line) from a requirements file."""
    out = []
    path = os.path.join(APP_ROOT, req_file)
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            parsed = _parse_req_line(line)
            if parsed:
                out.append(parsed)
    return out


def _import_name(dist):
    return DIST_TO_MODULE.get(dist, dist.replace("-", "_"))


def _dist_installed(dist):
    try:
        _pkg_meta.version(dist)
        return True
    except _pkg_meta.PackageNotFoundError:
        return False


def _module_importable(module):
    try:
        importlib.import_module(module)
        return True
    except Exception:
        return False


def _version(dist):
    try:
        return _pkg_meta.version(dist)
    except Exception:
        return "?"

# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------
def check_python():
    v = sys.version_info
    version_text = "%d.%d.%d" % (v[0], v[1], v[2])
    if v < MIN_PYTHON:
        return {"name": "Python", "status": FAIL,
                "detail": "%s - required >= %d.%d" % (version_text, MIN_PYTHON[0], MIN_PYTHON[1])}
    if (v[0], v[1]) > MAX_PYTHON:
        return {"name": "Python", "status": FAIL,
                "detail": "%s - supported range is Python %d.%d through %d.%d; use Python 3.12" % (
                    version_text, MIN_PYTHON[0], MIN_PYTHON[1], MAX_PYTHON[0], MAX_PYTHON[1])}
    status = OK if v >= MIN_PYTHON_RECOMMENDED else WARN
    note = "recommended 3.10/3.11" if status == WARN else ""
    return {"name": "Python", "status": status,
            "detail": "%s%s" % (version_text, (" - " + note) if note else "")}


def check_binary(name, critical=True):
    path = shutil.which(name)
    if path:
        return {"name": name, "status": OK, "detail": path}
    return {"name": name, "status": FAIL if critical else WARN,
            "detail": "not found on PATH"}


def check_bundled_binary(name):
    """Frozen exe: tools live in sys._MEIPASS - check there first."""
    if not _is_frozen():
        return None
    bundle = getattr(sys, "_MEIPASS", "") or ""
    exe = name + (".exe" if os.name == "nt" else "")
    for base in (bundle, _app_dir()):
        cand = os.path.join(base, exe)
        if os.path.exists(cand):
            return {"name": "%s (bundled)" % name, "status": OK, "detail": cand}
    return None


def check_disk_space(path):
    try:
        free = shutil.disk_usage(path).free // (1024 * 1024)
        status = OK if free >= MIN_FREE_DISK_MB else FAIL
        return {"name": "Disk space", "status": status,
                "detail": "%d MB free (need >= %d MB)" % (free, MIN_FREE_DISK_MB)}
    except Exception as e:
        return {"name": "Disk space", "status": WARN, "detail": "cannot check: %s" % e}


def check_writable(path, label):
    try:
        os.makedirs(path, exist_ok=True)
        test_path = os.path.join(path, ".vc_write_test")
        with open(test_path, "w") as fh:
            fh.write("ok")
        os.remove(test_path)
        return {"name": label, "status": OK, "detail": os.path.abspath(path)}
    except Exception as e:
        return {"name": label, "status": FAIL, "detail": str(e)}


def check_dependency(dist, raw_line, critical):
    module = _import_name(dist)
    if not _module_importable(module):
        installed = _dist_installed(dist)
        detail = "missing (%s)" % dist if not installed else \
            "installed but import failed (%s)" % module
        return {"name": dist, "status": FAIL if critical else WARN, "detail": detail}
    return {"name": dist, "status": OK, "detail": _version(dist)}


def check_pin(dist, rule, note=None):
    """Enforce PINNED_RULES. Returns None when ok/unverifiable, else a FAIL dict."""
    spec = _iu.find_spec(dist.replace("-", "_"))
    if spec is None:
        return None  # missing -> handled by check_dependency
    try:
        import packaging.version as _pv  # may not exist before core install
    except Exception:
        return None
    if note is None:
        for _dist, _rule, _note in PINNED_RULES:
            if _dist == dist and _rule == rule:
                note = _note
                break
        note = note or "version pin %s violated" % rule
    try:
        installed = _pv.parse(_version(dist))
        if "<" not in rule:
            return None
        if dist == "tokenizers":
            # Only relevant while the installed transformers is the pre-5.16
            # line (the huggingface-hub<1.0 world). transformers>=5.16
            # *requires* tokenizers>=0.23.1, so never pin tokenizers down in
            # that world.
            if not _dist_installed("transformers"):
                return None
            tver = _version("transformers")
            if tver != "?" and _pv.parse(tver) >= _pv.parse("5.16"):
                return None
        if dist == "huggingface-hub":
            # transformers 5.x (including 5.14/5.16 — the uv.lock world)
            # requires huggingface-hub>=1.5, so only pin hub <1.0 while the
            # installed transformers is the 4.x line.
            if not _dist_installed("transformers"):
                return None
            tver = _version("transformers")
            if tver != "?" and _pv.parse(tver) >= _pv.parse("5.0"):
                return None
        boundary = _pv.parse(rule.split("<", 1)[1])
        if installed >= boundary:
            return {"name": dist, "status": FAIL,
                    "detail": "version %s violates %s - %s" % (_version(dist), rule, note)}
    except Exception:
        return None
    return None


def check_api_config():
    path = os.path.join(_app_dir(), "api_config.json")
    if not os.path.exists(path):
        return {"name": "api_config.json", "status": FAIL,
                "detail": "missing - will be created from template (add your AI key afterwards)"}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as e:
        return {"name": "api_config.json", "status": FAIL, "detail": "invalid JSON: %s" % e}
    missing = [k for k in ("selected_api", "gemini") if k not in data]
    if missing:
        return {"name": "api_config.json", "status": WARN,
                "detail": "missing keys %s - defaulting works but is fragile" % missing}
    key = (data.get("gemini") or {}).get("api_key") or ""
    if not key:
        return {"name": "Gemini API key", "status": WARN,
                "detail": "empty - AI analysis needs a key in api_config.json (or use --ai-backend manual/g4f/local)"}
    if not key.startswith("AIza"):
        return {"name": "Gemini API key", "status": WARN,
                "detail": "does not look like a Gemini key (should start with AIza)"}
    return {"name": "Gemini API key", "status": OK, "detail": "configured"}


def check_assets():
    missing = [a for a in ASSETS if not os.path.exists(os.path.join(APP_ROOT, a))]
    if missing:
        return {"name": "Packaged assets", "status": FAIL,
                "detail": "missing: %s - re-clone/re-extract the app; a damaged install will fail mid-pipeline"
                % ", ".join(missing[:3])}
    return {"name": "Packaged assets", "status": OK, "detail": "%d files present" % len(ASSETS)}


def check_models_dir():
    path = os.path.join(APP_ROOT, "models")
    if os.path.isdir(path):
        files = [f for f in os.listdir(path) if not f.startswith(".")]
        if files:
            return {"name": "models/", "status": OK,
                    "detail": "%d local model(s): %s" % (len(files), ", ".join(files[:3]))}
        return {"name": "models/", "status": WARN,
                "detail": "empty - local GGUF models go here (optional; cloud AI works without them)"}
    return {"name": "models/", "status": WARN, "detail": "missing - will be created (optional)"}


def check_telegram_config(environ=None):
    """Check optional local Telegram settings without importing the WebUI."""
    env = environ if environ is not None else os.environ
    enabled = str(env.get("VIRALCUTTER_TELEGRAM_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return {"name": "Telegram Control", "status": WARN,
                "detail": "disabled - no local polling will start"}
    token = str(env.get("VIRALCUTTER_TELEGRAM_BOT_TOKEN", "") or "").strip()
    raw_ids = str(env.get("VIRALCUTTER_TELEGRAM_CHAT_IDS", "") or "")
    values = [item for item in re.split(r"[\s,;]+", raw_ids.strip()) if item]
    chat_ids = {item for item in values if re.fullmatch(r"-?\d+", item)}
    if not token:
        return {"name": "Telegram Control", "status": WARN,
                "detail": "enabled but bot token is missing (value is never shown)"}
    if not chat_ids:
        return {"name": "Telegram Control", "status": WARN,
                "detail": "enabled but no valid allowlisted Chat ID is configured"}
    if not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{20,}", token):
        return {"name": "Telegram Control", "status": WARN,
                "detail": "enabled with a token whose format could not be validated"}
    return {"name": "Telegram Control", "status": OK,
            "detail": "ready; local long polling; %d allowlisted Chat ID(s)" % len(chat_ids)}

# --------------------------------------------------------------------------
# Repair actions
# --------------------------------------------------------------------------
def _run_pip(args):
    """Install packages through venv pip or uv pip when uv created a pip-less venv."""
    if _module_importable("pip"):
        cmd = [sys.executable, "-m", "pip", "install"] + args
    else:
        uv = shutil.which("uv")
        if not uv:
            return None
        cmd = [uv, "pip", "install", "--python", sys.executable] + args
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except Exception:
        return None


def install_requirements(req_file, dry_run=False):
    """Install every line of a requirements file via pip. Returns (ok, detail)."""
    deps = _read_req_names(req_file)
    if not deps:
        return True, "nothing to install (%s empty)" % req_file
    if dry_run:
        return False, "would run: pip install -r %s" % req_file
    if _is_frozen():
        return False, "cannot pip-install inside the packaged exe (bundled deps are fixed)"
    proc = _run_pip(["-r", os.path.join(APP_ROOT, req_file)])
    if proc is None:
        return False, "pip unavailable (%s)" % sys.executable
    if proc.returncode == 0:
        return True, "installed %d packages from %s" % (len(deps), req_file)
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [""]
    return False, "pip failed (exit %d) on %s: %s" % (proc.returncode, req_file, tail[0][:200])


def fix_numpy_pin(dry_run=False):
    """NumPy 2 breaks whisperx/pyannote - force the known-good 1.26.4 wheel."""
    spec = _iu.find_spec("numpy")
    if spec is None:
        return True, "numpy not installed - nothing to pin"
    try:
        import packaging.version as _pv
        from numpy import __version__ as v
        if _pv.parse(v) < _pv.parse("2.0"):
            return True, "numpy %s OK (<2)" % v
    except Exception:
        return True, "cannot read numpy version"
    if dry_run:
        return False, "would downgrade numpy (currently %s)" % v
    if _is_frozen():
        return False, "cannot reinstall numpy inside the packaged exe"
    proc = _run_pip(["--force-reinstall", "--no-cache-dir", "--no-deps", "numpy==1.26.4"])
    if proc is not None and proc.returncode == 0:
        return True, "pinned numpy to 1.26.4 (<2)"
    return False, "could not downgrade numpy (exit %s)" % (proc.returncode if proc else "?")


def fix_tokenizers_pin(dry_run=False):
    """tokenizers 0.23.1 breaks the transformers import check (the installed
    transformers caps at <=0.23.0, and 0.23.0 was never released on PyPI).
    Force the known-good 0.22.2 wheel."""
    spec = _iu.find_spec("tokenizers")
    if spec is None:
        return True, "tokenizers not installed - nothing to pin"
    try:
        import packaging.version as _pv
        v = _version("tokenizers")
        if v != "?" and _pv.parse(v) < _pv.parse("0.23.1"):
            return True, "tokenizers %s OK (<0.23.1)" % v
    except Exception:
        return True, "cannot read tokenizers version"
    if dry_run:
        return False, "would downgrade tokenizers (currently %s)" % v
    if _is_frozen():
        return False, "cannot reinstall tokenizers inside the packaged exe"
    proc = _run_pip(["--force-reinstall", "--no-cache-dir", "--no-deps", "tokenizers==0.22.2"])
    if proc is not None and proc.returncode == 0:
        return True, "pinned tokenizers to 0.22.2 (<0.23.1)"
    return False, "could not downgrade tokenizers (exit %s)" % (proc.returncode if proc else "?")


def fix_hub_pin(dry_run=False):
    """transformers 4.x refuses to import with huggingface-hub>=1.0 (a partial
    upgrade that bumped hub alone leaves this broken state). Force the hub back
    below 1.0 WITH its own dependencies (hub is not a leaf package)."""
    spec = _iu.find_spec("huggingface_hub")
    if spec is None:
        return True, "huggingface-hub not installed - nothing to pin"
    try:
        import packaging.version as _pv
        v = _version("huggingface-hub")
        if v != "?" and _pv.parse(v) < _pv.parse("1.0"):
            return True, "huggingface-hub %s OK (<1.0)" % v
    except Exception:
        return True, "cannot read huggingface-hub version"
    if dry_run:
        return False, "would downgrade huggingface-hub (currently %s)" % v
    if _is_frozen():
        return False, "cannot reinstall huggingface-hub inside the packaged exe"
    # NOT --no-deps: hub has runtime dependencies (httpx, filelock, fsspec...)
    # and uv may need to re-align transformers' own constraint.
    proc = _run_pip(["--force-reinstall", "--no-cache-dir", "huggingface-hub>=0.34.0,<1.0"])
    if proc is not None and proc.returncode == 0:
        return True, "pinned huggingface-hub to <1.0"
    return False, "could not downgrade huggingface-hub (exit %s)" % (proc.returncode if proc else "?")


def ensure_dirs():
    """Create the folders the app expects. Returns list of (label, ok, detail)."""
    out = []
    for rel in ["models", "fonts", "i18n/locale", "VIRALS", "logs"]:
        path = os.path.join(_app_dir(), rel)
        try:
            os.makedirs(path, exist_ok=True)
            out.append((rel, True, "created/ok"))
        except Exception as e:
            out.append((rel, False, str(e)))
    return out


def ensure_api_config():
    """Create api_config.json from template when missing/invalid. Returns (ok, detail)."""
    path = os.path.join(_app_dir(), "api_config.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                json.load(fh)
            return True, "api_config.json present and valid"
        except Exception:
            pass  # fall through: rewrite a valid template (backup broken file)
    backup = path + ".bak"
    try:
        if os.path.exists(path):
            shutil.copy2(path, backup)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(API_CONFIG_TEMPLATE, fh, indent=4, ensure_ascii=False)
        return True, "api_config.json %s (add your AI key in it)" % ("recreated" if os.path.exists(backup) else "created")
    except Exception as e:
        return False, "could not write api_config.json: %s" % e

# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def collect_checks():
    """Run every check. Returns (checks, critical_names, warn_names)."""
    checks = [check_python(), check_binary("ffmpeg", critical=True),
              check_binary("ffprobe", critical=True)]
    bundled = check_bundled_binary("ffmpeg")
    if bundled:
        checks.append(bundled)
    checks += [check_disk_space(_app_dir()),
               check_writable(_app_dir(), "Working dir writable")]
    checks += [check_api_config(), check_assets(), check_models_dir(), check_telegram_config()]

    # dependency checks per requirements file
    for req_file, (_label, _optional, _hint) in REQ_FILES.items():
        deps = _read_req_names(req_file)
        if not deps:
            continue
        for dist, _raw in deps:
            checks.append(check_dependency(dist, _raw, critical=not _optional))
            for pin_dist, pin_rule, pin_note in PINNED_RULES:
                if dist == pin_dist:
                    pin = check_pin(dist, pin_rule, pin_note)
                    if pin:
                        checks.append(pin)

    critical_names = [c["name"] for c in checks if c["status"] == FAIL]
    warn_names = [c["name"] for c in checks if c["status"] == WARN]
    return checks, critical_names, warn_names


def _prompt(msg):
    try:
        return input(msg + " [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def repair(mode, checks, ensure_upload=False):
    """Apply fixes. mode: 'fix' (interactive) or 'auto-fix' (non-interactive).
    Returns (fixed_report, remaining_critical, remaining_warn)."""
    fixed = []

    # 1) cheap fixes: dirs + config
    for rel, ok, detail in ensure_dirs():
        if ok:
            fixed.append({"name": "dir " + rel, "status": OK, "detail": detail})
    ok_cfg, cfg_detail = ensure_api_config()
    fixed.append({"name": "api_config.json", "status": OK if ok_cfg else FAIL, "detail": cfg_detail})

    # 2) pip installs (core always attempted; optional stacks only on request)
    fail_names = [c["name"] for c in checks if c["status"] == FAIL]
    if ensure_upload:
        upload_dists = {dist for dist, _raw in _read_req_names("requirements-upload.txt")}
        fail_names.extend(
            c["name"] for c in checks
            if c["name"] in upload_dists and c["status"] == WARN
        )
    req_fails = {}
    for req_file, (_label, _optional, _hint) in REQ_FILES.items():
        for dist, _raw in _read_req_names(req_file):
            if dist in fail_names and dist not in req_fails:
                req_fails[dist] = req_file

    for req_file, (label, optional, hint) in REQ_FILES.items():
        missing_in_file = [d for d, rf in req_fails.items() if rf == req_file]
        if not missing_in_file:
            continue
        do_install = not optional
        if optional and mode == "fix":
            print("\n[preflight] Optional stack: %s (%s)" % (label, hint))
            do_install = _prompt("  Install %s now?" % label)
        if optional and mode == "auto-fix":
            # OAuth dependencies are small and required by the WebUI channel-linking
            # controls when requested; keep the large transcription stack opt-in.
            do_install = bool(ensure_upload and req_file == "requirements-upload.txt")
        if not do_install:
            for d in missing_in_file:
                fixed.append({"name": d, "status": WARN, "detail": "optional - skipped"})
            continue
        ok, detail = install_requirements(req_file, dry_run=False)
        fixed.append({"name": "stack " + label, "status": OK if ok else FAIL, "detail": detail})

    # 3) version pins (numpy<2, tokenizers<0.23.1, huggingface-hub<1.0)
    for dist, rule, note in PINNED_RULES:
        pin = check_pin(dist, rule, note)
        if pin:
            if dist == "numpy":
                ok, detail = fix_numpy_pin()
            elif dist == "tokenizers":
                ok, detail = fix_tokenizers_pin()
            elif dist == "huggingface-hub":
                ok, detail = fix_hub_pin()
            else:
                continue
            fixed.append({"name": dist, "status": OK if ok else FAIL, "detail": detail})

    # 4) re-check everything
    _, remaining_crit, remaining_warn = collect_checks()
    return fixed, remaining_crit, remaining_warn


def _print_report(checks, title=None):
    title = title or APP_NAME + " Pre-flight"
    print("\n=== %s ===\n" % title)
    for c in checks:
        print("%s %-28s %s" % (ICONS.get(c["status"], "  "), c["name"], c["detail"]))
    print()


def print_ffmpeg_help():
    print("""
  ffmpeg/ffprobe are REQUIRED for video processing.
  Install them, then run this check again:
    - Windows:  winget install ffmpeg        (then reopen the terminal)
                or double-click packaging\\install_ffmpeg_windows.bat
    - Linux:    sudo apt install ffmpeg      (or dnf/pacman equivalent)
    - macOS:    brew install ffmpeg
  (The packaged OUSSAMA-Cutter.exe already bundles ffmpeg - no action needed.)""")


def run_preflight(mode="auto-fix", json_out=False, quiet=False, ensure_upload=False):
    """Entry point used by the app (main_improved / webui).

    mode: 'check' (read-only) | 'fix' (interactive) | 'auto-fix' (default)
    Returns exit code: 0 ready, 1 critical remains, 2 warnings only.
    """
    checks, critical, warn = collect_checks()

    upload_missing = False
    if ensure_upload:
        upload_dists = {dist for dist, _raw in _read_req_names("requirements-upload.txt")}
        upload_missing = any(
            c["name"] in upload_dists and c["status"] in (FAIL, WARN)
            for c in checks
        )
    if (critical or upload_missing) and mode in ("fix", "auto-fix"):
        if not quiet:
            _print_report(checks, "Pre-flight: problems found, repairing")
        _, critical, warn = repair(mode, checks, ensure_upload=ensure_upload)
        checks, critical, warn = collect_checks()

    if not quiet and not json_out:
        _print_report(checks)
        if critical:
            print("❌ %d critical problem(s) remain. Fix them before running %s." % (len(critical), APP_NAME))
            if "ffmpeg" in critical or "ffprobe" in critical:
                print_ffmpeg_help()
            return 1
        if warn:
            print("✅ System ready. (%d optional warning(s) - app will still work)" % len(warn))
        else:
            print("✅ System ready. Everything in place.")
        return 0

    if json_out:
        print(json.dumps({"exit": 1 if critical else (2 if warn else 0),
                          "critical": critical, "warnings": warn,
                          "checks": checks}, indent=2, ensure_ascii=False))
    return 1 if critical else (2 if warn else 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=APP_NAME + " pre-flight check & auto-repair",
                                     epilog="Exit: 0=ready, 1=critical problems remain, 2=warnings only")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="read-only check (no installs)")
    mode.add_argument("--fix", action="store_true", help="interactive repair")
    mode.add_argument("--auto-fix", action="store_true", help="non-interactive repair")
    mode.add_argument("--off", action="store_true", help="skip everything, exit 0")
    parser.add_argument("--ensure-upload", action="store_true",
                        help="auto-install YouTube OAuth/upload dependencies")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--quiet", action="store_true", help="no console output")
    args = parser.parse_args(argv)

    if args.off:
        return 0
    # Safe default: plain `python -m scripts.preflight` is READ-ONLY.
    # Auto-install only happens on explicit --auto-fix / --fix (the run
    # scripts and the app launcher pass it explicitly).
    if args.fix:
        m = "fix"
    elif args.auto_fix:
        m = "auto-fix"
    else:
        m = "check"
    return run_preflight(mode=m, json_out=args.json, quiet=args.quiet,
                         ensure_upload=args.ensure_upload)


if __name__ == "__main__":
    sys.exit(main())
