# -*- coding: utf-8 -*-
"""
Music fingerprinting — Roadmap item 2.3 (بصمة الموسيقى Chromaprint).

Protects against audio copyright claims before you publish a clip:

  * Local fingerprinting with Chromaprint via `pyacoustid` or the `fpcalc`
    CLI (Windows: drop fpcalc.exe next to the app / on PATH).
  * Identification against the public AcoustID database (free API key,
    override with ACOUSTID_API_KEY; a public default key is bundled).
  * Optional LOCAL database matching: point the tool at a folder of songs
    you are licensed to use (or must avoid); any clip that borrows more
    than a threshold of their audio is flagged — no network needed.

Pipeline integration
--------------------
  * `analyze_project()` fingerprints every clip in `final/`, writes
    `music_fingerprint.json`, and returns a report.
  * The upload gate consults that report: with `gate="warn"` (default) a
    matching clip is flagged but can still be uploaded; with `gate="block"`
    the clip is REFUSED before it ever reaches a platform.

Everything degrades gracefully when Chromaprint is missing: the module
never raises on import, and `analyze_project()` reports `no_fpcalc` per
clip instead of crashing the pipeline.
"""

import base64
import json
import os
import shutil
import struct
import subprocess
import sys
import urllib.parse
import urllib.request

REPORT_NAME = "music_fingerprint.json"

# Chromaprint release used by --install-fpcalc (auto-download). Kept on a
# pinned release so builds are reproducible; the API fallback picks the
# latest release automatically when this asset name pattern changes.
FPCalc_RELEASE = "v1.5.1"
FPCalc_GH_API = "https://api.github.com/repos/acoustid/chromaprint/releases/latest"

# Extra folder we look for fpcalc in (also where --install-fpcalc puts it).
USER_BIN_DIR = os.path.join(os.path.expanduser("~"), ".viralcutter", "bin")

# Public default client key used by open-source Chromaprint tools.
# Override with the ACOUSTID_API_KEY env var.
DEFAULT_ACOUSTID_KEY = "8XaBELgH"
ACOUSTID_LOOKUP_URL = "https://api.acoustid.org/v2/lookup"

# Fraction of n-gram overlap (0..1) before a local match counts as "matched".
LOCAL_MATCH_THRESHOLD = 0.10


def get_acoustid_key():
    return os.getenv("ACOUSTID_API_KEY") or DEFAULT_ACOUSTID_KEY


class FpcalcUnavailable(RuntimeError):
    """Chromaprint (fpcalc / pyacoustid) is not available on this machine."""


def _import_acoustid():
    """Best-effort import of pyacoustid; never raises."""
    try:
        import acoustid  # noqa: F401
        return acoustid
    except Exception:
        return None


def _fpcalc_candidates():
    """Every plausible location of the fpcalc CLI, most specific first.

    Order matters — later entries are fallbacks:
      1. inside a PyInstaller onefile bundle (sys._MEIPASS),
      2. next to the running exe (frozen app; where the CI build bundles it),
      3. ~/.viralcutter/bin (where --install-fpcalc drops it),
      4. anywhere on PATH.
    """
    cands = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        cands += [os.path.join(bundle, "fpcalc.exe"), os.path.join(bundle, "fpcalc")]
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    cands += [os.path.join(exe_dir, "fpcalc.exe"), os.path.join(exe_dir, "fpcalc")]
    cands += [os.path.join(USER_BIN_DIR, "fpcalc.exe"), os.path.join(USER_BIN_DIR, "fpcalc")]
    seen = []
    for c in cands:
        if c and os.path.isfile(c) and c not in seen:
            seen.append(c)
    on_path = shutil.which("fpcalc")
    if on_path and on_path not in seen:
        seen.append(on_path)
    return seen


def fpcalc_available():
    """True when we can fingerprint locally (pyacoustid lib or fpcalc CLI)."""
    if _import_acoustid() is not None:
        return True
    return len(_fpcalc_candidates()) > 0


def fingerprint_file(video_path, timeout=600):
    """Return {"fingerprint", "duration", "engine", "format"} for a media file.

    format is "compressed" (pyacoustid, AcoustID-ready) or "raw" (fpcalc ints).
    Raises FpcalcUnavailable when no backend exists; RuntimeError when a
    backend exists but fails on this file.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError("media not found: {}".format(video_path))

    acoustid = _import_acoustid()
    if acoustid is not None:
        try:
            duration, fingerprint = acoustid.fingerprint_file(
                str(video_path), timeout=timeout)
            return {"fingerprint": fingerprint, "duration": float(duration),
                    "engine": "pyacoustid", "format": "compressed"}
        except Exception:
            pass  # fall through to the fpcalc CLI

    fpcalc = _fpcalc_candidates()
    if not fpcalc:
        raise FpcalcUnavailable(
            "Chromaprint not found. Fix it with one command:\n"
            "  • python -m scripts.music_fingerprint --install-fpcalc\n"
            "    (auto-downloads fpcalc.exe next to the app — bundled in the "
            "official Windows build, so packaged users never see this), OR\n"
            "  • pip install pyacoustid  (needs the native chromaprint lib), OR\n"
            "  • Linux: sudo apt-get install libchromaprint-tools")

    errors = []
    for candidate in fpcalc:
        cmd = [candidate, "-raw", str(video_path)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except Exception as e:
            errors.append("{}: {}".format(candidate, e))
            continue
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[:300]
            errors.append("{} failed ({}): {}".format(fpcalc, proc.returncode, detail))
            continue
        duration = 0.0
        ints = []
        for line in (proc.stdout or "").splitlines():
            if line.startswith("DURATION="):
                try:
                    duration = float(line.split("=", 1)[1])
                except ValueError:
                    pass
            elif line.startswith("FINGERPRINT="):
                raw = line.split("=", 1)[1].strip()
                if raw and raw != "0":
                    ints = [int(x) for x in raw.split(",") if x.strip()]
        if not ints:
            errors.append(
                "{} produced no fingerprint — is the file a valid video with "
                "audio?".format(fpcalc))
            continue
        return {"fingerprint": ints, "duration": duration, "engine": "fpcalc",
                "format": "raw"}
    if errors:
        raise RuntimeError("fpcalc failed on every candidate:\n  " +
                           "\n  ".join(errors[:5]))
    raise RuntimeError("no fpcalc found")


def decode_fingerprint(fingerprint, fmt="compressed"):
    """Return the raw list of 32-bit sub-fingerprints (ints).

    Works for both formats; returns [] when decoding is impossible.
    """
    if fmt == "raw" or isinstance(fingerprint, (list, tuple)):
        return [int(x) for x in fingerprint]
    acoustid = _import_acoustid()
    if acoustid is not None:
        try:
            from acoustid.chromaprint import decode_fingerprint as _decode
            return list(_decode(fingerprint))
        except Exception:
            pass
    return []


def compress_fingerprint(ints):
    """Encode raw 32-bit sub-fingerprints into AcoustID's compressed format.

    `fpcalc -raw` prints signed 32-bit integers; AcoustID's web API takes the
    base64 of those values serialized little-endian (exactly what pyacoustid's
    chromaprint encoder produces). Encoding here means a plain fpcalc install
    can query AcoustID with ZERO extra Python deps.

    Returns the base64 str, or None when the input cannot be encoded.
    """
    try:
        vals = [int(x) & 0xFFFFFFFF for x in ints]
        buf = struct.pack("<{}I".format(len(vals)), *vals)
    except (ValueError, TypeError, struct.error):
        return None
    if not buf:
        return None
    return base64.b64encode(buf).decode("ascii")


# ---------------------------------------------------------------------------
# AcoustID identification (network)
# ---------------------------------------------------------------------------

def identify_acoustid(fingerprint, duration, api_key=None, timeout=60):
    """Query the AcoustID lookup API.

    Accepts BOTH fingerprint formats: the compressed string from pyacoustid,
    or raw ints from `fpcalc -raw` (encoded on the fly — no pyacoustid
    required). Returns a list of {"artist", "title", "score", "id",
    "sources"} sorted by score, or [] when nothing matched / the lookup
    cannot run.
    """
    if isinstance(fingerprint, (list, tuple)):
        fingerprint = compress_fingerprint(fingerprint)
        if not fingerprint:
            return []  # unencodable input — nothing we can send
    if not fingerprint:
        return []
    params = {
        "client": api_key or get_acoustid_key(),
        "fingerprint": fingerprint,
        "duration": int(round(float(duration or 0))),
        "meta": "recordings+releasegroups+sources",
    }
    url = "{}?{}".format(ACOUSTID_LOOKUP_URL, urllib.parse.urlencode(params))
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return []
    if payload.get("status") != "ok":
        return []
    results = []
    for item in payload.get("results", []):
        for recording in item.get("recordings", []):
            artists = ", ".join(
                a.get("name", "") for a in recording.get("artists", [])
                if a.get("name"))
            results.append({
                "id": recording.get("id", ""),
                "artist": artists,
                "title": recording.get("title", ""),
                "score": item.get("score", 0.0),
                "sources": recording.get("sources", 0),
            })
    results.sort(key=lambda r: (r["score"], r["sources"]), reverse=True)
    return results


# ---------------------------------------------------------------------------
# Local database matching (offline)
# ---------------------------------------------------------------------------

def _atomic_write(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def build_local_db(music_dir, cache_path=None):
    """Fingerprint every audio/video file under `music_dir`.

    Returns a database dict: {"songs": [{"path", "title", "duration",
    "fingerprint", "format"}]}. Unreadable files are skipped (never crash).
    """
    if not os.path.isdir(music_dir):
        raise NotADirectoryError("local music dir not found: {}".format(music_dir))
    supported = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus",
                 ".mp4", ".mkv", ".webm", ".mov")
    songs = []
    for root, _dirs, files in os.walk(music_dir):
        for name in sorted(files):
            if not name.lower().endswith(supported):
                continue
            path = os.path.join(root, name)
            try:
                fp = fingerprint_file(path)
            except Exception:
                continue
            songs.append({
                "path": path,
                "title": os.path.splitext(name)[0],
                "duration": fp["duration"],
                "fingerprint": fp["fingerprint"],
                "format": fp["format"],
            })
    db = {"songs": songs}
    if cache_path:
        _atomic_write(cache_path, db)
    return db


def load_local_db(db_path):
    if not os.path.exists(db_path):
        return {"songs": []}
    try:
        with open(db_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"songs": []}


def _grams(ints, window=8):
    """Sliding-window n-grams of raw sub-fingerprints."""
    if len(ints) < window:
        return {tuple(ints)}
    return {tuple(ints[i:i + window]) for i in range(len(ints) - window + 1)}


def match_local(db, fingerprint, fmt="compressed", threshold=None):
    """Compare a clip fingerprint against a local DB.

    score = fraction of the clip's n-grams that appear in the best song.
    Returns {"matched", "song", "score", "threshold", "error"}.
    """
    threshold = LOCAL_MATCH_THRESHOLD if threshold is None else threshold
    clip_ints = decode_fingerprint(fingerprint, fmt)
    if not clip_ints:
        return {"matched": False, "song": None, "score": 0.0,
                "threshold": threshold, "error": "decode_failed"}
    clip_grams = _grams(clip_ints)
    if not clip_grams:
        return {"matched": False, "song": None, "score": 0.0,
                "threshold": threshold, "error": "empty_fingerprint"}

    best = {"score": 0.0, "song": None}
    for song in (db or {}).get("songs", []):
        song_ints = decode_fingerprint(song.get("fingerprint"),
                                       song.get("format", "compressed"))
        if not song_ints:
            continue
        song_grams = _grams(song_ints)
        if not song_grams:
            continue
        overlap = len(clip_grams & song_grams)
        if overlap == 0:
            continue
        score = overlap / float(len(clip_grams))
        if score > best["score"]:
            best = {"score": score, "song": song["title"]}

    return {
        "matched": best["score"] >= threshold and best["song"] is not None,
        "song": best["song"],
        "score": round(best["score"], 4),
        "threshold": threshold,
    }


# ---------------------------------------------------------------------------
# Project analysis
# ---------------------------------------------------------------------------

def _find_clips(project_folder):
    import glob
    final_dir = os.path.join(project_folder, "final")
    hits = sorted(glob.glob(os.path.join(final_dir, "*.mp4")))
    if hits:
        return hits
    return sorted(glob.glob(os.path.join(project_folder, "cuts", "*.mp4")))


def _index_from_path(path):
    import re
    m = re.match(r"(\d+)", os.path.basename(path))
    return int(m.group(1)) if m else None


def analyze_project(project_folder, acoustid_key=None, local_db=None,
                    gate="warn", threshold=None, do_acoustid=True,
                    do_local=True):
    """Fingerprint every clip and write `music_fingerprint.json`.

    `local_db` may be a db dict (from build_local_db) or a path to a cached
    JSON db. Returns the report dict.
    """
    if isinstance(local_db, str) or local_db is None:
        local_db = load_local_db(local_db) if local_db else {"songs": []}

    # What backend are we running on? Reported honestly so users know how much
    # of the check is real vs skipped.
    acoustid_lib = _import_acoustid()
    if acoustid_lib is not None:
        backend = "pyacoustid"
    elif _fpcalc_candidates():
        backend = "fpcalc"
    else:
        backend = "none"
    coverage_note = (
        "AcoustID's database covers mostly commercially released music. "
        "Arabic / regional / unreleased tracks are often MISSING — for those, "
        "build a local reference DB of the songs you care about with "
        "'python -m scripts.music_fingerprint --build-local-db <folder>'."
    )

    report = {
        "gate": gate,
        "threshold": threshold or LOCAL_MATCH_THRESHOLD,
        "acoustid_key_configured": bool(os.getenv("ACOUSTID_API_KEY")),
        "backend": backend,
        "coverage_note": coverage_note,
        "clips": [],
        "summary": {"checked": 0, "matched": 0, "warned": 0,
                    "no_fpcalc": 0, "errors": 0},
    }
    for clip in _find_clips(project_folder):
        entry = {"index": _index_from_path(clip), "video": clip,
                 "verdict": "clean", "suggestion": None}
        try:
            fp = fingerprint_file(clip)
        except FpcalcUnavailable as e:
            entry["verdict"] = "no_fpcalc"
            entry["suggestion"] = str(e).splitlines()[0]
            report["summary"]["no_fpcalc"] += 1
            report["clips"].append(entry)
            continue
        except Exception as e:
            entry["verdict"] = "error"
            entry["suggestion"] = str(e)[:200]
            report["summary"]["errors"] += 1
            report["clips"].append(entry)
            continue

        entry["duration"] = round(fp["duration"], 2)
        entry["engine"] = fp["engine"]
        report["summary"]["checked"] += 1

        # Local reference matching (offline, both engines work).
        if do_local and local_db.get("songs"):
            local = match_local(local_db, fp["fingerprint"], fp["format"],
                                threshold=threshold)
            entry["local_match"] = local
            if local["matched"]:
                entry["verdict"] = "local_match"
                entry["suggestion"] = ("Audio overlaps local reference "
                                       "'{}' ({:.0%})".format(
                                           local["song"], local["score"]))

        # AcoustID lookup — works with BOTH engines now: pyacoustid's
        # compressed string, or raw fpcalc ints (encoded in identify_acoustid).
        acoustid_matches = []
        if do_acoustid:
            acoustid_matches = identify_acoustid(fp["fingerprint"],
                                                 fp["duration"],
                                                 api_key=acoustid_key)
            entry["acoustid"] = acoustid_matches[:3]
            if acoustid_matches and acoustid_matches[0]["score"] >= 0.5:
                top = acoustid_matches[0]
                if entry["verdict"] == "clean":
                    entry["verdict"] = "acoustid_match"
                entry["suggestion"] = (
                    "AcoustID: '{}' by {} (score {:.0%}, {} sources)".format(
                        top["title"], top["artist"], top["score"],
                        top.get("sources", 0)))

        if entry["verdict"] == "acoustid_match":
            report["summary"]["matched"] += 1
        elif entry["verdict"] == "local_match":
            report["summary"]["matched"] += 1
            report["summary"]["warned"] += 1
        report["clips"].append(entry)

    _atomic_write(os.path.join(project_folder, REPORT_NAME), report)
    return report


# ---------------------------------------------------------------------------
# Upload-gate integration
# ---------------------------------------------------------------------------

def music_gate_reasons(project_folder, index=None, gate=None):
    """Reasons a clip should not be published, from the music report.

    gate: "off" → no reasons; "warn" → medium-severity flag; "block" → high.
    Returns [] when there is no report or no match (safe default).
    """
    if gate == "off":
        return []
    path = os.path.join(project_folder, REPORT_NAME)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            report = json.load(f)
    except Exception:
        return []
    if gate is None:
        gate = report.get("gate", "warn")
    reasons = []
    for entry in report.get("clips", []):
        if index is not None and entry.get("index") != index:
            continue
        if entry.get("verdict") in ("acoustid_match", "local_match"):
            detail = entry.get("suggestion") or "audio fingerprint matched"
            reasons.append({
                "source": "music_fingerprint",
                "detail": "clip #{} — {}".format(entry.get("index", "?"), detail),
                "severity": "high" if gate == "block" else "medium",
            })
    return reasons


# ---------------------------------------------------------------------------
# fpcalc auto-install (--install-fpcalc)
# ---------------------------------------------------------------------------

def install_fpcalc(target_dir=None, timeout=300):
    """Download and extract the Chromaprint fpcalc binary for this platform.

    Windows → fpcalc.exe into `target_dir` (default: next to the app/exe, so
    a frozen build finds it automatically); Linux/macOS → into
    ~/.viralcutter/bin and prints the PATH hint. Uses the pinned
    FPCalc_RELEASE asset; falls back to the latest release via the GitHub API.

    Returns the installed fpcalc path. Raises RuntimeError with a readable
    message when the download/extraction fails.
    """
    import platform as _platform
    import tarfile
    import tempfile
    import zipfile

    system = (_platform.system() or "").lower()
    machine = (_platform.machine() or "").lower()
    if machine in ("amd64", "x86_64"):
        arch = "x86_64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"  # best effort — not every release ships it
    else:
        arch = "i686"

    if system.startswith("win"):
        asset = "chromaprint-fpcalc-{}-windows-{}.zip".format(
            FPCalc_RELEASE.lstrip("v"), arch)
        exe_name = "fpcalc.exe"
    elif system == "darwin":
        asset = "chromaprint-fpcalc-{}-macos-{}.tar.gz".format(
            FPCalc_RELEASE.lstrip("v"), arch)
        exe_name = "fpcalc"
    else:  # linux & friends
        asset = "chromaprint-fpcalc-{}-linux-{}.tar.gz".format(
            FPCalc_RELEASE.lstrip("v"), arch)
        exe_name = "fpcalc"

    # The packaged build of ViralCutter bundles fpcalc next to the exe, so a
    # frozen app defaults to the exe dir. Source installs → ~/.viralcutter/bin.
    if target_dir is None:
        if getattr(sys, "frozen", False):
            target_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            target_dir = USER_BIN_DIR
    os.makedirs(target_dir, exist_ok=True)

    download_urls = [
        "https://github.com/acoustid/chromaprint/releases/download/{}/{}".format(
            FPCalc_RELEASE, asset),
    ]
    req = urllib.request.Request(
        FPCalc_GH_API, headers={"Accept": "application/vnd.github+json",
                                "User-Agent": "ViralCutter"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            release = json.loads(resp.read().decode("utf-8", errors="replace"))
        for a in release.get("assets", []):
            if a.get("name") == asset:
                download_urls.append(a.get("browser_download_url"))
    except Exception:
        pass  # pinned URL is the primary source; API is just a fallback

    last_err = None
    for url in dict.fromkeys(filter(None, download_urls)):
        try:
            with tempfile.TemporaryDirectory() as td:
                archive = os.path.join(td, os.path.basename(url))
                dl = urllib.request.Request(url, headers={"User-Agent": "ViralCutter"})
                with urllib.request.urlopen(dl, timeout=timeout) as resp:
                    with open(archive, "wb") as f:
                        shutil.copyfileobj(resp, f)
                if archive.endswith(".zip"):
                    with zipfile.ZipFile(archive) as z:
                        members = [m for m in z.namelist()
                                   if m.endswith(exe_name) and "/" not in m]
                        z.extract(members[0], td) if members else z.extractall(td)
                        extracted = os.path.join(td, members[0] if members else "")
                else:
                    with tarfile.open(archive, "r:gz") as t:
                        members = [m for m in t.getmembers()
                                   if m.name.endswith("/" + exe_name)]
                        if members:
                            t.extract(members[0], td)
                            extracted = os.path.join(td, members[0].name)
                        else:
                            extracted = ""
                if not extracted or not os.path.isfile(extracted):
                    raise RuntimeError("no {} inside {}".format(exe_name, archive))
                dest = os.path.join(target_dir, exe_name)
                shutil.copy2(extracted, dest)
                if system != "win32":
                    os.chmod(dest, 0o755)
                print("fpcalc installed → {}".format(dest))
                if target_dir == USER_BIN_DIR:
                    print("add it to PATH: export PATH=\"{}:$PATH\"".format(target_dir))
                return dest
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(
        "fpcalc download failed for {} ({}). Download it manually from "
        "https://github.com/acoustid/chromaprint/releases and place {} next "
        "to the app or on PATH. Last error: {}".format(
            asset, system, exe_name, last_err))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(
        description="ViralCutter music fingerprint check (Chromaprint/AcoustID).")
    parser.add_argument("--project", help="Project folder to analyze")
    parser.add_argument("--acoustid-key", default=None, help="AcoustID API key (or ACOUSTID_API_KEY env)")
    parser.add_argument("--local-db", default=None,
                        help="JSON db from --build-local-db, or a folder of reference songs")
    parser.add_argument("--build-local-db", default=None,
                        help="Fingerprint a folder of songs into a JSON cache (no project needed)")
    parser.add_argument("--db-cache", default=None, help="Cache path for --build-local-db")
    parser.add_argument("--install-fpcalc", action="store_true",
                        help="Download Chromaprint's fpcalc binary for this platform (no project needed)")
    parser.add_argument("--gate", choices=["warn", "block", "off"], default="warn")
    parser.add_argument("--threshold", type=float, default=None)
    args = parser.parse_args(argv)

    if args.install_fpcalc:
        install_fpcalc()
        return 0

    if args.build_local_db:
        cache = args.db_cache or os.path.join(
            os.path.expanduser("~"), ".viralcutter", "music_db.json")
        db = build_local_db(args.build_local_db, cache_path=cache)
        print("local DB: {} songs → {}".format(len(db["songs"]), cache))
        return 0

    if not args.project:
        parser.error("--project is required (or use --build-local-db)")

    local_db = None
    if args.local_db:
        if os.path.isdir(args.local_db):
            cache = args.db_cache or os.path.join(
                os.path.expanduser("~"), ".viralcutter", "music_db.json")
            local_db = build_local_db(args.local_db, cache_path=cache)
            print("local DB built: {} songs".format(len(local_db["songs"])))
        else:
            local_db = load_local_db(args.local_db)

    report = analyze_project(args.project, acoustid_key=args.acoustid_key,
                             local_db=local_db, gate=args.gate,
                             threshold=args.threshold)
    s = report["summary"]
    print("music check: backend={} | {} clips, {} matched, {} no_fpcalc, {} errors".format(
        report.get("backend", "?"), s["checked"], s["matched"],
        s["no_fpcalc"], s["errors"]))
    if s["no_fpcalc"]:
        print("⚠️ Chromaprint (fpcalc) is missing — the check did NOT run for "
              "{} clip(s). Install it with: python -m scripts.music_fingerprint "
              "--install-fpcalc".format(s["no_fpcalc"]))
    if report.get("coverage_note"):
        print("ℹ️ " + report["coverage_note"])
    for clip in report["clips"]:
        verdict = clip["verdict"]
        mark = {"clean": "✅", "acoustid_match": "🎵⚠️", "local_match": "🎵⚠️",
                "no_fpcalc": "⚠️", "error": "❌"}.get(verdict, "?")
        print("  {} #{} {} — {}".format(mark, clip.get("index", "?"),
                                        os.path.basename(clip["video"]), verdict))
        if clip.get("suggestion"):
            print("      ↳ {}".format(clip["suggestion"]))
    print("report → {}".format(os.path.join(args.project, REPORT_NAME)))
    return 3 if s["matched"] and args.gate == "block" else 0


if __name__ == "__main__":
    sys.exit(main())
