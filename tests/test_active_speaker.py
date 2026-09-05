from scripts.active_speaker import ActiveSpeakerSelector


def face(x, score):
    return {"center": (x, 100), "activity_score": score}


def test_selector_keeps_current_speaker_during_hold_window():
    selector = ActiveSpeakerSelector(switch_margin=1.0, hold_frames=3)
    first, switched = selector.select([face(10, 5), face(200, 1)], frame_index=0)
    assert first["center"] == (10, 100)
    assert switched is True

    second, switched = selector.select([face(10, 1), face(200, 5)], frame_index=1)
    assert second["center"] == (10, 100)
    assert switched is False


def test_selector_switches_after_margin_and_hold():
    selector = ActiveSpeakerSelector(switch_margin=1.0, hold_frames=2)
    selector.select([face(10, 5), face(200, 1)], frame_index=0)
    selector.select([face(10, 1), face(200, 5)], frame_index=1)
    selected, switched = selector.select([face(10, 1), face(200, 5)], frame_index=2)
    assert selected["center"] == (200, 100)
    assert switched is True


def test_reorder_puts_active_speaker_first():
    selector = ActiveSpeakerSelector(switch_margin=0.5, hold_frames=1)
    faces = [face(10, 1), face(200, 4)]
    ordered, switched = selector.reorder(faces, frame_index=0)
    assert ordered[0]["center"] == (200, 100)
    assert len(ordered) == 2
    assert switched is True


def test_selector_prefers_tracker_identity_over_detection_order():
    selector = ActiveSpeakerSelector(switch_margin=1.0, hold_frames=1)
    first = {"_track_id": 10, "center": (100, 100), "activity_score": 5.0}
    second = {"_track_id": 20, "center": (300, 100), "activity_score": 1.0}
    selected, _ = selector.select([first, second], frame_index=0)
    assert selected["_track_id"] == 10

    # Detector order changes and the other face becomes louder briefly. The
    # tracked speaker stays selected until the score lead is meaningful.
    first = {"_track_id": 10, "center": (230, 100), "activity_score": 2.0}
    second = {"_track_id": 20, "center": (170, 100), "activity_score": 2.5}
    selected, switched = selector.select([second, first], frame_index=1)
    assert selected["_track_id"] == 10
    assert switched is False


def test_selector_survives_short_detector_gap():
    selector = ActiveSpeakerSelector(hold_frames=1, lost_grace_frames=2)
    speaker = {"_track_id": 1, "center": (100, 100), "activity_score": 5.0}
    other = {"_track_id": 2, "center": (400, 100), "activity_score": 1.0}
    selector.select([speaker, other], frame_index=0)
    selected, switched = selector.select([other], frame_index=1)
    assert selected["_track_id"] == 2
    assert switched is False
    selected, switched = selector.select([other], frame_index=2)
    assert selected["_track_id"] == 2
    assert switched is False
    selected, switched = selector.select([other], frame_index=3)
    assert selected["_track_id"] == 2
    assert switched is True
