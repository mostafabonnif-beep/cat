#!/usr/bin/env bash
# ViralCutter installer — Linux (Roadmap 1.3). Safe to re-run (idempotent).
set -euo pipefail
cd "$(dirname "$0")"

echo "== ViralCutter installer (Linux) =="
echo "[1/4] System dependencies (ffmpeg + build tools)..."
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq ffmpeg python3 python3-pip python3-venv build-essential
elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y ffmpeg python3 python3-pip gcc gcc-c++ make
elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --noconfirm ffmpeg python python-pip base-devel
else
    echo "!! Unknown package manager — install ffmpeg + Python 3.10+ manually."
fi

echo "[2/4] Virtual environment..."
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip --quiet

echo "[3/4] Python dependencies (this can take a few minutes)..."
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
echo "   (optional CUDA: install torch with CUDA support for GPU transcription)"
