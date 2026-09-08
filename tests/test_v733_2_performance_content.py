# -*- coding: utf-8 -*-
"""v7.33.2 — content-aware performance insights (which titles/topics win)."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from scripts import performance_loop as pl


def _history_event(video_id, index, title, views=None, *, topic="", angle="",
                   hook_type="", hashtags=""):
    return {
        "timestamp": "2026-08-0{}T10:00:00Z".format((index % 9) + 1),
        "platform": "youtube",
        "video": "{:03d}_clip.mp4".format(index),
        "title": title,
        "status": "uploaded",
        "video_id": video_id,
        "file_fingerprint": "sha256:{}".format(video_id),
        "topic": topic,
        "angle": angle,
        "hook_type": hook_type,
        "hashtags": hashtags,
    }


def _project(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    events = [
        _history_event("vid-q1", 0, "كيف تربح من يوتيوب؟ السر الكامل",
                       topic="money", angle="lesson", hook_type="question",
                       hashtags="money,yt"),
        _history_event("vid-q2", 1, "لماذا يفشل معظم الناس؟ خطأ شائع",
                       topic="money", angle="mistake", hook_type="question",
                       hashtags="money"),
        _history_event("vid-n1", 2, "تجربتي مع العمل الحر",
                       topic="freelance", angle="story", hook_type="story",
                       hashtags=""),
        _history_event("vid-n2", 3, "قصة فشلي في التجارة",
                       topic="freelance", angle="story", hook_type="story",
                       hashtags=""),
    ]
    with open(os.path.join(str(project), "publish_history.jsonl"),
              "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return str(project)


def test_content_markers_extraction():
    markers = pl._content_markers(_history_event(
        "v", 0, "كيف تربح من يوتيوب؟ السر الكامل", topic="money",
        angle="lesson", hook_type="question", hashtags="a,b"))
    assert markers["topic"] == "money"
    assert markers["hook_type"] == "question"
    assert markers["title_question"] is True
    assert markers["hashtag_count"] == 2


def test_analyze_reports_content_insights(tmp_path, monkeypatch):
    project = _project(tmp_path)
    # Question-hook videos massively outperform the story one.
    monkeypatch.setattr(pl, "_fetch_real_metrics", lambda ids: {
        "vid-q1": {"views": 1000, "avg_view_duration": 30.0, "likes": 10, "shares": 2},
        "vid-q2": {"views": 900, "avg_view_duration": 28.0, "likes": 8, "shares": 1},
        "vid-n1": {"views": 100, "avg_view_duration": 15.0, "likes": 1, "shares": 0},
        "vid-n2": {"views": 120, "avg_view_duration": 16.0, "likes": 1, "shares": 0},
    })
    report = pl.analyze(project, fetch_live=True)
    content = report["content_insights"]
    assert content["overall_avg_views"] == pytest.approx(530.0, abs=0.1)

    hook_types = {row["value"]: row for row in content["categories"]["hook_type"]}
    assert hook_types["question"]["samples"] == 2
    assert hook_types["question"]["delta_pct"] > 0
    assert hook_types["story"]["delta_pct"] < 0

    styles = {row["style"]: row for row in content["title_styles"]}
    assert styles["title_question"]["samples_yes"] == 2
    assert styles["title_question"]["samples_no"] == 2
    assert styles["title_question"]["delta_pct"] > 0

    topics = {row["value"]: row for row in content["categories"]["topic"]}
    assert topics["money"]["samples"] == 2
    assert topics["freelance"]["delta_pct"] < 0

    # Human insight lines were produced too.
    text = "\n".join(report["insights"])
    assert "hook_type=question" in text
    assert "question titles" in text


def test_analyze_without_content_markers_is_clean(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    events = [
        {"timestamp": "2026-08-01T10:00:00Z", "platform": "youtube",
         "video": "000_clip.mp4", "title": "Plain", "status": "uploaded",
         "video_id": "v1", "file_fingerprint": "sha256:x"},
        {"timestamp": "2026-08-02T10:00:00Z", "platform": "youtube",
         "video": "001_clip.mp4", "title": "Plain Two", "status": "uploaded",
         "video_id": "v2", "file_fingerprint": "sha256:y"},
        {"timestamp": "2026-08-03T10:00:00Z", "platform": "youtube",
         "video": "002_clip.mp4", "title": "Plain Three", "status": "uploaded",
         "video_id": "v3", "file_fingerprint": "sha256:z"},
    ]
    with open(os.path.join(str(project), "publish_history.jsonl"),
              "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    monkeypatch.setattr(pl, "_fetch_real_metrics", lambda ids: {
        "v1": {"views": 50, "avg_view_duration": 10.0, "likes": 1, "shares": 0},
        "v2": {"views": 60, "avg_view_duration": 11.0, "likes": 1, "shares": 0},
        "v3": {"views": 70, "avg_view_duration": 12.0, "likes": 1, "shares": 0},
    })
    report = pl.analyze(project, fetch_live=True)
    assert report["content_insights"]["overall_avg_views"] == pytest.approx(60.0)
    assert "categories" not in report["content_insights"]
    assert "title_styles" not in report["content_insights"]
