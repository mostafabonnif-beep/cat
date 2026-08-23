from scripts.create_viral_segments import (
    _choose_recommended_title,
    _rank_segments_with_diversity,
    _selection_score,
    deduplicate_segments,
    process_segments,
)


def test_selection_score_is_bounded_and_explainable():
    score, breakdown = _selection_score({
        "score": 90,
        "hook_strength": 80,
        "narrative_completeness": 70,
        "clarity_score": 85,
        "novelty_score": 60,
    })
    assert 0 <= score <= 100
    assert set(breakdown) == {"virality", "hook", "completeness", "clarity", "novelty"}
    assert breakdown["virality"] == 90


def test_diversity_ranking_prefers_new_topic_after_first_pick():
    segments = [
        {"title": "A", "topic": "same", "angle": "story", "selection_score": 95},
        {"title": "B", "topic": "same", "angle": "story", "selection_score": 94},
        {"title": "C", "topic": "new", "angle": "lesson", "selection_score": 92},
    ]
    ranked = _rank_segments_with_diversity(segments, 3)
    assert [item["title"] for item in ranked] == ["A", "C", "B"]
    assert [item["candidate_rank"] for item in ranked] == [1, 2, 3]


def test_process_segments_drops_same_window_with_different_titles():
    transcript = [
        {"start": 0.0, "end": 10.0, "text": "opening statement"},
        {"start": 10.0, "end": 20.0, "text": "important conclusion"},
        {"start": 20.0, "end": 30.0, "text": "later detail"},
    ]
    raw = [
        {"title": "Hook A", "start_time_ref": "0s", "start_text": "opening statement",
         "end_text": "important conclusion", "score": 95},
        {"title": "Hook B", "start_time_ref": "0s", "start_text": "opening statement",
         "end_text": "important conclusion", "score": 94},
    ]
    result = process_segments(raw, transcript, 5, 30)
    assert len(result["segments"]) == 1
    assert result["segments"][0]["title"] == "Hook A"


def test_process_segments_respects_explicit_numeric_window():
    transcript = [{"start": float(i), "end": float(i + 1), "text": f"word {i}"} for i in range(0, 61, 5)]
    raw = [
        {"title": "First", "start_time": 10, "end_time": 20, "score": 90},
        {"title": "Second", "start_time": 40, "end_time": 50, "score": 80},
    ]
    result = process_segments(raw, transcript, 5, 30)
    windows = {(round(item["start_time"]), round(item["end_time"])) for item in result["segments"]}
    assert windows == {(10, 20), (40, 50)}


def test_deduplicate_segments_keeps_highest_score_for_same_window():
    segments = [
        {"title": "strong", "start_time": 10, "end_time": 30, "score": 95},
        {"title": "same footage", "start_time": 10.5, "end_time": 29.5, "score": 99},
        {"title": "different", "start_time": 50, "end_time": 70, "score": 80},
    ]
    result = deduplicate_segments(segments)
    assert len(result) == 2
    assert {item["title"] for item in result} == {"same footage", "different"}


def test_recommended_title_prefers_readable_candidate():
    segment = {
        "title": "THIS IS A VERY LONG TITLE THAT SHOULD NOT BE THE DEFAULT " * 2,
        "alt_titles": ["كيف تغيّرت النتيجة في لحظة؟", "نتيجة مفاجئة"],
    }
    assert _choose_recommended_title(segment) == "كيف تغيّرت النتيجة في لحظة؟"
