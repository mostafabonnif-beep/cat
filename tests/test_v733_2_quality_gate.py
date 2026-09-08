# -*- coding: utf-8 -*-
"""v7.33.2 — editorial quality gate for genuinely weak candidates."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import create_viral_segments as cvs


def _config(**overrides):
    base = {"enabled": True,
            "min": {"hook_strength": 20.0, "narrative_completeness": 20.0,
                    "clarity_score": 20.0}}
    for key, value in overrides.items():
        if key == "min":
            base["min"].update(value)
        else:
            base[key] = value
    return base


def _candidate(title, hook, narrative, clarity, **extra):
    seg = {"title": title, "start_time": 10.0, "end_time": 40.0, "score": 80,
           "hook_strength": hook, "narrative_completeness": narrative,
           "clarity_score": clarity}
    seg.update(extra)
    return seg


def test_gate_drops_genuinely_weak_candidate():
    strong = _candidate("Strong", 85, 80, 75)
    weak = _candidate("Weak", 10, 8, 5)
    kept, dropped = cvs.apply_quality_gate([strong, weak], _config())
    assert [s["title"] for s in kept] == ["Strong"]
    assert len(dropped) == 1
    assert dropped[0]["title"] == "Weak"
    assert any("hook" in r for r in dropped[0]["reasons"])


def test_gate_keeps_quality_missing_candidates():
    # No genuine self-evaluation shipped → components are unverified copies
    # of the virality score; gating them would be guessing.
    unverified = _candidate("NoEval", None, None, None)
    unverified["quality_missing"] = True
    kept, dropped = cvs.apply_quality_gate([unverified], _config())
    assert [s["title"] for s in kept] == ["NoEval"]
    assert dropped == []


def test_gate_disabled_keeps_everything(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_QUALITY_GATE", "0")
    weak = _candidate("Weak", 5, 5, 5)
    kept, dropped = cvs.apply_quality_gate([weak])
    assert [s["title"] for s in kept] == ["Weak"]
    assert dropped == []


def test_gate_floor_override_via_env(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_MIN_HOOK", "95")
    config = cvs.quality_gate_config()
    assert config["min"]["hook_strength"] == 95.0
    candidate = _candidate("GoodHook", 90, 90, 90)
    kept, dropped = cvs.apply_quality_gate([candidate], config)
    assert kept == []
    assert len(dropped) == 1


def test_process_segments_integrates_gate():
    transcript = [{"start": float(i * 5), "end": float(i * 5 + 4),
                   "text": "sentence number {}".format(i)} for i in range(8)]
    raw = [
        _candidate("Strong", 85, 80, 75, start_time=0.0, end_time=20.0),
        _candidate("Weak", 10, 8, 5, start_time=25.0, end_time=39.0),
        {"title": "NoEval", "start_time": 25.0, "end_time": 39.0, "score": 70},
    ]
    result = cvs.process_segments(raw, transcript, 10, 60,
                                  snap_to_boundaries=False)
    titles = [s["title"] for s in result["segments"]]
    assert "Weak" not in titles
    assert "Strong" in titles
    assert "NoEval" in titles


def test_process_segments_gate_can_be_disabled(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_QUALITY_GATE", "0")
    transcript = [{"start": float(i * 5), "end": float(i * 5 + 4),
                   "text": "sentence number {}".format(i)} for i in range(8)]
    raw = [_candidate("Weak", 10, 8, 5, start_time=0.0, end_time=20.0)]
    result = cvs.process_segments(raw, transcript, 10, 60,
                                  snap_to_boundaries=False)
    assert [s["title"] for s in result["segments"]] == ["Weak"]
