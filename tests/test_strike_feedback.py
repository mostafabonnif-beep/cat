"""Tests for the strike feedback loop (scripts/strike_feedback.py, Roadmap 5.1)."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import strike_feedback as sf


@pytest.fixture()
def base(tmp_path):
    return str(tmp_path)


def test_add_creates_term_and_journal(base):
    path, entry = sf.cmd_add("كلمة خطيرة", lang="ar", severity="high",
                             reason="strike on video X", base_dir=base)
    assert os.path.exists(path)
    assert os.path.exists(sf.journal_path(base))
    terms = sf.load_terms(base)
    assert terms["extra_terms"][0]["term"] == "كلمة خطيرة"
    journal = sf.load_journal(base)
    assert journal["events"][0]["action"] == "add"
    assert journal["events"][0]["reason"] == "strike on video X"


def test_add_dedupe(base):
    sf.cmd_add("term", base_dir=base)
    sf.cmd_add("TERM", base_dir=base)  # case-insensitive dup
    terms = sf.load_terms(base)
    assert len(terms["extra_terms"]) == 1


def test_add_invalid_severity(base):
    with pytest.raises(ValueError):
        sf.cmd_add("x", severity="extreme", base_dir=base)


def test_allow_and_remove(base):
    sf.cmd_add("blocked-word", base_dir=base)
    sf.cmd_allow("منغولي", reason="history channel", base_dir=base)
    terms = sf.load_terms(base)
    assert "منغولي" in terms["allow_terms"]

    assert sf.cmd_remove("BLOCKED-WORD", base_dir=base) is True
    assert sf.cmd_remove("blocked-word", base_dir=base) is False  # gone
    terms = sf.load_terms(base)
    assert terms["extra_terms"] == []

    assert sf.cmd_remove("منغولي", allow=True, base_dir=base) is True
    assert sf.load_terms(base)["allow_terms"] == []


def test_list_and_stats(base):
    sf.cmd_add("a", severity="high", base_dir=base)
    sf.cmd_add("b", severity="medium", base_dir=base)
    sf.cmd_allow("c", base_dir=base)
    stats = sf.cmd_stats(base)
    assert stats["events"] == 3
    assert stats["by_action"] == {"add": 2, "allow": 1}
    assert stats["by_severity"] == {"high": 1, "medium": 1}
    assert stats["active_extra_terms"] == 2
    assert stats["active_allow_terms"] == 1
    terms = sf.cmd_list(base)
    assert len(terms["extra_terms"]) == 2


def test_load_terms_handles_corrupt(base):
    with open(os.path.join(base, sf.TERMS_FILE), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert sf.load_terms(base) == {"extra_terms": [], "allow_terms": []}


def test_atomic_write_keeps_no_tmp(base):
    sf.cmd_add("term", base_dir=base)
    leftovers = [f for f in os.listdir(base) if f.endswith(".tmp")]
    assert leftovers == []


def test_extract_terms_from_project(base):
    project = os.path.join(base, "proj")
    os.makedirs(project)
    # safety report with a blocked segment match
    safety = {"segments": [
        {"title": "seg1", "status": "blocked",
         "matches": [{"term": "كلمة سيئة", "severity": "high"}]},
        {"title": "seg2", "status": "kept",
         "matches": [{"term": "كلمة سيئة", "severity": "high"}]},
    ]}
    with open(os.path.join(project, "safety_report.json"), "w", encoding="utf-8") as fh:
        json.dump(safety, fh, ensure_ascii=False)
    # scorecard blocking seg1 (index 0)
    scorecard = {"segments": [
        {"index": 0, "title": "seg1", "overall": "danger",
         "axes": {"text": {"first7s": {"terms": ["bad-first7"]}}}},
    ], "blocked": [{"index": 0, "title": "seg1", "overall": "danger"}]}
    with open(os.path.join(project, "risk_scorecard.json"), "w", encoding="utf-8") as fh:
        json.dump(scorecard, fh, ensure_ascii=False)

    found = sf.extract_terms_from_project(project)
    terms = {f["term"] for f in found}
    assert "كلمة سيئة" in terms
    assert "bad-first7" in terms
    by_term = {f["term"]: f for f in found}
    # both segments matched the term → counted twice (blocked + kept alike)
    assert by_term["كلمة سيئة"]["count"] == 2


def test_from_scorecard_apply(base):
    project = os.path.join(base, "proj2")
    os.makedirs(project)
    with open(os.path.join(project, "safety_report.json"), "w", encoding="utf-8") as fh:
        json.dump({"segments": [
            {"title": "s", "status": "blocked",
             "matches": [{"term": "learned-word", "severity": "medium"}]}]}, fh)
    found = sf.extract_terms_from_project(project)
    assert found and found[0]["term"] == "learned-word"
    for f in found:
        sf.cmd_add(f["term"], severity=f["severity"], category="learned",
                   source="scorecard", project=project, base_dir=base)
    terms = sf.load_terms(base)
    assert any(t["term"] == "learned-word" and t["category"] == "learned"
               for t in terms["extra_terms"])


def test_extract_no_reports(base):
    assert sf.extract_terms_from_project(base) == []


def test_main_cli(base, capsys):
    assert sf.main(["--dir", base, "add", "--term", "t1"]) == 0
    assert sf.main(["--dir", base, "list"]) == 0
    assert sf.main(["--dir", base, "stats"]) == 0
    assert sf.main(["--dir", base, "export", "--format", "txt"]) == 0
    assert sf.main(["--dir", base, "export", "--format", "json"]) == 0
    assert sf.main(["--dir", base, "remove", "--term", "t1"]) == 0
    assert sf.main(["--dir", base, "remove", "--term", "t1"]) == 1  # gone
