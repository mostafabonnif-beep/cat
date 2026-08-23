from scripts import visual_hooks


def test_plan_visual_hooks_prioritizes_opening_and_emotional_words():
    hooks = visual_hooks.plan_visual_hooks([
        {"word": "wow", "start": 0.2, "end": 0.4},
        {"word": "ordinary", "start": 3.0, "end": 3.2},
        {"word": "danger", "start": 5.0, "end": 5.2},
    ])
    assert hooks
    assert hooks[0]["start"] == 0.12
    assert "hook_word" in hooks[0]["reason"]


def test_visual_hooks_dry_run_has_enable_windows(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    hooks = [{"start": 1.0, "end": 1.5, "word": "secret", "score": 3}]
    report = visual_hooks.apply_visual_hooks(
        str(video), str(tmp_path / "out.mp4"), hooks, dry_run=True,
    )
    assert report["ok"] is True
    assert report["count"] == 1
    command = " ".join(report["cmd"])
    assert "between(t,1.0,1.5)" in command
    assert "drawbox" in command


def test_visual_hooks_empty_plan_copies_video(tmp_path):
    video = tmp_path / "clip.mp4"
    output = tmp_path / "out.mp4"
    video.write_bytes(b"video")
    report = visual_hooks.apply_visual_hooks(str(video), str(output), [])
    assert report["ok"] is True
    assert report["count"] == 0
    assert output.read_bytes() == b"video"
