import pytest

from scripts import review_decisions, review_queue


def test_decision_roundtrip_and_queue_reflects_it(tmp_path):
    project = str(tmp_path)
    assert review_decisions.record_decision(project, 2, "reject", reasons=[{"code": "hate_slur"}])
    decisions = review_decisions.load_decisions(project)
    assert decisions[0]["decision"] == "reject"
    assert decisions[0]["reasons"][0]["code"] == "hate_slur"
    rules = review_decisions.learned_rules(project)
    assert rules["decision_counts"]["reject"] == 1
    assert rules["reject_terms"]["hate_slur"] == 1
    (tmp_path / "risk_scorecard.json").write_text('{"segments": [{"index": 2, "title": "X", "overall_score": 80, "overall": "high"}]}', encoding="utf-8")
    queue = review_queue.build_queue(project)
    entry = [item for item in queue["clips"] if item["index"] == 2][0]
    assert entry["prior_decision"] == "reject"


def test_invalid_decision_rejected(tmp_path):
    with pytest.raises(ValueError):
        review_decisions.record_decision(str(tmp_path), 0, "maybe")
