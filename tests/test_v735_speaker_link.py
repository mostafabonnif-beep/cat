# -*- coding: utf-8 -*-
"""v7.35 — voice-face link: speech turns, association, crop boost helper."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import speaker_diarization as sd
from scripts.edit_video import _apply_voice_face_link
from scripts.voice_face_link import VoiceFaceLink


class TestSpeechTurns:
    def test_single_turn(self):
        # 30fps: frames 0-30 speech, then silence
        energies = [0.5] * 30 + [0.01] * 60
        turns = sd.detect_speech_turns(energies, fps=30)
        assert len(turns) == 1
        assert turns[0]["start"] == 0.0
        assert turns[0]["end"] == pytest.approx(1.0)
        assert turns[0]["speaker"] == sd.SINGLE_SPEAKER

    def test_two_turns_separated_by_silence(self):
        energies = ([0.5] * 24 + [0.01] * 60 +
                    [0.5] * 24 + [0.01] * 60)
        turns = sd.detect_speech_turns(energies, fps=30)
        assert len(turns) == 2
        assert turns[1]["start"] == pytest.approx(2.8)

    def test_short_pauses_inside_turn_are_merged(self):
        # 0.3s gap (< 0.4s max_gap) inside speech is merged into one turn.
        energies = ([0.5] * 30 + [0.01] * 9 + [0.5] * 30 + [0.01] * 60)
        turns = sd.detect_speech_turns(energies, fps=30)
        assert len(turns) == 1
        assert turns[0]["end"] == pytest.approx(2.3)

    def test_noise_bursts_below_min_turn_are_dropped(self):
        energies = [0.01] * 90
        energies[30] = 0.9  # 1-frame click
        energies[31] = 0.9
        assert sd.detect_speech_turns(energies, fps=30,
                                      min_turn_seconds=0.8) == []

    def test_empty_or_silent_input(self):
        assert sd.detect_speech_turns([], fps=30) == []
        assert sd.detect_speech_turns([0.0] * 100, fps=30) == []
        assert sd.detect_speech_turns([0.5] * 10, fps=0) == []


class TestDiarizationFallbacks:
    def test_diarize_audio_returns_none_without_pyannote(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PYANNOTE_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("HF_TOKEN", raising=False)
        clip = tmp_path / "a.mp4"
        clip.write_bytes(b"x")
        # No pyannote + no token in the sandbox → graceful None.
        assert sd.diarize_audio(str(clip)) is None

    def test_turns_for_clip_uses_energy_fallback(self, tmp_path):
        clip = tmp_path / "a.mp4"
        clip.write_bytes(b"x")
        energies = [0.5] * 30 + [0.01] * 60
        turns = sd.turns_for_clip(str(clip), energies, fps=30)
        assert len(turns) == 1
        assert turns[0]["speaker"] == sd.SINGLE_SPEAKER

    def test_load_segments_file(self, tmp_path):
        assert sd.load_segments_file(str(tmp_path)) is None
        with open(os.path.join(str(tmp_path), "speaker_segments.json"),
                  "w", encoding="utf-8") as handle:
            import json
            json.dump({"segments": [{"start": 0, "end": 5, "speaker": "A"}]},
                      handle)
        loaded = sd.load_segments_file(str(tmp_path))
        assert loaded == [{"start": 0, "end": 5, "speaker": "A"}]


class TestVoiceFaceLink:
    def _link(self):
        turns = [{"start": 0.0, "end": 10.0, "speaker": "A"},
                 {"start": 10.0, "end": 20.0, "speaker": "B"}]
        return VoiceFaceLink(turns, seconds_per_frame=1.0 / 30.0)

    def test_speaker_at(self):
        link = self._link()
        assert link.speaker_at(5.0) == "A"
        assert link.speaker_at(15.0) == "B"
        assert link.speaker_at(25.0) is None
        assert link.speaker_at(None) is None

    def test_association_learns_whose_mouth_matches(self):
        link = self._link()
        # Track 1 opens its mouth only during speaker A; track 2 only in B.
        for frame in range(0, 300):     # 0-10s
            link.update(frame / 30.0, {1: True, 2: False})
        for frame in range(300, 600):   # 10-20s
            link.update(frame / 30.0, {1: False, 2: True})
        association = link.association()
        assert association[1]["A"] == 1.0
        assert association[2]["B"] == 1.0
        assert link.best_speaker_for(1) == ("A", 1.0)
        assert link.best_speaker_for(2) == ("B", 1.0)
        # During speaker B the face with the open mouth is track 2.
        winner, confidence = link.current_speaker_face(15.0, [1, 2])
        assert winner == 2
        assert confidence == 1.0
        # Nothing is speaking at 25s.
        assert link.current_speaker_face(25.0, [1, 2]) == (None, 0.0)

    def test_ambiguous_tracks_are_not_stolen(self):
        link = VoiceFaceLink(
            [{"start": 0.0, "end": 10.0, "speaker": "A"}],
            min_confidence=0.1, margin=0.15)
        # Both tracks talk half of A's turn → near-equal confidence.
        for frame in range(0, 300):
            link.update(frame / 30.0, {1: frame % 2 == 0, 2: frame % 2 == 1})
        winner, _ = link.current_speaker_face(5.0, [1, 2])
        assert winner is None  # ambiguous → hysteresis keeps the decision

    def test_update_ignores_silence_frames(self):
        link = self._link()
        link.update(25.0, {1: True})  # outside all turns
        assert link.association() == {}


class TestApplyVoiceFaceLink:
    def test_boost_winner_penalize_others(self):
        link = VoiceFaceLink([{"start": 0.0, "end": 10.0, "speaker": "A"}])
        for frame in range(0, 300):
            link.update(frame / 30.0, {1: True, 2: False})
        faces = [
            {"_track_id": 1, "activity_score": 5.0, "is_talking": True},
            {"_track_id": 2, "activity_score": 5.0, "is_talking": False},
        ]
        _apply_voice_face_link(faces, link, 5.0)
        assert faces[0]["activity_score"] == 7.0   # 5 + boost(2)
        assert faces[1]["activity_score"] == 4.25  # 5 - penalty(0.75)

    def test_no_winner_means_no_change(self):
        link = VoiceFaceLink([{"start": 0.0, "end": 10.0, "speaker": "A"}])
        faces = [
            {"_track_id": 1, "activity_score": 5.0},
            {"_track_id": 2, "activity_score": 5.0},
        ]
        # No evidence accumulated → current_speaker_face returns None.
        _apply_voice_face_link(faces, link, 5.0)
        assert faces[0]["activity_score"] == 5.0
        assert faces[1]["activity_score"] == 5.0

    def test_single_face_is_untouched(self):
        link = VoiceFaceLink([{"start": 0.0, "end": 10.0, "speaker": "A"}])
        link.update(1.0, {1: True})
        faces = [{"_track_id": 1, "activity_score": 5.0}]
        _apply_voice_face_link(faces, link, 1.0)
        assert faces[0]["activity_score"] == 5.0

    def test_scores_are_capped(self):
        link = VoiceFaceLink([{"start": 0.0, "end": 10.0, "speaker": "A"}])
        for frame in range(0, 300):
            link.update(frame / 30.0, {1: True, 2: False})
        faces = [
            {"_track_id": 1, "activity_score": 19.5, "is_talking": True},
            {"_track_id": 2, "activity_score": 0.2, "is_talking": False},
        ]
        _apply_voice_face_link(faces, link, 5.0)
        assert faces[0]["activity_score"] == 20.0
        assert faces[1]["activity_score"] == 0.0
