# -*- coding: utf-8 -*-
"""
GPU OOM Guard — automatic model fallback when Whisper runs out of memory.

Roadmap item 4.1 ("حماية ذاكرة GPU"). On a low-VRAM GPU the transcription
model can die mid-batch (torch.cuda.OutOfMemoryError or an "out of
memory" CUDA error), killing the whole run. This wrapper retries with a
smaller model: large-v3 → medium → small → base, logs which model
actually worked, and only gives up after the chain is exhausted.

Usage (drop-in):
    from scripts import oom_guard
    srt, tsv = oom_guard.transcribe_with_fallback(
        input_file, "large-v3-turbo", project_folder)
"""

import gc
import os

FALLBACK_CHAIN = {
    "large-v3": "medium",
    "large-v3-turbo": "medium",
    "large": "medium",
    "medium": "small",
    "small": "base",
    "base": "tiny",
}
CHAIN_END = "tiny"

OOM_MARKERS = (
    "out of memory",
    "cuda out of memory",
    "cudnn error",
    "cublas error",
    "RuntimeError: CUDA",
    "insufficient memory",
    "cannot allocate",
)


def _looks_like_oom(exc):
    msg = str(exc)
    return any(m.lower() in msg.lower() for m in OOM_MARKERS)


def _next_model(model_name):
    return FALLBACK_CHAIN.get(str(model_name).strip())


def transcribe_with_fallback(input_file, model_name, project_folder,
                             transcribe_fn=None, verbose=True, device="auto"):
    """Transcribe, retrying smaller on OOM. Returns (srt_file, tsv_file).

    `transcribe_fn` defaults to scripts.transcribe_video.transcribe and is
    injectable for tests. The chosen model is recorded in
    project_folder/transcription_model.json.
    """
    if transcribe_fn is None:
        from scripts.transcribe_video import transcribe as transcribe_fn

    current = model_name
    attempts = []
    while True:
        try:
            kwargs = {"project_folder": project_folder}
            if str(device or "auto").lower() in {"cpu", "cuda"}:
                kwargs["device"] = str(device).lower()
            result = transcribe_fn(input_file, current, **kwargs)
            _record_used_model(project_folder, current, attempts)
            if verbose and attempts:
                print("[oom-guard] transcription succeeded with '{}' "
                      "(after {} failed attempt(s))".format(current, len(attempts)))
            return result
        except Exception as e:
            if not _looks_like_oom(e):
                raise  # not a memory problem — let the caller handle it
            smaller = _next_model(current)
            attempts.append(current)
            if smaller is None:
                print("[oom-guard] OOM even on '{}' — giving up: {}".format(current, e))
                raise
            if verbose:
                print("[oom-guard] OOM with '{}' — falling back to '{}'".format(
                    current, smaller))
            # free VRAM before reloading
            try:
                import torch
                if torch.cuda.is_available():
                    gc.collect()
                    torch.cuda.empty_cache()
            except Exception:
                pass
            current = smaller


def _record_used_model(project_folder, model, attempts):
    import json
    try:
        os.makedirs(project_folder, exist_ok=True)
        with open(os.path.join(project_folder, "transcription_model.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"requested": model, "used": model,
                       "oom_fallbacks": attempts}, f, indent=2)
    except Exception:
        pass


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ViralCutter GPU OOM guard.")
    parser.add_argument("--video", required=True)
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--project", default="tmp")
    args = parser.parse_args()
    srt, tsv = transcribe_with_fallback(args.video, args.model, args.project)
    print("SRT: {}\nTSV: {}".format(srt, tsv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
