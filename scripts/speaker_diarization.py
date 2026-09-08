# -*- coding: utf-8 -*-
"""Speaker intelligence — speech turns + optional diarization (v7.35).

Two layers, both graceful-degrading:

1. ``detect_speech_turns`` — pure-Python turn detection from the per-frame
   audio-energy array the edit pipeline already computes (no dependency).
   Produces "when does someone speak" even when nothing else is installed.

2. ``diarize_audio`` — optional `pyannote.audio` speaker diarization
   ("who speaks when"). Only used when the package AND a Hugging Face token
   (PYANNOTE_AUTH_TOKEN / HF_TOKEN) are available; otherwise returns None
   and the caller falls back to the single-speaker turns from layer 1.

The association between those segments and the tracked faces happens in
``scripts/voice_face_link.py`` (pure, unit-tested).
"""
from __future__ import annotations

import os

SINGLE_SPEAKER = "SPEAKER_0"


def detect_speech_turns(energies, fps, threshold=0.15, min_turn_seconds=0.8,
                        max_gap_seconds=0.4):
    """Pure speech-turn detection over a per-frame energy array.

    ``energies`` follows the scale the pipeline uses (audio_activity_change
    thresholds at 0.05/0.3), so 0.15 is a sensible middle default. Runs of
    frames above the threshold are merged when separated by less than
    ``max_gap_seconds``; turns shorter than ``min_turn_seconds`` are dropped
    as noise (breaths, clicks).
    Returns ``[{"start": s, "end": s, "speaker": SINGLE_SPEAKER}, ...]`` in
    seconds. Empty list when the array is empty or fully silent.
    """
    if not energies or fps <= 0:
        return []
    frames = [float(v or 0.0) for v in energies]
    speaking = [v >= threshold for v in frames]
    gap_frames = max(1, int(round(max_gap_seconds * fps)))
    min_turn_frames = max(1, int(round(min_turn_seconds * fps)))
    turns = []
    start = None
    last_speech = -1
    for idx, is_speech in enumerate(speaking):
        if is_speech:
            if start is None:
                start = idx
            last_speech = idx
        elif start is not None and (idx - last_speech) > gap_frames:
            if last_speech - start + 1 >= min_turn_frames:
                turns.append({"start": round(start / fps, 3),
                              "end": round((last_speech + 1) / fps, 3),
                              "speaker": SINGLE_SPEAKER})
            start = None
    if start is not None and last_speech - start + 1 >= min_turn_frames:
        turns.append({"start": round(start / fps, 3),
                      "end": round((last_speech + 1) / fps, 3),
                      "speaker": SINGLE_SPEAKER})
    return turns


def diarize_audio(audio_path, token=None):
    """Optional pyannote.audio diarization → [{'start','end','speaker'},...].

    Returns None when pyannote is not installed, no HF token is available,
    or the pipeline fails — the caller then uses the energy fallback.
    """
    token = (token or os.getenv("PYANNOTE_AUTH_TOKEN")
             or os.getenv("HF_TOKEN") or "").strip()
    if not token or not audio_path or not os.path.isfile(audio_path):
        return None
    try:
        from pyannote.audio import Pipeline
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1", use_auth_token=token)
        result = pipeline(audio_path)
        segments = []
        for turn, _track, speaker in result.itertracks(yield_label=True):
            segments.append({"start": round(float(turn.start), 3),
                             "end": round(float(turn.end), 3),
                             "speaker": str(speaker)})
        segments.sort(key=lambda item: item["start"])
        return segments or None
    except Exception:
        return None


def turns_for_clip(audio_path, audio_energies, fps, prefer_diarization=True):
    """Best available speech segments for one rendered clip.

    Order: pyannote diarization (when available & requested) → energy-based
    single-speaker turns. Never raises; returns [] when no speech is found.
    """
    if prefer_diarization:
        segments = diarize_audio(audio_path)
        if segments:
            return segments
    return detect_speech_turns(audio_energies, fps)


def load_segments_file(project_folder):
    """Read a persisted ``speaker_segments.json`` from a project, or None."""
    import json
    path = os.path.join(project_folder, "speaker_segments.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        segments = value.get("segments") if isinstance(value, dict) else None
        return segments if isinstance(segments, list) and segments else None
    except Exception:
        return None
