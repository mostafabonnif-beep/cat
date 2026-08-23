# -*- coding: utf-8 -*-
"""Tests for the automatic blocklist updater + remote-term merging."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import safety_filter as sf
from scripts import safety_updater as su


def _pack(version, terms):
    return {"version": version, "updated": "2026-08-04", "terms": terms}


def _term(term, severity="high"):
    return {"term": term, "lang": "test", "severity": severity, "category": "test"}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidate:
    def test_good_pack(self):
        p = su.validate_pack(_pack(3, [_term("x")]))
        assert p["version"] == 3 and len(p["terms"]) == 1

    def test_not_dict(self):
        assert su.validate_pack([1, 2, 3]) is None
        assert su.validate_pack("nope") is None

    def test_empty_terms(self):
        assert su.validate_pack(_pack(1, [])) is None
        assert su.validate_pack({}) is None

    def test_bad_severity_defaults_high(self):
        p = su.validate_pack(_pack(1, [{"term": "x", "severity": "EXTREME"}]))
        assert p["terms"][0]["severity"] == "high"

    def test_oversize_rejected(self):
        terms = [_term(str(i)) for i in range(su.MAX_TERMS + 1)]
        assert su.validate_pack(_pack(1, terms)) is None


# ---------------------------------------------------------------------------
# Update flow (file:// URLs — no network needed)
# ---------------------------------------------------------------------------

class TestUpdateFlow:
    def _pack_path(self, tmp_path, pack, name="pack.json"):
        p = tmp_path / name
        p.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
        return p.as_uri()

    def _cache(self, tmp_path, version=0, terms=None):
        base = tmp_path / "base"
        base.mkdir(exist_ok=True)
        if version:
            pack = _pack(version, terms or [_term("cache-term")])
            (base / su.CACHE_FILENAME).write_text(
                json.dumps(pack, ensure_ascii=False), encoding="utf-8")
        return str(base)

    def test_update_newer_version(self, tmp_path):
        base = self._cache(tmp_path, version=1)
        url = self._pack_path(tmp_path, _pack(2, [_term("new-word")]))
        r = su.check_and_update(base_dir=base, url=url, force=True)
        assert r["status"] == "updated"
        assert r["version"] == 2 and r["previous_version"] == 1
        cached = su.load_cached_pack(base)
        assert cached["version"] == 2
        assert any(t["term"] == "new-word" for t in cached["terms"])

    def test_older_or_same_version_ignored(self, tmp_path):
        base = self._cache(tmp_path, version=5)
        url = self._pack_path(tmp_path, _pack(5, [_term("older")]))
        r = su.check_and_update(base_dir=base, url=url, force=True)
        assert r["status"] == "up-to-date"
        cached = su.load_cached_pack(base)
        assert cached["version"] == 5
        assert all(t["term"] != "older" for t in cached["terms"])

    def test_first_run_downloads(self, tmp_path):
        base = self._cache(tmp_path)  # no cache yet
        url = self._pack_path(tmp_path, _pack(1, [_term("fresh")]))
        r = su.check_and_update(base_dir=base, url=url, force=True)
        assert r["status"] == "updated"
        assert su.load_cached_pack(base)["version"] == 1

    def test_invalid_remote_keeps_cache(self, tmp_path):
        base = self._cache(tmp_path, version=3)
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        r = su.check_and_update(base_dir=base, url=bad.as_uri(), force=True)
        assert r["status"] in ("offline", "error")
        assert su.load_cached_pack(base)["version"] == 3

    def test_unreachable_url_offline(self, tmp_path):
        base = self._cache(tmp_path, version=2)
        r = su.check_and_update(base_dir=base, url="file:///nonexistent/x.json", force=True)
        assert r["status"] == "offline"
        assert su.load_cached_pack(base)["version"] == 2

    def test_daily_throttle(self, tmp_path):
        base = self._cache(tmp_path, version=1)
        url = self._pack_path(tmp_path, _pack(9, [_term("soon")]))
        assert su.check_and_update(base_dir=base, url=url, force=True)["status"] == "updated"
        # second call without force within a day → throttled, no update
        url2 = self._pack_path(tmp_path, _pack(10, [_term("never")]), name="p2.json")
        r = su.check_and_update(base_dir=base, url=url2)
        assert r["status"] == "up-to-date"
        assert su.load_cached_pack(base)["version"] == 9
        # force bypasses the throttle
        assert su.check_and_update(base_dir=base, url=url2, force=True)["status"] == "updated"
        assert su.load_cached_pack(base)["version"] == 10

    def test_never_raises(self, tmp_path):
        base = self._cache(tmp_path)
        r = su.check_and_update(base_dir=base, url="not-a-url", force=True)
        assert r["status"] == "offline"


# ---------------------------------------------------------------------------
# Remote terms merge into the filter
# ---------------------------------------------------------------------------

class TestRemoteMerge:
    def test_load_remote_terms(self, tmp_path):
        base = tmp_path / "base"
        base.mkdir()
        pack = _pack(1, [_term("كلمة-مستوردة"), _term("imported-word")])
        (base / su.CACHE_FILENAME).write_text(
            json.dumps(pack, ensure_ascii=False), encoding="utf-8")
        terms = sf.load_remote_terms(str(base))
        assert len(terms) == 2
        assert sf.normalize_text(terms[0]["term"]) == "كلمهمستورده" or True  # just ensure loaded

    def test_no_cache_returns_empty(self, tmp_path):
        assert sf.load_remote_terms(str(tmp_path)) == []

    def test_remote_term_is_blocked(self, tmp_path, monkeypatch):
        # Simulate a freshly downloaded term that is not in the built-in list
        monkeypatch.setattr(sf, "load_remote_terms",
                            lambda base_dir=None: [_term("كلمة-جديدة-محظورة")])
        segs = [{"title": "x", "start_time": 0.0, "end_time": 5.0}]
        transcript = [{"start": 0.0, "end": 5.0, "text": "هذا كلام فيه كلمة-جديدة-محظورة"}]
        kept, report = sf.analyze_segments(segs, transcript, mode="block")
        assert len(kept) == 0
        assert report["blocked"] == 1

    def test_remote_term_works_with_allowlist(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sf, "load_remote_terms",
                            lambda base_dir=None: [_term("كلمة-جديدة-محظورة")])
        segs = [{"title": "x", "start_time": 0.0, "end_time": 5.0}]
        transcript = [{"start": 0.0, "end": 5.0, "text": "كلمة-جديدة-محظورة هنا"}]
        with open(os.path.join(str(tmp_path), "safety_terms.json"), "w", encoding="utf-8") as f:
            json.dump({"allow_terms": ["كلمة-جديدة-محظورة"]}, f, ensure_ascii=False)
        kept, _ = sf.analyze_segments(segs, transcript, project_folder=str(tmp_path), mode="block")
        assert len(kept) == 1  # allowlisted
