#!/usr/bin/env bash
# ViralCutter launcher — Linux/macOS (Roadmap 1.3).
# 1) applies a pending auto-update binary if present,
# 2) runs the pre-flight check (verifies + auto-installs anything missing),
# 3) activates the venv (or tells you to run install_linux.sh / install_macos.sh),
# 4) starts the CLI (pass --webui to launch the web interface).
set -euo pipefail
cd "$(dirname "$0")"

if [ -f updates/update_info.json ]; then
    echo "[auto-update] pending update found — see updates/"
fi

if [ ! -d .venv ]; then
    echo "No .venv found. Run ./install_linux.sh or ./install_macos.sh first."
    exit 1
fi
. .venv/bin/activate

# Pre-flight: verify EVERYTHING (deps, ffmpeg, config, assets) and auto-install
# whatever is missing, so the app starts with everything in place.
# Use --off or VIRALCUTTER_SKIP_PREFLIGHT=1 to skip.
if [ "${VIRALCUTTER_SKIP_PREFLIGHT:-0}" != "1" ]; then
    echo "[preflight] Checking environment and installing anything missing..."
    if ! python -m scripts.preflight --auto-fix; then
        echo "[preflight] Critical problems found. Fix the items above, then run again."
        exit 1
    fi
fi

if [ "${1:-}" = "--webui" ]; then
    shift
    exec python webui/app.py "$@"
fi
exec python main_improved.py "$@"
