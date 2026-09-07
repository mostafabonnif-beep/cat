# -*- coding: utf-8 -*-
"""Verified ffmpeg/ffprobe resolution (v7.32.2).

Why this exists — yt-dlp ≥ 2025 does not trust PATH lookup alone: before any
merge it *executes* ``ffmpeg -bsfs`` and requires parseable version output
(``FFmpegPostProcessor._get_ffmpeg_version``). A broken binary on PATH — the
classic Windows case is a lone ``ffmpeg.exe`` copied into
``C:\\Windows\\system32`` without its DLLs, which also shadows a healthy
install in ``C:\\ffmpeg\\bin`` — passes a plain ``shutil.which`` check but
makes every merged download die with::

    ERROR: You have requested merging of multiple formats but ffmpeg is not
    installed. Aborting due to --abort-on-error

while the pre-flight (existence check) said ffmpeg was fine.

This module closes the gap the same way yt-dlp does: candidates are verified
by actually running them, and when the PATH copy is broken we rescue the
working binary from a sibling directory (a full ffmpeg install ships ffmpeg
and ffprobe side by side) or from ``VIRALCUTTER_FFMPEG_DIR``.
"""

import os
import shutil
import subprocess

_VERIFY_TIMEOUT = 20  # seconds; ffmpeg -version is instant, broken DLL stubs can hang

FFMPEG_BROKEN_GUIDANCE = (
    "The ffmpeg/ffprobe copy on PATH does not run (corrupt binary or missing "
    "DLLs — often a lone ffmpeg.exe copied to C:\\Windows\\system32).\n"
    "Fixes:\n"
    "  1) Delete/rename the broken copy, then reopen the terminal, or\n"
    "  2) Reinstall the FULL ffmpeg package: winget install ffmpeg\n"
    "     (bin folder must contain ffmpeg.exe AND its DLLs), or\n"
    "  3) Point the app at a good install: set VIRALCUTTER_FFMPEG_DIR=C:\\ffmpeg\\bin"
)


def _exe(name):
    return name + (".exe" if os.name == "nt" else "")


def binary_runs(path, timeout=_VERIFY_TIMEOUT, runner=None):
    """True when *path* actually executes and prints a version banner.

    Mirrors what yt-dlp requires (it runs the binary and parses the version),
    which is strictly stronger than ``os.path.isfile`` / ``shutil.which``.
    """
    if runner is None:
        runner = subprocess.run
    try:
        proc = runner(
            [path, "-hide_banner", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if getattr(proc, "returncode", 1) != 0:
        return False
    out = getattr(proc, "stdout", b"") or b""
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    head = out[:400].lower()
    name = os.path.basename(str(path)).lower().split(".")[0]
    return "version" in head and name in head


def _candidate_dirs(which_fn, env=os.environ):
    """Directories worth probing for a working binary, in priority order."""
    dirs = []
    for other in ("ffmpeg", "ffprobe"):
        other_path = which_fn(other)
        if other_path:
            dirs.append(os.path.dirname(other_path))
    env_dir = env.get("VIRALCUTTER_FFMPEG_DIR", "").strip()
    if env_dir:
        dirs.append(env_dir)
    seen, ordered = set(), []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            ordered.append(d)
    return ordered


def resolve_toolchain(which_fn=None, isfile=None, runs=None, env=None):
    """Resolve ffmpeg+ffprobe, verifying they run. Returns a dict:

    ``programs[name]`` — {"path", "state"} where state is one of:
        ok      found on PATH and executes correctly
        rescued PATH copy broken/missing; working copy found in a sibling dir
        broken  found on PATH but does not execute (corrupt / missing DLLs)
        missing not found on PATH
    ``location`` — directory to hand to yt-dlp as ``ffmpeg_location`` (None
        when PATH already works and yt-dlp should use its default lookup)
    ``ok``       — True when both programs are usable (ok or rescued)
    ``problems`` — human-readable issues, empty when ok
    """
    if which_fn is None:
        which_fn = shutil.which
    if isfile is None:
        isfile = os.path.isfile
    if runs is None:
        runs = binary_runs
    if env is None:
        env = os.environ
    programs = {}
    for name in ("ffmpeg", "ffprobe"):
        path = which_fn(name)
        if path and runs(path):
            programs[name] = {"path": path, "state": "ok"}
        elif path:
            programs[name] = {"path": path, "state": "broken"}
        else:
            programs[name] = {"path": None, "state": "missing"}

    location = None
    cand_dirs = _candidate_dirs(which_fn, env)
    for name in ("ffmpeg", "ffprobe"):
        if programs[name]["state"] == "ok":
            continue
        for d in cand_dirs:
            cand = os.path.join(d, _exe(name))
            if isfile(cand) and runs(cand):
                programs[name] = {"path": cand, "state": "rescued"}
                location = d
                break

    problems = []
    for name in ("ffmpeg", "ffprobe"):
        info = programs[name]
        if info["state"] == "broken":
            problems.append("%s found at %s but it does not run (corrupt or missing DLLs)"
                            % (name, info["path"]))
        elif info["state"] == "missing":
            problems.append("%s not found on PATH" % name)

    ok = all(p["state"] in ("ok", "rescued") for p in programs.values())
    return {"programs": programs, "location": location, "ok": ok, "problems": problems}
