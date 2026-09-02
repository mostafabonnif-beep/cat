"""Lightweight identity tracker for the face-crop pipeline (v7.28).

InsightFace detection is stateless: every detection cycle re-discovers faces
from scratch, so the crop pipeline used to re-match faces purely by position
each cycle. When two similar faces are side by side, or a face disappears for
a few cycles and comes back, that positional re-matching can swap identities
and make the crop box jump between people.

This module adds stable per-face IDs with:

* greedy unique matching (each detection consumes at most one track) gated by
  a scale-normalised centre distance;
* per-track velocity prediction so a briefly-missing face is matched back to
  its predicted position instead of being mistaken for a brand-new person;
* a missing-cycle lifecycle (tracks die after ``max_missing_cycles`` without
  a detection, new faces spawn fresh tracks);
* deterministic ordering: matched tracks are returned sorted by ID, so slot 0
  of the crop layout always means "the same person".

No external dependencies (pure Python / math) so it is fully unit-testable
without OpenCV or a GPU detector.
"""

from __future__ import annotations

from typing import List, Optional, Sequence


def _box_center(box: Sequence[float]) -> tuple:
    x1, y1, x2, y2 = (float(v) for v in box[:4])
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _box_diag(box: Sequence[float]) -> float:
    x1, y1, x2, y2 = (float(v) for v in box[:4])
    return max(1e-6, ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)


class _Track:
    """Internal per-face state."""

    __slots__ = ("id", "bbox", "center", "velocity", "missing_cycles",
                 "last_frame", "born_frame")

    def __init__(self, track_id: int, bbox, frame_index: int, velocity=None):
        self.id = track_id
        self.bbox = [float(v) for v in bbox[:4]]
        self.center = _box_center(self.bbox)
        self.velocity = (0.0, 0.0) if velocity is None else velocity
        self.missing_cycles = 0
        self.last_frame = frame_index
        self.born_frame = frame_index

    def predict_center(self):
        """Where we expect this face to be on the next detection cycle."""
        return (self.center[0] + self.velocity[0],
                self.center[1] + self.velocity[1])


class FaceTracker:
    """Tracks face boxes across detection cycles and assigns stable IDs.

    Parameters
    ----------
    match_gate:
        Maximum normalised centre distance for a detection to be considered
        the same face (distance divided by the mean of the two boxes'
        diagonals). 1.0 = one full box-length apart still matches; 0.35 is a
        reasonable default for talking-head videos.
    max_missing_cycles:
        A track that receives no detection for this many consecutive
        ``update()`` calls is dropped; a returning face then gets a new ID.
    velocity_alpha:
        EMA factor for the per-track velocity estimate (0 = no prediction).
    """

    def __init__(self, match_gate: float = 0.35,
                 max_missing_cycles: int = 3, velocity_alpha: float = 0.4):
        self.match_gate = max(0.01, float(match_gate))
        self.max_missing_cycles = max(0, int(max_missing_cycles))
        self.velocity_alpha = max(0.0, min(0.95, float(velocity_alpha)))
        self._tracks = {}  # id -> _Track
        self._next_id = 0
        self.frame_index = 0
        self.last_ids = []  # ids aligned to the previous update() input

    # ------------------------------------------------------------------ API
    def reset(self):
        self._tracks.clear()
        self._next_id = 0
        self.last_ids = []

    def update(self, frame_index: int, boxes: Sequence[Sequence[float]]) -> List[Optional[int]]:
        """Match ``boxes`` against the alive tracks and update state.

        Returns a list of track IDs aligned to ``boxes`` (``None`` marks a
        box that spawned a new track this cycle — the spawned ID is returned
        on the *next* update, matching classic tracker semantics is not needed
        here, so new boxes return their fresh ID immediately).
        """
        self.frame_index = int(frame_index)
        boxes = [list(b[:4]) for b in (boxes or [])]
        alive = {tid: t for tid, t in self._tracks.items()
                 if t.missing_cycles <= self.max_missing_cycles}

        matched_track_ids = set()
        assignment = [None] * len(boxes)

        # Greedy unique matching, cheapest pair first.
        candidates = []
        for i, box in enumerate(boxes):
            diag = _box_diag(box)
            for tid, track in alive.items():
                if track.missing_cycles > 0:
                    tc = track.predict_center()
                else:
                    tc = track.center
                bc = _box_center(box)
                dist = ((tc[0] - bc[0]) ** 2 + (tc[1] - bc[1]) ** 2) ** 0.5
                norm = dist / max(1e-6, (diag + _box_diag(track.bbox)) / 2.0)
                if norm <= self.match_gate:
                    candidates.append((norm, i, tid))
        candidates.sort(key=lambda item: (item[0], item[2]))
        for _norm, i, tid in candidates:
            if assignment[i] is None and tid not in matched_track_ids:
                assignment[i] = tid
                matched_track_ids.add(tid)

        # Consume matched detections into their tracks.
        for i, tid in enumerate(assignment):
            if tid is None:
                continue
            track = self._tracks[tid]
            box = boxes[i]
            new_center = _box_center(box)
            if track.missing_cycles == 0 and track.last_frame < self.frame_index:
                # EMA velocity from the observed centre displacement.
                vx = new_center[0] - track.center[0]
                vy = new_center[1] - track.center[1]
                a = self.velocity_alpha
                track.velocity = (a * vx + (1 - a) * track.velocity[0],
                                  a * vy + (1 - a) * track.velocity[1])
            track.bbox = box
            track.center = new_center
            track.missing_cycles = 0
            track.last_frame = self.frame_index

        # Age unmatched tracks; drop dead ones.
        dead = []
        for tid, track in self._tracks.items():
            if track.last_frame < self.frame_index:
                track.missing_cycles += 1
            if track.missing_cycles > self.max_missing_cycles:
                dead.append(tid)
        for tid in dead:
            del self._tracks[tid]

        # Spawn tracks for unmatched detections.
        for i, tid in enumerate(assignment):
            if tid is None:
                track = _Track(self._next_id, boxes[i], self.frame_index)
                self._tracks[self._next_id] = track
                assignment[i] = self._next_id
                self._next_id += 1

        self.last_ids = list(assignment)
        return assignment

    def stable_order(self, ids: Sequence[Optional[int]], fallback_order=None):
        """Index order for the current detections that keeps identities stable.

        Previously-known IDs sort first (ascending ID), brand-new tracks keep
        their detection order at the end. ``fallback_order`` is applied as the
        tie-breaker inside each group (e.g. left-to-right or by area).
        """
        ids = list(ids)
        fallback_order = list(fallback_order or range(len(ids)))
        keyed = sorted(
            range(len(ids)),
            key=lambda i: (
                0 if ids[i] is not None else 1,          # known faces first
                ids[i] if ids[i] is not None else 0,     # then by ID
                fallback_order.index(i) if i in fallback_order else i,
            ),
        )
        return keyed

    def track_count(self) -> int:
        return len(self._tracks)

    def alive_ids(self) -> List[int]:
        return sorted(self._tracks.keys())

    def snapshot(self) -> List[dict]:
        """Diagnostics: one dict per alive track."""
        out = []
        for tid in sorted(self._tracks):
            t = self._tracks[tid]
            out.append({
                "id": tid,
                "bbox": [round(v, 1) for v in t.bbox],
                "missing_cycles": t.missing_cycles,
                "last_frame": t.last_frame,
                "born_frame": t.born_frame,
            })
        return out
