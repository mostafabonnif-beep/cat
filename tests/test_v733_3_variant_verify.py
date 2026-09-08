# -*- coding: utf-8 -*-
"""v7.33.3 — variant renders are verified visually distinct."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from scripts import platform_variant as pv


def _clip(tmp_path):
    path = tmp_path / "final" / "000_clip.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-video-bytes")
    return str(path)


def _youtube_sibling_event():
    return [{"platform": "youtube", "status": "uploaded",
             "video": "000_clip.mp4", "file_fingerprint": "sha256:x"}]


def _seed_of(output_path):
    return int(os.path.splitext(os.path.basename(output_path))[0].split("_")[-1])


def _install_fakes(monkeypatch, tmp_path, similarities_by_offset):
    """Patch originality so transforms write files and compare is scripted.

    ``similarities_by_offset`` maps attempt offset -> similarity value.
    """
    import scripts.originality as originality

    def fake_transform(input_path, output_path, *, seed, preset, ffmpeg):
        with open(output_path, "wb") as stream:
            stream.write("variant-{}".format(seed).encode())
        return {"ok": True, "transforms": ["mirror"], "seed": seed}

    def fake_compare(path_a, path_b):
        # compare against the base seed recorded from plan_variant is complex;
        # simpler: return from queue keyed by call count.
        fake_compare.calls += 1
        similarity = fake_compare.queue[min(fake_compare.calls - 1,
                                            len(fake_compare.queue) - 1)]
        return {"similarity": similarity, "verdict": "similar"}

    fake_compare.calls = 0
    fake_compare.queue = list(similarities_by_offset)
    monkeypatch.setattr(originality, "transform_with_seed", fake_transform)
    monkeypatch.setattr(originality, "compare_clips", fake_compare)
    return fake_compare


def test_variant_retries_until_distinct(tmp_path, monkeypatch):
    clip = _clip(tmp_path)
    compare = _install_fakes(monkeypatch, tmp_path, [0.95, 0.95, 0.30])
    decision = pv.maybe_variant(str(tmp_path), clip, "tiktok", policy="auto",
                                events=_youtube_sibling_event())
    assert compare.calls == 3          # two too-similar + one accepted
    assert decision["action"] == "variate"
    assert decision["similarity"] == 0.30
    assert os.path.isfile(decision["path"])
    # the accepted render is the third attempt
    assert "tiktok" in os.path.basename(decision["path"])


def test_first_distinct_variant_wins_immediately(tmp_path, monkeypatch):
    clip = _clip(tmp_path)
    compare = _install_fakes(monkeypatch, tmp_path, [0.10])
    decision = pv.maybe_variant(str(tmp_path), clip, "tiktok", policy="auto",
                                events=_youtube_sibling_event())
    assert compare.calls == 1
    assert decision["similarity"] == 0.10
    assert decision["action"] == "variate"


def test_all_similar_returns_best_effort_with_warning(tmp_path, monkeypatch):
    clip = _clip(tmp_path)
    compare = _install_fakes(monkeypatch, tmp_path,
                             [0.9, 0.91, 0.89, 0.92])
    decision = pv.maybe_variant(str(tmp_path), clip, "tiktok", policy="auto",
                                events=_youtube_sibling_event())
    assert compare.calls == 4          # all VERIFY_ATTEMPTS exhausted
    assert decision["action"] == "variate"          # never hard-blocked
    assert decision["similarity"] == pytest.approx(0.92)
    assert "best-effort" in decision["reason"]


def test_verify_can_be_disabled(tmp_path, monkeypatch):
    clip = _clip(tmp_path)
    monkeypatch.setenv("VIRALCUTTER_VARIANT_VERIFY", "0")
    compare = _install_fakes(monkeypatch, tmp_path, [0.99])
    decision = pv.maybe_variant(str(tmp_path), clip, "tiktok", policy="auto",
                                events=_youtube_sibling_event())
    assert compare.calls == 0          # verification skipped entirely
    assert decision["action"] == "variate"
    assert "similarity" not in decision
