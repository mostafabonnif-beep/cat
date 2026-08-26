# OUSSAMA Cutter 7.25.0-pro

This build is based on the uploaded ViralCutter source and includes a
production-oriented reliability layer.

> **Attribution** — upstream project: [ViralCutter](https://github.com/RafaelGodoyEbert/ViralCutter)
> by [Rafael Godoy](https://github.com/RafaelGodoyEbert), GPL-3.0.

## New modules
- `scripts/pipeline_engine.py`
- `webui/editor_core.py`
- `webui/render_queue.py`
- `webui/telegram_control.py` (optional local Telegram queue control)

## Resilient transcription backend

The primary WhisperX + Torch path remains preferred. If it is unavailable or its dependency stack is broken, install the optional `requirements-transcribe-fallback.txt` profile and use `VIRALCUTTER_TRANSCRIPTION_BACKEND=auto` (default) or `faster-whisper` to force the independent local backend. Placeholder subtitles remain disabled by default.

## Security
- API keys are not put in WebUI child-process argv.
- Secure credential storage requires real `cryptography` encryption.
- No XOR fallback is used.
- Secure files are written atomically.

## Run
Use the existing project entry points documented in `README.md` and
`README_DEV.md`.

## Validation
Run:
`python -m pytest -q`
