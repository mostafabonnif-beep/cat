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
