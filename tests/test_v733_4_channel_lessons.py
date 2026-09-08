# -*- coding: utf-8 -*-
"""v7.33.4 — channel-lessons summary + review-table quality markers."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webui import publish_panel as pp
from webui import segments_review


def _insights():
    return {
        "with_metrics": 5,
        "best_hours": [17, 21],
        "content_insights": {
            "overall_avg_views": 530.0,
            "categories": {
                "hook_type": [{"value": "question", "samples": 3,
                               "avg_views": 950.0, "delta_pct": 79.2}],
            },
            "title_styles": [{"style": "title_question", "samples_yes": 3,
                              "samples_no": 2, "avg_views_yes": 950.0,
                              "avg_views_no": 110.0, "delta_pct": 763.6}],
        },
    }


def test_lessons_without_insights_explains_setup(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    text = pp.format_channel_lessons(str(project))
    assert "لا يوجد ملف تحليلات" in text
    assert "performance_loop" in text


def test_lessons_summarize_winning_styles(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    with open(os.path.join(str(project), "performance_insights.json"),
              "w", encoding="utf-8") as handle:
        json.dump(_insights(), handle, ensure_ascii=False)
    text = pp.format_channel_lessons(str(project))
    assert "دروس قناتك" in text
    assert "question" in text or "سؤال" in text
    assert "79.2" in text
    assert "17, 21" in text


def test_lessons_never_raises_on_bad_json(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    with open(os.path.join(str(project), "performance_insights.json"),
              "w", encoding="utf-8") as handle:
        handle.write("{not json")
    assert "لا يوجد ملف تحليلات" in pp.format_channel_lessons(str(project))


def test_review_row_marks_quality_missing_segments():
    segments = [
        {"title": "A", "start_time": 1.0, "end_time": 10.0, "score": 90,
         "reasoning": "hook"},
        {"title": "B", "start_time": 20.0, "end_time": 30.0, "score": 80,
         "reasoning": "story", "quality_missing": True,
         "missing_components": ["hook_strength"]},
    ]
    rows = segments_review.rows_from_segments(segments)
    assert "hook" == rows[0][6]           # untouched when not flagged
    assert "التقييم التحريري" in rows[1][6]  # flagged segment says so
    assert "story" in rows[1][6]
