import json

from scripts import policy_lexicon


def test_sync_policy_lexicon_creates_auditable_sqlite(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    report = policy_lexicon.sync_policy_db(str(project))
    assert report["terms_seen"] > 0
    terms = policy_lexicon.load_terms(str(project), sync=False)
    assert terms
    assert any(item["source"] == "builtin" for item in terms)
    assert policy_lexicon.search_terms("hate", str(project))
    assert policy_lexicon.search_terms("term_that_does_not_exist", str(project)) == []
    assert report["database"].endswith(".oussama_policy_lexicon.sqlite3")
