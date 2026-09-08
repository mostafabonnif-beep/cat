# -*- coding: utf-8 -*-
"""Voice ↔ face association for active-speaker framing (v7.35).

The edit loop knows, per frame, which face has an open mouth — and the audio
knows when someone speaks (and, with diarization, *which* speaker). This
class correlates the two *online*: while the render runs, every frame's
mouth-open flags are attributed to the speaker that is active at that
moment, so by the middle of the clip we can say with confidence
"track #3 is the mouth of SPEAKER_01".

It is a pure, deterministic, dependency-free module so the whole logic is
unit-testable with synthetic tracks.
"""
from __future__ import annotations


class VoiceFaceLink:
    """Online mouth-activity ↔ speaker-segment association."""

    def __init__(self, turns, seconds_per_frame=1.0 / 30.0,
                 min_confidence=0.1, margin=0.15):
        self.turns = sorted(
            ({"start": float(t["start"]), "end": float(t["end"]),
              "speaker": str(t["speaker"])} for t in (turns or [])),
            key=lambda item: item["start"])
        self.seconds_per_frame = float(seconds_per_frame) if seconds_per_frame else (1.0 / 30.0)
        self.min_confidence = max(0.0, float(min_confidence))
        self.margin = max(0.0, float(margin))
        # track -> speaker -> [open_frames, total_frames]
        self._counts = {}

    def speaker_at(self, seconds):
        """The speaker active at ``seconds``, or None in gaps/silence."""
        if seconds is None:
            return None
        for turn in self.turns:
            if turn["start"] <= seconds < turn["end"]:
                return turn["speaker"]
            if turn["start"] > seconds:
                break
        return None

    def update(self, seconds, mouth_open_by_track):
        """Attribute one frame of mouth flags to the active speaker.

        ``mouth_open_by_track``: {track_key: bool}. Only frames *inside* a
        speaker's turn are counted (silence teaches nothing).
        """
        speaker = self.speaker_at(seconds)
        if speaker is None or not mouth_open_by_track:
            return
        for track, is_open in mouth_open_by_track.items():
            counts = self._counts.setdefault(track, {}).setdefault(speaker, [0, 0])
            counts[1] += 1
            if is_open:
                counts[0] += 1

    def association(self):
        """track -> {speaker: confidence (0..1)} from the frames seen so far."""
        result = {}
        for track, by_speaker in self._counts.items():
            result[track] = {}
            for speaker, (opened, total) in by_speaker.items():
                if total > 0:
                    result[track][speaker] = opened / total
        return result

    def best_speaker_for(self, track):
        """(speaker, confidence) with the highest mouth correlation, or (None, 0)."""
        best = (None, 0.0)
        for speaker, confidence in (self._counts.get(track) or {}).items():
            opened, total = confidence
            if total > 0:
                ratio = opened / total
                if ratio > best[1]:
                    best = (speaker, ratio)
        return best

    def current_speaker_face(self, seconds, tracks):
        """(track, confidence) that best matches the *currently active* speaker.

        Returns (None, 0.0) when nobody is speaking now, when no track has
        enough evidence, or when the top two candidates are within ``margin``
        (ambiguous — better to stay with the hysteresis-based selection).
        """
        speaker = self.speaker_at(seconds)
        if speaker is None or not tracks:
            return None, 0.0
        scored = []
        for track in tracks:
            best_speaker, confidence = self.best_speaker_for(track)
            if best_speaker == speaker:
                scored.append((confidence, track))
        if not scored:
            return None, 0.0
        scored.sort(reverse=True)
        top_conf, top_track = scored[0]
        if top_conf < self.min_confidence:
            return None, 0.0
        if len(scored) > 1 and (top_conf - scored[1][0]) < self.margin:
            return None, 0.0  # ambiguous — do not steal the decision
        return top_track, top_conf
