#!/usr/bin/env bash
# Build the OUSSAMA Cutter single-file executable on Linux (Roadmap 1.1).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[1/3] Installing build deps..."
pip install --quiet pyinstaller
echo "[2/3] Building (onefile, console)..."
pyinstaller packaging/viralcutter.spec --noconfirm --clean
echo "[3/3] Done → dist/OUSSAMA-Cutter"
ls -lh dist/OUSSAMA-Cutter
