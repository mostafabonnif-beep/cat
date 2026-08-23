# -*- coding: utf-8 -*-
"""Tests for the v7.18 multi-source blocklist merge + watch helpers."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import safety_updater as su
from scripts import youtube_policy_watch as yw


def _term(term, severity="high", lang="test"):
    return {"term": term, "lang": lang, "severity": severity, "category": "test"}


def _pack(version, terms, source="test"):
    return {"version": version, "updated": "2026-08-20", "source": source,
            "terms": terms}


class TestMergePacks:
    def test_single_pack_passthrough(self):
        merged = su.merge_packs([_pack(3, [_term("a")])])
        assert merged["version"] == 3 and len(merged["terms"]) == 1

    def test_merges_union_of_terms(self):
        merged = su.merge_packs([
            _pack(1, [_term("a"), _term("b")]),
            _pack(2, [_term("c")]),
        ])
        assert len(merged["terms"]) == 3
        assert merged["version"] == 2

    def test_highest_severity_wins_per_term(self):
        merged = su.merge_packs([
            _pack(1, [_term("x", severity="low")]),
            _pack(2, [_term("x", severity="high")]),
        ])
        terms = {t["term"]: t for t in merged["terms"]}
        assert terms["x"]["severity"] == "high"

    def test_deduplicates_same_term_and_lang(self):
        merged = su.merge_packs([
            _pack(1, [_term("word", lang="ar"), _term("word", lang="en")]),
            _pack(2, [_term("word", lang="ar")]),
        ])
        assert len(merged["terms"]) == 2  # one ar + one en

    def test_skips_none_packs(self):
        merged = su.merge_packs([None, _pack(1, [_term("a")]), None])
        assert len(merged["terms"]) == 1

    def test_empty_input(self):
        merged = su.merge_packs([])
        assert merged["version"] == 0 and merged["terms"] == []


class TestMultiSourceUpdate:
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

    def test_multi_source_merge_updates(self, tmp_path):
        base = self._cache(tmp_path)  # no cache → first run
        url_a = self._pack_path(tmp_path, _pack(1, [_term("from-a")]), "a.json")
        url_b = self._pack_path(tmp_path, _pack(1, [_term("from-b")]), "b.json")
        r = su.check_and_update(base_dir=base, force=True,
                                extra_urls=[url_b], url=url_a)
        assert r["status"] == "updated"
        cached = su.load_cached_pack(base)
        terms = {t["term"] for t in cached["terms"]}
        assert "from-a" in terms and "from-b" in terms

    def test_explicit_unreachable_url_is_offline(self, tmp_path):
        # Single-source contract: an explicit URL that cannot be fetched
        # reports offline (no surprise mirror fallback).
        base = self._cache(tmp_path, version=2)
        r = su.check_and_update(base_dir=base, url="file:///nonexistent/x.json",
                                force=True)
        assert r["status"] == "offline"
        assert su.load_cached_pack(base)["version"] == 2


class TestWatch:
    def test_watch_bounded_cycles(self, tmp_path, monkeypatch):
        base = tmp_path / "base"
        base.mkdir()
        calls = {"n": 0}

        def fake_check(base_dir=None, force=False):
            calls["n"] += 1
            return {"status": "offline", "message": "no network in test"}

        monkeypatch.setattr(su, "check_and_update", fake_check)
        monkeypatch.setattr(su.time, "sleep", lambda s: None)
        count = su.watch(base_dir=str(base), interval_hours=1, max_cycles=3)
        assert count == 3 and calls["n"] == 3


class TestPolicyWatch:
    def test_load_feed_missing(self, tmp_path):
        assert yw.load_feed(str(tmp_path)) == {}

    def test_load_feed_corrupt(self, tmp_path):
        (tmp_path / yw.FEED_FILENAME).write_text("{nope", encoding="utf-8")
        assert yw.load_feed(str(tmp_path)) == {}

    def test_check_policy_offline_graceful(self, tmp_path, monkeypatch):
        def fake_fetch(url):
            raise OSError("no network in test")
        monkeypatch.setattr(yw, "_fetch_text", fake_fetch)
        result = yw.check_policy_pages(str(tmp_path))
        assert result["status"] == "offline"

    def test_policy_change_detected(self, tmp_path, monkeypatch):
        pages = {}
        monkeypatch.setattr(
            yw, "_fetch_text",
            lambda url: pages.setdefault(url, "<html><body>v1 rules</body></html>"))
        first = yw.check_policy_pages(str(tmp_path))
        assert first["status"] == "unchanged"
        # mutate one page
        pages[yw.POLICY_PAGES["hate_speech"]] = "<html><body>v2 new rules</body></html>"
        second = yw.check_policy_pages(str(tmp_path))
        assert second["status"] == "changed"
        assert "hate_speech" in second["changes"]

    def test_feed_written(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            yw, "_fetch_text",
            lambda url: "<html><body>stable</body></html>")
        yw.check_policy_pages(str(tmp_path))
        assert os.path.exists(tmp_path / yw.FEED_FILENAME)
