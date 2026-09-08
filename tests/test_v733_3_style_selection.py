# -*- coding: utf-8 -*-
"""v7.33.3 — content-style learning applied to segment selection."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import create_viral_segments as cvs
from scripts import performance_weights as pw


def _insights(overall=500.0):
    return {
        "with_metrics": 5,
        "correlations": {},
        "content_insights": {
            "overall_avg_views": overall,
            "categories": {
                "hook_type": [
                    {"value": "question", "samples": 3, "avg_views": 800.0,
                     "delta_pct": 60.0},
                    {"value": "story", "samples": 2, "avg_views": 200.0,
                     "delta_pct": -60.0},
                ],
                "topic": [
                    {"value": "money", "samples": 3, "avg_views": 700.0,
                     "delta_pct": 40.0},
                ],
            },
            "title_styles": [
                {"style": "title_question", "samples_yes": 3, "samples_no": 2,
                 "avg_views_yes": 800.0, "avg_views_no": 200.0, "delta_pct": 300.0},
            ],
        },
    }


def _write_insights(project):
    path = os.path.join(project, pw.INSIGHTS_NAME)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(_insights(), handle, ensure_ascii=False)
    return path


def test_title_style_flags():
    flags = pw.title_style_flags("كيف تربح من يوتيوب؟ السر الكامل")
    assert flags["title_question"] is True
    assert flags["title_number"] is False  # no digits in this title
    assert flags["title_hook_word"] is True
    flags_digits = pw.title_style_flags("5 أخطاء تدمر قناتك")
    assert flags_digits["title_number"] is True
    flags_plain = pw.title_style_flags("تجربتي في العمل الحر")
    assert flags_plain == {"title_question": False, "title_number": False,
                           "title_hook_word": False}


def test_style_bonus_is_bounded():
    assert pw._style_bonus(60.0) == 2.4
    assert pw._style_bonus(300.0) == 3.0   # capped
    assert pw._style_bonus(-300.0) == -3.0
    assert pw._style_bonus(10.0) == 0.0    # below the 15% floor
    assert pw._style_bonus(None) == 0.0


def test_style_bonuses_map_from_insights(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    _write_insights(str(project))
    weights = pw.load_weights(str(project))
    style = weights.get("style")
    assert style is not None
    assert style["hook_type"]["question"] == 2.4
    assert style["hook_type"]["story"] == -2.4
    assert style["topic"]["money"] == 1.6
    assert style["title_question"] == 3.0


def test_style_bonus_for_segment():
    style = {"hook_type": {"question": 2.4, "story": -2.4},
             "title_question": 3.0}
    winner = {"hook_type": "question", "angle": "lesson",
              "recommended_title": "كيف تربح من يوتيوب؟"}
    assert pw.style_bonus_for(winner, style) == pytest.approx(5.4)
    loser = {"hook_type": "story", "recommended_title": "تجربتي في العمل الحر"}
    assert pw.style_bonus_for(loser, style) == pytest.approx(-2.4)
    assert pw.style_bonus_for(winner, {}) == 0.0


def test_no_insights_means_no_style(tmp_path):
    project = tmp_path / "empty"
    project.mkdir()
    weights = pw.load_weights(str(project))
    assert "style" not in weights
    assert weights["basis"] == "defaults"


def test_process_segments_prefers_measured_winning_style(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    _write_insights(str(project))
    # Style bonuses must survive even with zero numeric correlations.
    transcript = [{"start": float(i * 6), "end": float(i * 6 + 5),
                   "text": "sentence number {}".format(i)} for i in range(8)]
    base = {"score": 70, "hook_strength": 70, "narrative_completeness": 70,
            "clarity_score": 70, "novelty_score": 50}
    question = dict(base, title="سؤال قوي؟", hook_type="question",
                    start_time=0.0, end_time=22.0)
    story = dict(base, title="قصة عادية", hook_type="story",
                 start_time=30.0, end_time=52.0)
    raw = [question, story]
    result = cvs.process_segments(raw, transcript, 15, 60,
                                  project_folder=str(project))
    by_title = {s["title"]: s for s in result["segments"]}
    assert by_title["سؤال قوي؟"]["selection_score"] > by_title["قصة عادية"]["selection_score"]
    assert by_title["سؤال قوي؟"]["selection_breakdown"]["style_bonus"] > 0
    assert by_title["قصة عادية"]["selection_breakdown"]["style_bonus"] < 0


def test_no_project_insights_keeps_scores_untouched():
    transcript = [{"start": float(i * 6), "end": float(i * 6 + 5),
                   "text": "sentence number {}".format(i)} for i in range(8)]
    candidate = {"score": 70, "hook_strength": 70, "narrative_completeness": 70,
                 "clarity_score": 70, "novelty_score": 50, "title": "سؤال قوي؟",
                 "hook_type": "question", "start_time": 0.0, "end_time": 22.0}
    result = cvs.process_segments([candidate], transcript, 15, 60)
    seg = result["segments"][0]
    assert "style_bonus" not in seg["selection_breakdown"]
    assert seg["selection_breakdown"]["performance_basis"] == "defaults"
