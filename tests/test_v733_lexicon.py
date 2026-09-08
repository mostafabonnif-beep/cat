# -*- coding: utf-8 -*-
"""v7.33 — policy-lexicon knowledge base: incremental sync + doctor."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import policy_lexicon


def test_second_sync_is_skipped_when_unchanged(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    first = policy_lexicon.sync_policy_db(str(project))
    assert first["synced"] is True
    assert first["terms_seen"] > 0

    second = policy_lexicon.sync_policy_db(str(project))
    assert second["synced"] is False
    assert second["reason"] == "unchanged"
    assert second["terms_seen"] == first["terms_seen"]

    # load_terms(sync=True) is the hot path used per analysis run: it must
    # not rebuild the FTS index when the term set is unchanged.
    terms = policy_lexicon.load_terms(str(project), sync=True)
    assert terms
    fts_rows = policy_lexicon._connect(str(project)).execute(
        "SELECT COUNT(*) AS count FROM policy_terms_fts_v2").fetchone()["count"]
    active_rows = policy_lexicon._connect(str(project)).execute(
        "SELECT COUNT(*) AS count FROM policy_terms WHERE active=1").fetchone()["count"]
    assert fts_rows == active_rows > 0


def test_force_sync_rebuilds(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    policy_lexicon.sync_policy_db(str(project))
    forced = policy_lexicon.sync_policy_db(str(project), force=True)
    assert forced["synced"] is True


def test_doctor_reports_conflicting_duplicates():
    terms = [
        {"term": "badword", "lang": "ar", "severity": "high",
         "category": "hate_slur", "source": "builtin"},
        {"term": "badword", "lang": "ar", "severity": "low",
         "category": "hate_slur", "source": "builtin"},
    ]
    report = policy_lexicon.run_doctor(terms=terms)
    assert report["ok"] is False
    assert report["duplicate_conflicts"] == 1
    assert any("severity" in problem for problem in report["problems"])


def test_doctor_accepts_clean_terms():
    terms = [
        {"term": "goodword", "lang": "ar", "severity": "high",
         "category": "hate_slur", "source": "builtin"},
        {"term": "another", "lang": "en", "severity": "medium",
         "category": "violence_threat", "source": "builtin"},
    ]
    report = policy_lexicon.run_doctor(terms=terms)
    assert report["ok"] is True
    assert report["unique"] == 2


def test_doctor_matches_database_state(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    policy_lexicon.sync_policy_db(str(project))
    report = policy_lexicon.run_doctor(str(project))
    # Built-in pack may contain legit data-quality conflicts — but the DB
    # state must always be internally consistent (FTS == active rows).
    assert report["active_terms"] == report["fts_rows"] > 0
    assert report["database"] is not None
