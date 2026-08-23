# -*- coding: utf-8 -*-
"""Tests for metadata compliance (title / caption / hashtag policy check)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import metadata_compliance as mc


class TestHashtags:
    def test_clean_hashtags_ok(self):
        ok, findings = mc.check_hashtags(["shorts", "motivation", "funny"])
        assert ok is True
        assert findings == []

    def test_banned_hashtag_high(self):
        ok, findings = mc.check_hashtags(["shorts", "freebitcoin"])
        assert ok is False
        assert any(f["category"] == "banned_hashtag" and f["severity"] == "high"
                   for f in findings)

    def test_hashtag_with_hash_symbol_stripped(self):
        ok, findings = mc.check_hashtags(["#casino", "#Shorts"])
        assert ok is False
        assert any(f["matched"] == "#casino" for f in findings)

    def test_string_input_split(self):
        ok, findings = mc.check_hashtags("#shorts, funny #motivation")
        assert ok is True


class TestPatterns:
    def test_medical_claim_high(self):
        res = mc.check_metadata("This cures cancer instantly", "", [])
        assert res["ok"] is False
        assert any(f["category"] == "medical_claim" and f["severity"] == "high"
                   for f in res["findings"])

    def test_financial_claim(self):
        res = mc.check_metadata("Make $5000 a day guaranteed", "", [])
        assert res["ok"] is False
        assert any(f["category"] == "financial_claim" for f in res["findings"])

    def test_clickbait_low_severity(self):
        res = mc.check_metadata("You won't believe this!", "", [])
        assert res["ok"] is True  # low severity does not fail the gate
        assert res["severity"] == "low"

    def test_engagement_bait(self):
        res = mc.check_metadata("", "Comment YES below and subscribe or else", [])
        assert res["ok"] is False
        assert any(f["category"] == "engagement_bait" for f in res["findings"])

    def test_keyword_stuffing(self):
        res = mc.check_metadata("", "best free tool best free tool best free tool", [])
        assert any(f["category"] == "keyword_stuffing" for f in res["findings"])

    def test_clean_metadata(self):
        res = mc.check_metadata("Top 5 Editing Tips", "Full breakdown inside", ["editing"])
        assert res["ok"] is True
        assert res["findings"] == []


class TestExtras:
    def test_extra_rules_file(self, tmp_path):
        extra = tmp_path / "extra.json"
        extra.write_text(
            '{"hashtags": ["mybrand"], '
            '"patterns": [["brandsecret", "brand", "high"]]}',
            encoding="utf-8")
        res = mc.check_metadata("brandsecret tip", "", ["mybrand"],
                                extra_rules_path=str(extra))
        assert res["ok"] is False
        assert any(f["category"] == "brand" for f in res["findings"])
        assert any(f["category"] == "banned_hashtag" for f in res["findings"])

    def test_missing_extra_rules_is_noop(self, tmp_path):
        res = mc.check_metadata("hello", "world", [], extra_rules_path=str(tmp_path / "nope.json"))
        assert res["ok"] is True


class TestAxis:
    def test_metadata_axis_shape(self):
        axis = mc.metadata_axis("Make $5000 a day", "watch now", ["shorts"])
        assert axis["ok"] is False
        assert axis["score"] >= 40
        assert axis["severity"] in ("low", "medium", "high")

    def test_clean_axis_score_zero(self):
        axis = mc.metadata_axis("A normal title", "A normal caption", ["shorts"])
        assert axis["ok"] is True
        assert axis["score"] == 0
