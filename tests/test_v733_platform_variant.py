# -*- coding: utf-8 -*-
"""v7.33 — cross-platform republish variance (TikTok/Reels duplicate defense)."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from scripts import platform_variant as pv


def _clip(tmp_path, name="000_clip.mp4"):
    path = tmp_path / "final" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-video-bytes-000")
    return str(path)


def _history(project_path, events):
    target = os.path.join(project_path, "publish_history.jsonl")
    os.makedirs(project_path, exist_ok=True)
    with open(target, "w", encoding="utf-8") as stream:
        for event in events:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def test_normalize_platform_and_variant_set():
    assert pv.is_variant_platform("tiktok") is True
    assert pv.is_variant_platform("reels") is True
    assert pv.is_variant_platform("youtube") is False
    assert pv.is_variant_platform("") is False


def test_prior_other_platform_publishes_finds_youtube_sibling(tmp_path):
    clip = _clip(tmp_path)
    events = [
        {"platform": "youtube", "status": "uploaded", "video": "000_clip.mp4",
         "file_fingerprint": "sha256:x", "timestamp": "2026-01-01T00:00:00Z"},
        {"platform": "tiktok", "status": "uploaded", "video": "000_clip.mp4",
         "file_fingerprint": "sha256:x", "timestamp": "2026-01-02T00:00:00Z"},
    ]
    siblings = pv.prior_other_platform_publishes(str(tmp_path), clip, "tiktok", events)
    assert len(siblings) == 1
    assert siblings[0]["platform"] == "youtube"


def test_policy_off_never_varies(tmp_path):
    clip = _clip(tmp_path)
    decision = pv.plan_variant(str(tmp_path), clip, "tiktok", policy="off")
    assert decision["action"] == "none"
    assert "variant_policy=off" in decision["reason"]


def test_auto_varies_only_when_other_platform_published(tmp_path):
    clip = _clip(tmp_path)
    # No prior publish anywhere: original must be kept.
    none_decision = pv.plan_variant(str(tmp_path), clip, "tiktok", policy="auto")
    assert none_decision["action"] == "none"

    events = [
        {"platform": "youtube", "status": "uploaded", "video": "000_clip.mp4",
         "file_fingerprint": "sha256:x"},
    ]
    decision = pv.plan_variant(str(tmp_path), clip, "tiktok", policy="auto", events=events)
    assert decision["action"] == "variate"
    assert decision["seed"] is not None
    assert isinstance(decision["seed"], int)
    assert decision["preset"] is not None


def test_seed_changes_per_attempt_and_platform(tmp_path):
    clip = _clip(tmp_path)
    events = [
        {"platform": "youtube", "status": "uploaded", "video": "000_clip.mp4",
         "file_fingerprint": "sha256:x"},
    ]
    first = pv.plan_variant(str(tmp_path), clip, "tiktok", policy="auto", events=events)
    second = pv.plan_variant(str(tmp_path), clip, "reels", policy="auto", events=events)
    # Same attempt count but different platform -> different seed.
    assert first["seed"] != second["seed"]
    # A second attempt at the same platform gets a fresh seed (attempt 2).
    again_events = events + [
        {"platform": "tiktok", "status": "uploaded", "video": "000_clip.mp4",
         "file_fingerprint": "sha256:y"},
    ]
    third = pv.plan_variant(str(tmp_path), clip, "tiktok", policy="auto",
                            events=again_events)
    assert third["attempt"] == 2
    assert third["seed"] != first["seed"]


def test_maybe_variant_renders_file(tmp_path, monkeypatch):
    clip = _clip(tmp_path)
    events = [
        {"platform": "youtube", "status": "uploaded", "video": "000_clip.mp4",
         "file_fingerprint": "sha256:x"},
    ]
    captured = {}

    def fake_transform(input_path, output_path, *, seed, preset, ffmpeg):
        captured["input"] = input_path
        captured["seed"] = seed
        with open(output_path, "wb") as stream:
            stream.write(b"variant-bytes")
        return {"ok": True, "transforms": ["mirror", "color"], "seed": seed}

    import scripts.originality as originality
    monkeypatch.setattr(originality, "transform_with_seed", fake_transform)
    decision = pv.maybe_variant(str(tmp_path), clip, "tiktok", policy="auto",
                                events=events)
    assert decision["action"] == "variate"
    assert captured["input"] == clip
    assert decision["path"] != clip
    assert os.path.isfile(decision["path"])
    assert decision["transforms"] == ["mirror", "color"]


def test_maybe_variant_falls_back_to_original_on_failure(tmp_path, monkeypatch):
    clip = _clip(tmp_path)
    events = [
        {"platform": "youtube", "status": "uploaded", "video": "000_clip.mp4",
         "file_fingerprint": "sha256:x"},
    ]

    import scripts.originality as originality

    def failing_transform(input_path, output_path, **kwargs):
        raise RuntimeError("ffmpeg missing")

    monkeypatch.setattr(originality, "transform_with_seed", failing_transform)
    decision = pv.maybe_variant(str(tmp_path), clip, "tiktok", policy="auto",
                                events=events)
    assert decision["action"] == "none"
    assert decision["path"] == clip
    assert "original" in decision["reason"]


def test_history_file_driven_detection(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    clip = _clip(project)
    _history(str(project), [
        {"platform": "youtube", "status": "uploaded", "video": "000_clip.mp4",
         "file_fingerprint": "sha256:x"},
    ])
    decision = pv.plan_variant(str(project), clip, "tiktok", policy="auto")
    assert decision["action"] == "variate"
