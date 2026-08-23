from scripts import auto_sfx


def test_plan_sfx_maps_emotional_words_and_deduplicates():
    events = auto_sfx.plan_sfx([
        {"word": "wow", "start": 0.2, "end": 0.4},
        {"word": "amazing", "start": 0.4, "end": 0.6},
        {"word": "secret", "start": 2.0, "end": 2.2},
    ])
    assert [event["effect"] for event in events] == ["pop", "whoosh"]
    assert events[0]["start"] == 0.2


def test_find_asset_accepts_supported_extensions(tmp_path):
    sfx_dir = tmp_path / "sfx"
    sfx_dir.mkdir()
    (sfx_dir / "pop.wav").write_bytes(b"audio")
    assert auto_sfx.find_asset(str(sfx_dir), "pop").endswith("pop.wav")
    assert auto_sfx.find_asset(str(sfx_dir), "impact") is None


def test_apply_auto_sfx_without_assets_copies_video(tmp_path):
    video = tmp_path / "clip.mp4"
    output = tmp_path / "out.mp4"
    video.write_bytes(b"video")
    report = auto_sfx.apply_auto_sfx(
        str(video), str(output), [{"start": 1, "effect": "pop"}], str(tmp_path / "missing")
    )
    assert report["ok"] is True
    assert report["count"] == 0
    assert output.read_bytes() == b"video"


def test_apply_auto_sfx_dry_run_contains_delays(tmp_path):
    video = tmp_path / "clip.mp4"
    sfx_dir = tmp_path / "sfx"
    video.write_bytes(b"video")
    sfx_dir.mkdir()
    (sfx_dir / "pop.wav").write_bytes(b"audio")
    report = auto_sfx.apply_auto_sfx(
        str(video), str(tmp_path / "out.mp4"),
        [{"start": 1.25, "effect": "pop"}], str(sfx_dir), dry_run=True,
    )
    assert report["ok"] is True
    assert report["count"] == 1
    assert "adelay=1250|1250" in " ".join(report["cmd"])
