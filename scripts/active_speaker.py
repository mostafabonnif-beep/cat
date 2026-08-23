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
    """Select one face over time while requiring a meaningful score lead."""

    def __init__(self, switch_margin: float = 1.5, hold_frames: int = 8, max_jump: float = 240.0):
        self.switch_margin = max(0.0, float(switch_margin))
        self.hold_frames = max(1, int(hold_frames))
        self.max_jump = max(1.0, float(max_jump))
        self.current_center: Optional[Tuple[float, float]] = None
        self.current_score = 0.0
        self.last_switch_frame = -10**9

    @staticmethod
    def _center(face: Face) -> Optional[Tuple[float, float]]:
        center = face.get("center")
        if isinstance(center, (tuple, list)) and len(center) >= 2:
            try:
                return float(center[0]), float(center[1])
            except (TypeError, ValueError):
                return None
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

    def select(self, faces: List[Face], frame_index: int = 0) -> Tuple[Optional[Face], bool]:
        """Return ``(active_face, switched)`` for the current frame.

        A new face must beat the tracked face by ``switch_margin`` and the
        previous selection must have been held for ``hold_frames``.  If the
        current face disappears, the best available face is selected after a
        conservative reset.
        """
        if not faces:
            self.current_center = None
            self.current_score = 0.0
            return None, False

        ranked = sorted(faces, key=self._score, reverse=True)
        best = ranked[0]
        best_score = self._score(best)
        current = None
        if self.current_center is not None:
            current = min(
                faces,
                key=lambda face: self._distance(
                    {"center": self.current_center}, face
                ),
            )
            if self._distance({"center": self.current_center}, current) > self.max_jump:
                current = None

        if current is None:
            self.current_center = self._center(best)
            self.current_score = best_score
            self.last_switch_frame = int(frame_index)
            return best, True

        current_score = self._score(current)
        is_different = self._distance(current, best) > 1.0
        can_switch = int(frame_index) - self.last_switch_frame >= self.hold_frames
        should_switch = is_different and can_switch and best_score >= current_score + self.switch_margin
        if should_switch:
            self.current_center = self._center(best)
            self.current_score = best_score
            self.last_switch_frame = int(frame_index)
            return best, True

        self.current_score = current_score
        return current, False

    def reorder(self, faces: List[Face], frame_index: int = 0) -> Tuple[List[Face], bool]:
        """Return faces with the selected speaker first, preserving the rest."""
        selected, switched = self.select(faces, frame_index)
        if selected is None:
            return list(faces), switched
        ordered = [selected]
        ordered.extend(face for face in faces if face is not selected)
        return ordered, switched
