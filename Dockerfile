# OUSSAMA Cutter — production container (v7.20)
#
# One-command setup: docker compose up --build
# Runs the Gradio WebUI on http://localhost:7860 with everything bundled
# (Python 3.11, ffmpeg, yt-dlp, whisperx CPU path, OpenCV, Gradio).
#
# NOTE: InsightFace is intentionally NOT installed here (heavy native deps);
# the app falls back to MediaPipe → OpenCV Haar automatically, and the
# tracking report records which backend actually ran.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRALCUTTER_SKIP_PREFLIGHT=1 \
    VIRALCUTTER_HOST=0.0.0.0

WORKDIR /app

# System deps: ffmpeg (video), build tools (numpy/onnx wheels), fonts (Arabic)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        gcc \
        g++ \
        make \
        python3-dev \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Python deps first (layer caching)
COPY requirements.txt requirements-dev.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && pip install -r requirements-dev.txt

# App source
COPY . .

# VIRALS project root (host-mountable for persistence)
RUN mkdir -p /app/VIRALS /app/models /app/.venv

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860', timeout=3)" || exit 1

CMD ["python3", "webui/app.py", "--preflight", "off"]
