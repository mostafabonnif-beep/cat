#!/usr/bin/env bash
# ViralCutter installer — macOS (Roadmap 1.3). Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

echo "== ViralCutter installer (macOS) =="
echo "[1/4] Homebrew check..."
if ! command -v brew >/dev/null 2>&1; then
    echo "Installing Homebrew (https://brew.sh)..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
echo "[2/4] ffmpeg + python..."
brew install ffmpeg python@3.11 || brew upgrade ffmpeg python@3.11
echo "[3/4] Virtual environment + deps..."
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
pip install -r requirements-dev.txt --quiet || true
echo "[4/4] Sanity check..."
python -c "import sys; sys.path.insert(0,'.'); import scripts.risk_scorecard; print('✅ import ok')"
python -m scripts.preflight --check || echo "⚠️  preflight found issues — fix them before running (see above)"

echo ""
echo "== Optional stacks =="
read -r -p "Install full transcription stack (whisperx + torch, needed for the complete pipeline, ~2 GB)? [y/N] " full_yn
if [[ "$full_yn" =~ ^[Yy]$ ]]; then
    pip install -r requirements-transcribe.txt
fi
read -r -p "Install direct-upload stack (YouTube OAuth uploader)? [y/N] " up_yn
if [[ "$up_yn" =~ ^[Yy]$ ]]; then
    pip install -r requirements-upload.txt
fi
echo ""
echo "✅ Done. Run:  ./run.sh"
