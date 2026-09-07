# -*- coding: utf-8 -*-
"""Regression tests for v7.30: scene-aware crop reset + eased transitions.

* ``scene_boundary_frames`` converts scene times to cut-frame indices;
* ``ActiveSpeakerSelector.reset()`` forgets the previous shot's speaker;
* ``interpolate_boxes`` eases crop moves with smoothstep instead of a
  robotic constant-speed pan;
* Moroccan Darija hook words score in ``seo_titles``.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import scene_detect
from scripts.active_speaker import ActiveSpeakerSelector
from scripts.edit_video import interpolate_boxes

# ---------------------------------------------------------------------------
# scene_boundary_frames
# ---------------------------------------------------------------------------

def test_boundary_frames_skip_opening_scene():
    scenes = [(0.0, 4.0), (4.0, 9.5), (9.5, 20.0)]
    frames = scene_detect.scene_boundary_frames(scenes, fps=30.0)
    assert frames == {120, 285}
    assert 0 not in frames


def test_boundary_frames_respects_total_frames():
    scenes = [(0.0, 4.0), (4.0, 9.5)]
    assert scene_detect.scene_boundary_frames(scenes, 30.0, total_frames=100) == set()
    assert scene_detect.scene_boundary_frames(scenes, 30.0, total_frames=200) == {120}


def test_boundary_frames_guards_broken_fps():
    scenes = [(0.0, 2.0), (2.0, 5.0)]
    assert scene_detect.scene_boundary_frames(scenes, 0.0) == {60}   # 30fps fallback
    assert scene_detect.scene_boundary_frames(scenes, 999.0) == {60}
    assert scene_detect.scene_boundary_frames([], 25.0) == set()


# ---------------------------------------------------------------------------
# ActiveSpeakerSelector.reset
# ---------------------------------------------------------------------------

def test_selector_reset_forgets_previous_shot_speaker():
    selector = ActiveSpeakerSelector(switch_margin=1.5, hold_frames=8)
    speaker_a = {"center": (100, 100), "activity_score": 10.0, "_track_id": 0}
    selected, switched = selector.select([speaker_a], frame_index=0)
    assert switched and selector.current_track_id == 0

    selector.reset()
    assert selector.current_center is None
    assert selector.current_track_id is None
    assert selector.current_score == 0.0
    assert selector.missing_frames == 0

    # After a reset, a brand-new speaker may take over IMMEDIATELY — the old
    # hold window from the previous shot must not delay the switch.
    speaker_b = {"center": (500, 100), "activity_score": 10.0, "_track_id": 1}
    selected, switched = selector.select([speaker_b], frame_index=1)
    assert selected is speaker_b and switched


# ---------------------------------------------------------------------------
# interpolate_boxes (smoothstep easing)
# ---------------------------------------------------------------------------

def test_interpolate_boxes_lands_exactly_on_target():
    frames = interpolate_boxes([0, 0, 100, 100], [400, 200, 500, 300], 4)
    assert len(frames) == 4
    assert frames[-1] == [400, 200, 500, 300]


def test_interpolate_boxes_smoothstep_eases_in():
    start, end = [0, 0, 100, 100], [400, 0, 500, 100]
    eased = interpolate_boxes(start, end, 4)
    linear = interpolate_boxes(start, end, 4, easing="linear")
    # Quarter-way in, smoothstep is BEHIND the linear lerp (ease-in), and
    # three-quarters in it is AHEAD (ease-out) — the S-curve that makes
    # camera moves feel intentional.
    assert eased[0][0] < linear[0][0]
    assert eased[2][0] > linear[2][0]
    # Monotonic forward progress either way.
    xs = [f[0] for f in eased]
    assert xs == sorted(xs)


def test_interpolate_boxes_handles_multi_face_arrays():
    start = [[0, 0, 100, 100], [500, 0, 600, 100]]
    end = [[100, 0, 200, 100], [600, 0, 700, 100]]
    frames = interpolate_boxes(start, end, 3)
    assert len(frames) == 3
    assert frames[-1] == [[100, 0, 200, 100], [600, 0, 700, 100]]


# ---------------------------------------------------------------------------
# Darija hook words
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "كيفاش تربح من هاد الفكرة في 5 خطوات؟",
    "علاش الناس كيفشقو فهاد النقطة؟",
    "شنو خاصك تعرف قبل ما تبدا؟",
    "واش عرفتي هاد السر ديال النجاح؟",
])
def test_darija_hook_words_score(title):
    from scripts import seo_titles
    result = seo_titles.score_title(title)
    # 7-point hook-word bonus + question mark + digit where present: the
    # dialect phrasing must score at least as well as an MSA equivalent.
    assert result["breakdown"]["hook"] >= 7.0
