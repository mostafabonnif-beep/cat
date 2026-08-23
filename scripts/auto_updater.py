# -*- coding: utf-8 -*-
"""
Auto-Update — check GitHub Releases and download the new build.

Roadmap item 1.2 ("تحديث تلقائي للبرنامج نفسه"). The word list already
updates itself (v3); now the *program* can too:

    check_for_update()    — GET /repos/{repo}/releases/latest (8s timeout)
    download_update(url)  — stream the release asset into updates/ and verify
                            its SHA-256 against the release's checksums.txt /
                            SHA256SUMS manifest (fail-closed when missing)
    update_info()         — last downloaded asset + local version

Installers (see run.bat / install_linux.sh) check updates/ on startup and
swap the new binary in. Offline / no-releases / non-GitHub errors are all
safe no-ops — the app must never fail to start because of the updater.

SECURITY: the updater replaces the running executable, so downloads are
verified against a published checksum manifest before they are kept. A
release without a manifest is refused unless the operator explicitly sets
VIRALCUTTER_ALLOW_UNSIGNED_UPDATE=1.
"""

import hashlib
import json
import os
import subprocess
import sys
import urllib.request

REPO = "mostafabonnif-beep/cat"
UPDATES_DIR = "updates"
UPDATE_INFO = "update_info.json"

try:
    from app_version import VERSION as LOCAL_VERSION
except Exception:
    LOCAL_VERSION = "0.0.0"


def _github_api(path, timeout=8):
    req = urllib.request.Request(
        "https://api.github.com" + path,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "ViralCutter-auto-update"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _latest_tag(repo, timeout=8):
    """Fallback: read the latest git tag when no formal Release exists yet."""
    tags = _github_api("/repos/{}/tags".format(repo), timeout=timeout)
    if isinstance(tags, list) and tags:
        return tags[0].get("name", "")
    return ""


def _parse_version(tag):
    """'v0.9.0' / '0.9.0' → (0, 9, 0). Unparseable → (0, 0, 0)."""
    digits = "".join(c for c in (tag or "") if c.isdigit() or c == ".")
    parts = [p for p in digits.split(".") if p != ""]
    try:
        return tuple(int(p) for p in (parts + ["0", "0", "0"])[:3])
    except ValueError:
        return (0, 0, 0)


def _newer(remote_tag):
    return _parse_version(remote_tag) > _parse_version(LOCAL_VERSION)


def check_for_update(repo=REPO, current_version=None, timeout=8, urlopen=None):
    """Check the latest release. Returns a dict (never raises).

    Result keys: update_available, latest_version, tag, download_url,
                 notes, error (when unreachable / no releases).
    """
    if current_version is not None:
        global LOCAL_VERSION
        LOCAL_VERSION = current_version
    try:
        try:
            data = _github_api("/repos/{}/releases/latest".format(repo), timeout=timeout) \
                if urlopen is None else json.loads(urlopen(timeout))
        except Exception:
            if urlopen is not None:
                # an injected urlopen failed → report the error, never hit the
                # network behind the caller's back
                raise
            # No formal release yet → fall back to the latest git tag so the
            # update loop still works once maintainers push version tags.
            tag = _latest_tag(repo, timeout=timeout)
            return {"update_available": _newer(tag), "latest_version": tag or None,
                    "download_url": None, "notes": None, "error": None}
        tag = data.get("tag_name", "")
        assets = data.get("assets", [])
        download_url = None
        # pick the best asset for this platform
        os_name = os.name
        for asset in assets:
            name = asset.get("name", "").lower()
            if os_name == "nt" and name.endswith(".exe"):
                download_url = asset.get("browser_download_url")
                break
            if os_name != "nt" and (name.endswith(".bin") or name.endswith(".appimage")
                                    or name.endswith("-linux") or name.endswith("-macos")):
                download_url = asset.get("browser_download_url")
                break
        if not download_url and assets:
            # SECURITY: never fall back to a random asset — only accept an
            # asset that matches this platform.
            download_url = None
        available = _newer(tag) if tag else False
        return {
            "update_available": available,
            "latest_version": tag,
            "download_url": download_url,
            "notes": (data.get("body") or "")[:400],
            "error": None,
        }
    except Exception as e:
        return {"update_available": False, "latest_version": None,
                "download_url": None, "notes": None, "error": str(e)}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _tag_from_url(download_url):
    """Extract the release tag from a github.com/.../releases/download/ URL."""
    marker = "/releases/download/"
    if marker in (download_url or ""):
        return download_url.split(marker, 1)[1].split("/", 1)[0]
    return ""


def _fetch_checksums(repo, tag):
    """Fetch a release checksum manifest (checksums.txt / SHA256SUMS).

    Returns {filename: sha256_hex} or {} when no manifest exists for the tag.
    """
    if not repo or not tag:
        return {}
    for fname in ("checksums.txt", "SHA256SUMS"):
        url = "https://github.com/{}/releases/download/{}/{}".format(repo, tag, fname)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ViralCutter-auto-update"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                text = resp.read().decode("utf-8", "replace")
        except Exception:
            continue
        out = {}
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 2 and len(parts[0]) == 64:
                out[parts[1].lstrip("*")] = parts[0].lower()
        if out:
            return out
    return {}


def download_update(download_url, dest_dir=None, expected_sha256=None, repo=None, tag=None):
    """Stream the release asset to updates/ and verify its SHA-256.

    Returns the local path.

    Integrity policy (fail-closed): the binary is only kept when its SHA-256
    matches a published checksums.txt / SHA256SUMS manifest for the same
    release. Without a manifest the update is REFUSED unless the operator
    explicitly opts in with VIRALCUTTER_ALLOW_UNSIGNED_UPDATE=1 (not
    recommended — the updater replaces the running executable).
    """
    dest_dir = dest_dir or UPDATES_DIR
    os.makedirs(dest_dir, exist_ok=True)
    name = os.path.basename(download_url.split("?")[0]) or "viralcutter_update.bin"
    dest = os.path.join(dest_dir, name)
    tmp = dest + ".part"
    req = urllib.request.Request(download_url, headers={"User-Agent": "ViralCutter-auto-update"})
    with urllib.request.urlopen(req, timeout=600) as resp, open(tmp, "wb") as f:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)

    # --- integrity check -------------------------------------------------
    if not tag:
        tag = _tag_from_url(download_url)
    if not expected_sha256:
        checksums = _fetch_checksums(repo or REPO, tag)
        expected_sha256 = checksums.get(name)
        if not expected_sha256:
            # tolerate dash/underscore naming drift between manifest and asset
            expected_sha256 = checksums.get(name.replace("-", "_"))
    if expected_sha256:
        actual = _sha256(tmp)
        if actual != expected_sha256.lower():
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise RuntimeError(
                "checksum mismatch for {}: expected {} got {}. Update refused."
                .format(name, expected_sha256, actual))
        print("update integrity OK (sha256 {})".format(actual[:12]))
    elif os.environ.get("VIRALCUTTER_ALLOW_UNSIGNED_UPDATE", "").strip().lower() in ("1", "true", "yes", "on"):
        print("WARNING: no checksum manifest for release {} — accepting "
              "unsigned update (VIRALCUTTER_ALLOW_UNSIGNED_UPDATE=1)"
              .format(tag or "?"))
    else:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise RuntimeError(
            "no checksum manifest for release {}; refusing to install an "
            "unverified binary. Publish a checksums.txt asset for the release "
            "or set VIRALCUTTER_ALLOW_UNSIGNED_UPDATE=1 to accept it anyway."
            .format(tag or "?"))

    os.replace(tmp, dest)
    info = {"downloaded": dest, "asset": name,
            "local_version": LOCAL_VERSION, "tag": tag}
    try:
        with open(os.path.join(dest_dir, UPDATE_INFO), "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return dest


def update_info(dest_dir=None):
    """(local_version, downloaded_asset) from updates/update_info.json."""
    path = os.path.join(dest_dir or UPDATES_DIR, UPDATE_INFO)
    if not os.path.exists(path):
        return LOCAL_VERSION, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("local_version", LOCAL_VERSION), data.get("asset")
    except Exception:
        return LOCAL_VERSION, None


def apply_pending_update(dest_dir=None, restart=False):
    """Move a downloaded update into place (best-effort, platform-aware).

    On Windows the old .exe may be locked → the installer script (run.bat)
    performs the swap on next start. Returns the new path or None.
    """
    dest_dir = dest_dir or UPDATES_DIR
    path = os.path.join(dest_dir, UPDATE_INFO)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    src = data.get("downloaded")
    if not src or not os.path.exists(src):
        return None
    if getattr(sys, "frozen", False):  # running as a PyInstaller binary
        target = sys.executable
        try:
            os.replace(src, target)
            if restart:
                subprocess.Popen([target])
                sys.exit(0)
            return target
        except Exception:
            return None
    return src  # source installs: print a hint to re-pull the repo


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ViralCutter auto-update.")
    parser.add_argument("--check", action="store_true", help="check for updates")
    parser.add_argument("--download", action="store_true",
                        help="download the latest release asset")
    parser.add_argument("--apply", action="store_true",
                        help="apply a downloaded update (best-effort)")
    args = parser.parse_args()

    if args.check:
        info = check_for_update()
        if info["error"]:
            print("update check failed: {}".format(info["error"]))
            return 0
        if info["update_available"]:
            print("UPDATE AVAILABLE: {} (local: {})".format(info["latest_version"], LOCAL_VERSION))
            print("download: {}".format(info["download_url"]))
        else:
            print("up to date (local: {})".format(LOCAL_VERSION))
        return 0
    if args.download:
        info = check_for_update()
        if not info.get("download_url"):
            print("no downloadable asset found")
            return 1
        path = download_update(info["download_url"], repo=REPO,
                               tag=info.get("latest_version"))
        print("downloaded to {}".format(path))
        return 0
    if args.apply:
        path = apply_pending_update()
        print("applied: {}".format(path or "nothing pending"))
        return 0
    print("local version: {}".format(LOCAL_VERSION))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
