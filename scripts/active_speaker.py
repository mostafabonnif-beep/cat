"""Stable active-speaker selection for multi-person video crops.

The face detector supplies per-face ``activity_score`` values that combine
mouth opening and optional motion compensation.  This module adds temporal
hysteresis so the crop does not jump between speakers on a single noisy frame.
It deliberately has no model or network dependency and can be unit-tested with
plain dictionaries.
"""

from math import hypot
from typing import Any, Dict, List, Optional, Tuple

Face = Dict[str, Any]


class ActiveSpeakerSelector:
    """Select one face over time with identity-aware hysteresis."""

    def __init__(
        self,
        switch_margin: float = 1.5,
        hold_frames: int = 8,
        max_jump: float = 240.0,
        lost_grace_frames: int = 4,
    ):
        self.switch_margin = max(0.0, float(switch_margin))
        self.hold_frames = max(1, int(hold_frames))
        self.max_jump = max(1.0, float(max_jump))
        self.lost_grace_frames = max(0, int(lost_grace_frames))
        self.current_center: Optional[Tuple[float, float]] = None
        self.current_track_id: Optional[int] = None
        self.current_score = 0.0
        self.last_switch_frame = -10**9
        self.missing_frames = 0

    @staticmethod
    def _center(face: Face) -> Optional[Tuple[float, float]]:
        center = face.get("center")
        if isinstance(center, (tuple, list)) and len(center) >= 2:
            try:
                return float(center[0]), float(center[1])
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _track_id(face: Face) -> Optional[int]:
        value = face.get("_track_id", face.get("track_id"))
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _distance(cls, left: Face, right: Face) -> float:
        a = cls._center(left)
        b = cls._center(right)
        if a is None or b is None:
            return float("inf")
        return hypot(a[0] - b[0], a[1] - b[1])

    @staticmethod
    def _score(face: Face) -> float:
        try:
            return float(face.get("activity_score", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _remember(self, face: Face, frame_index: int, switched: bool) -> None:
        self.current_center = self._center(face)
        self.current_track_id = self._track_id(face)
        self.current_score = self._score(face)
        if switched:
            self.last_switch_frame = int(frame_index)
        self.missing_frames = 0

    def select(self, faces: List[Face], frame_index: int = 0) -> Tuple[Optional[Face], bool]:
        """Return ``(active_face, switched)`` for the current frame.

        Track IDs are preferred over centre distance. A missing active face is
        held for a short grace period so a detector blip cannot immediately
        transfer the crop to another person. A new speaker still becomes the
        visible crop candidate during that grace period, but the switch state
        is not committed until the track is stable.
        """
        if not faces:
            self.missing_frames += 1
            return None, False

        ranked = sorted(faces, key=self._score, reverse=True)
        best = ranked[0]
        current = None
        if self.current_track_id is not None:
            current = next(
                (face for face in faces if self._track_id(face) == self.current_track_id),
                None,
            )
        if current is None and self.current_center is not None:
            candidate = min(
                faces,
                key=lambda face: self._distance({"center": self.current_center}, face),
            )
            if self._distance({"center": self.current_center}, candidate) <= self.max_jump:
                current = candidate

        if current is None:
            self.missing_frames += 1
            if self.missing_frames <= self.lost_grace_frames and self.current_center is not None:
                return best, False
            self._remember(best, frame_index, switched=True)
            return best, True

        self.missing_frames = 0
        current_score = self._score(current)
        best_score = self._score(best)
        current_id = self._track_id(current)
        best_id = self._track_id(best)
        is_different = (
            current_id != best_id
            if current_id is not None and best_id is not None
            else self._distance(current, best) > 1.0
        )
        can_switch = int(frame_index) - self.last_switch_frame >= self.hold_frames
        should_switch = (
            is_different
            and can_switch
            and best_score >= current_score + self.switch_margin
        )
        if should_switch:
            self._remember(best, frame_index, switched=True)
            return best, True

        self.current_center = self._center(current)
        self.current_track_id = current_id
        self.current_score = current_score
        return current, False

    def reorder(self, faces: List[Face], frame_index: int = 0) -> Tuple[List[Face], bool]:
        """Return faces with the selected speaker first and stable others."""
        faces = list(faces or [])
        selected, switched = self.select(faces, frame_index)
        if selected is None:
            return faces, switched

        if any(self._track_id(face) is not None for face in faces):
            rest = sorted(
                (face for face in faces if face is not selected),
                key=lambda face: (
                    self._track_id(face) is None,
                    self._track_id(face) if self._track_id(face) is not None else 0,
                ),
            )
        else:
            rest = [face for face in faces if face is not selected]
        return [selected, *rest], switched
