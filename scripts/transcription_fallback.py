"""Optional faster-whisper backend for resilient local transcription.

This module is deliberately lazy: importing it never imports Torch, CTranslate2,
or downloads a model. The main WhisperX path remains the preferred backend;
this backend is used only when WhisperX is unavailable or its import stack is
broken. It returns the same basic segment shape used by the local pipeline.
"""
from __future__ import annotations

import importlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

_MODEL_ALIASES = {
    "large-v3-turbo": "turbo",
    "turbo": "turbo",
}


def _module():
    try:
        return importlib.import_module("faster_whisper")
    except Exception:
        return None


def availability() -> Dict[str, Any]:
    """Return a secret-free probe result without loading a model."""
    try:
        module = _module()
        if module is None:
            return {"ok": False, "version": "", "error": "faster_whisper is not installed"}
        return {
            "ok": callable(getattr(module, "WhisperModel", None)),
            "version": str(getattr(module, "__version__", "installed")),
            "error": "" if callable(getattr(module, "WhisperModel", None)) else "WhisperModel is missing",
        }
    except Exception as exc:  # optional backend must never break diagnostics
        return {"ok": False, "version": "", "error": str(exc)}


def _resolve_device(device: str) -> Tuple[str, str]:
    requested = str(device or "auto").strip().lower()
    if requested not in {"auto", "cpu", "cuda"}:
        requested = "auto"
    if requested == "cuda":
        return "cuda", os.getenv("VIRALCUTTER_FASTER_WHISPER_COMPUTE_TYPE", "float16")
    if requested == "cpu":
        return "cpu", os.getenv("VIRALCUTTER_FASTER_WHISPER_COMPUTE_TYPE", "int8")
    try:
        import torch

        if bool(torch.cuda.is_available()):
            return "cuda", os.getenv("VIRALCUTTER_FASTER_WHISPER_COMPUTE_TYPE", "float16")
    except Exception:
        pass
    return "cpu", os.getenv("VIRALCUTTER_FASTER_WHISPER_COMPUTE_TYPE", "int8")


def _number(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number >= 0 else default


def _word_dict(word: Any) -> Optional[Dict[str, Any]]:
    start = _number(getattr(word, "start", None))
    end = _number(getattr(word, "end", None))
    text = str(getattr(word, "word", "") or "").strip()
    if not text or start is None or end is None or end <= start:
        return None
    return {"word": text, "start": start, "end": end}


def normalize_segments(segments: Iterable[Any]) -> List[Dict[str, Any]]:
    """Convert faster-whisper segment objects to JSON-safe pipeline segments."""
    normalized: List[Dict[str, Any]] = []
    for segment in segments:
        start = _number(getattr(segment, "start", None))
        end = _number(getattr(segment, "end", None))
        text = str(getattr(segment, "text", "") or "").strip()
        if start is None or end is None or end <= start or not text:
            continue
        item: Dict[str, Any] = {"start": start, "end": end, "text": text}
        words = getattr(segment, "words", None)
        if words:
            normalized_words = [word_item for word in words if (word_item := _word_dict(word))]
            if normalized_words:
                item["words"] = normalized_words
        normalized.append(item)
    return normalized


def transcribe(
    input_file: str,
    model_name: str = "large-v3",
    device: str = "auto",
    progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Run faster-whisper lazily and return a WhisperX-like result dictionary."""
    module = _module()
    if module is None or not callable(getattr(module, "WhisperModel", None)):
        raise ImportError(
            "faster-whisper is not installed; install requirements-transcribe-fallback.txt"
        )
    runtime_device, compute_type = _resolve_device(device)
    resolved_model = _MODEL_ALIASES.get(str(model_name or "large-v3").strip(), str(model_name or "large-v3").strip())
    if progress:
        progress(f"جاري تحميل faster-whisper ({resolved_model}) على {runtime_device}")
    model = module.WhisperModel(resolved_model, device=runtime_device, compute_type=compute_type)
    if progress:
        progress("جاري التفريغ عبر faster-whisper مع VAD محلي")
    segments, info = model.transcribe(
        input_file,
        beam_size=5,
        vad_filter=True,
        word_timestamps=True,
    )
    normalized = normalize_segments(segments)
    if not normalized:
        raise RuntimeError("faster-whisper returned no valid timestamped segments")
    language = str(getattr(info, "language", "") or "unknown")
    return {
        "segments": normalized,
        "language": language,
        "backend": "faster-whisper",
        "device": runtime_device,
        "compute_type": compute_type,
        "model": resolved_model,
    }


def _atomic_write(path: str, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _timestamp(seconds: float, decimal: str = ",") -> str:
    total_ms = max(0, int(round(float(seconds) * 1000)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{decimal}{millis:03d}"


def write_outputs(result: Dict[str, Any], srt_path: str, tsv_path: str, json_path: str) -> None:
    """Write fallback outputs in the same filenames expected by OUSSAMA."""
    segments = list(result.get("segments") or [])
    srt_blocks = []
    tsv_lines = ["start\tend\ttext"]
    for index, segment in enumerate(segments, start=1):
        start = float(segment["start"])
        end = float(segment["end"])
        text = str(segment.get("text", "")).replace("\n", " ").strip()
        srt_blocks.append(f"{index}\n{_timestamp(start)} --> {_timestamp(end)}\n{text}\n")
        tsv_lines.append(f"{start:.3f}\t{end:.3f}\t{text}")
    _atomic_write(srt_path, "\n".join(srt_blocks) + "\n")
    _atomic_write(tsv_path, "\n".join(tsv_lines) + "\n")
    _atomic_write(json_path, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
