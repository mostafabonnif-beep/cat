"""Unit tests for the lightweight identity tracker (scripts/face_tracker.py)."""


from scripts.face_tracker import FaceTracker


def box(cx, cy, half=50):
    return [cx - half, cy - half, cx + half, cy + half]


def test_new_detections_get_fresh_ids():
    tr = FaceTracker()
    ids = tr.update(0, [box(100, 100), box(400, 100)])
    assert ids[0] == 0 and ids[1] == 1
    assert tr.track_count() == 2


def test_matching_keeps_ids_for_stable_boxes():
    tr = FaceTracker()
    ids1 = tr.update(0, [box(100, 100), box(400, 100)])
    # Both move slightly; matching by proximity must keep the same IDs.
    ids2 = tr.update(10, [box(110, 105), box(390, 95)])
    assert ids2 == ids1
    assert tr.last_ids == [0, 1]


def test_identity_does_not_swap_under_realistic_motion():
    # Two same-size faces moving side by side at realistic per-cycle steps
    # (~35px on a ~141px box diagonal): the tracker must keep each ID glued
    # to its face and never spawn duplicate tracks.
    tr = FaceTracker()
    ids0 = tr.update(0, [box(150, 100), box(450, 100)])
    assert ids0 == [0, 1]
    prev_a, prev_b = 150.0, 450.0
    for step in range(6):
        prev_a += 35
        prev_b += 35
        ids = tr.update(10 * (step + 1), [box(prev_a, 100), box(prev_b, 100)])
        assert ids == [0, 1], "IDs must stay stable while both faces are visible"
    assert tr.track_count() == 2


def test_order_reversal_after_gap_keeps_person_slots():
    # Face B disappears for a while (pipeline frames only A), then B returns.
    # Without an identity tracker the layout could resume with the faces in
    # swapped slots (crop jumps). The tracker must restore the old slot order.
    tr = FaceTracker(max_missing_cycles=3)
    tr.update(0, [box(150, 100), box(450, 100)])  # A=id0 left, B=id1 right
    tr.update(10, [box(155, 100)])                # only A visible
    tr.update(20, [box(160, 100)])
    tr.update(30, [box(165, 100)])
    ids = tr.update(40, [box(165, 100), box(455, 105)])  # B is back
    assert ids == [0, 1]  # same slots as before the gap
    # stable_order keeps the known pair in id order regardless of x-order.
    order = tr.stable_order(ids)
    assert order == [0, 1]


def test_missing_face_matches_back_at_predicted_position():
    tr = FaceTracker(velocity_alpha=0.5)
    tr.update(0, [box(100, 100)])
    tr.update(10, [box(140, 100)])   # moving right ~4px/cycle
    # Face missing for one cycle, reappears near the predicted position.
    ids = tr.update(30, [box(170, 100)])
    assert ids == [0]
    # A far-away new face is NOT the same identity.
    ids2 = tr.update(40, [box(800, 800)])
    assert ids2[0] != 0
    assert tr.track_count() == 2


def test_track_dies_after_max_missing_cycles():
    tr = FaceTracker(max_missing_cycles=2)
    tr.update(0, [box(100, 100)])
    ids = tr.update(10, [])
    assert ids == []
    assert tr.track_count() == 1
    tr.update(20, [])
    tr.update(30, [])
    assert tr.track_count() == 0
    # Returning face after death gets a brand-new id.
    ids = tr.update(40, [box(100, 100)])
    assert ids == [1]


def test_missing_track_survives_and_reacquires_same_id():
    tr = FaceTracker(max_missing_cycles=3)
    tr.update(0, [box(100, 100), box(500, 100)])
    # Second face gone for two cycles, first still tracked.
    ids = tr.update(10, [box(100, 100)])
    assert ids == [0]
    tr.update(20, [box(100, 100)])
    # Second face comes back -> same id (not a new person).
    ids = tr.update(30, [box(100, 100), box(510, 110)])
    assert ids == [0, 1]
    assert tr.track_count() == 2


def test_stable_order_known_first_then_new():
    tr = FaceTracker()
    ids = tr.update(0, [box(0, 0), box(500, 0)])       # ids 0,1
    ids = tr.update(10, [box(500, 0), box(0, 0), box(250, 400)])
    # ids: [1,0,2] (crossed) -> stable order keeps 0 before 1, new face last.
    order = tr.stable_order(ids)
    assert order == [1, 0, 2]
    order_ltr = tr.stable_order(ids, fallback_order=[0, 1, 2])
    assert order_ltr[2] == 2


def test_gate_rejects_distant_detection():
    tr = FaceTracker(match_gate=0.3)
    tr.update(0, [box(100, 100)])
    # Detection 500px away with ~141px box diagonal -> norm ~3.5 >> gate.
    ids = tr.update(10, [box(600, 100)])
    assert ids[0] != 0
    assert tr.track_count() == 2


def test_reset_clears_everything():
    tr = FaceTracker()
    tr.update(0, [box(100, 100)])
    tr.reset()
    assert tr.track_count() == 0
    assert tr.update(0, [box(100, 100)]) == [0]
