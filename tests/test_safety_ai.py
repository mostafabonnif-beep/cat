# -*- coding: utf-8 -*-
"""Tests for the AI second-pass policy review + new safety filter modes."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import safety_ai
from scripts import safety_filter as sf

# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

class TestPrompt:
    def test_prompt_contains_clips(self):
        clips = [{"index": 0, "title": "أ", "text": "نص أول"},
                 {"index": 1, "title": "ب", "text": "نص ثان"}]
        prompt = safety_ai.build_review_prompt(clips)
        assert "CLIP 0" in prompt and "CLIP 1" in prompt
        assert "نص أول" in prompt and "نص ثان" in prompt

    def test_long_text_truncated(self):
        clips = [{"index": 0, "title": "x", "text": "a" * 5000}]
        prompt = safety_ai.build_review_prompt(clips)
        assert len(prompt) < 5000


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

class TestParse:
    def test_clean_json(self):
        r = safety_ai.parse_review_response(
            '[{"index": 0, "violation": false, "reason": ""},'
            ' {"index": 1, "violation": true, "reason": "hate speech"}]')
        assert r[0]["violation"] is False
        assert r[1]["violation"] is True and "hate" in r[1]["reason"]

    def test_markdown_fenced(self):
        r = safety_ai.parse_review_response(
            'Sure! Here is the result:\n```json\n[{"index": 2, "violation": true, "reason": "x"}]\n```')
        assert 2 in r and r[2]["violation"] is True

    def test_garbage_returns_empty(self):
        assert safety_ai.parse_review_response("I cannot help with that") == {}
        assert safety_ai.parse_review_response("") == {}
        assert safety_ai.parse_review_response(None) == {}

    def test_think_tags_stripped(self):
        r = safety_ai.parse_review_response(
            '<think>reasoning here</think>[{"index": 0, "violation": true, "reason": "y"}]')
        assert 0 in r


# ---------------------------------------------------------------------------
# Backend gating
# ---------------------------------------------------------------------------

class TestGating:
    def test_should_run(self):
        assert safety_ai.should_run_ai_review("gemini", "on")
        assert safety_ai.should_run_ai_review("g4f", "on")
        assert not safety_ai.should_run_ai_review("local", "on")
        assert not safety_ai.should_run_ai_review("manual", "on")
        assert not safety_ai.should_run_ai_review("gemini", "off")


# ---------------------------------------------------------------------------
# Verdict application
# ---------------------------------------------------------------------------

class TestApply:
    def _segments(self):
        return [{"title": "clean"}, {"title": "toxic"}, {"title": "fine"}]

    def test_block_mode_drops_ai_flagged(self):
        verdicts = {1: {"violation": True, "reason": "hate"}}
        kept, report = safety_ai.apply_ai_review(
            self._segments(), [], verdicts, mode="block")
        assert [s["title"] for s in kept] == ["clean", "fine"]
        assert report[0]["status"] == "ai_blocked"

    def test_censor_mode_drops_ai_flagged(self):
        # context-level violation cannot be fixed by bleeping words
        verdicts = {1: {"violation": True, "reason": "hate"}}
        kept, report = safety_ai.apply_ai_review(
            self._segments(), [], verdicts, mode="censor")
        assert len(kept) == 2 and report[0]["status"] == "ai_blocked"

    def test_flag_mode_annotates(self):
        verdicts = {1: {"violation": True, "reason": "hate"}}
        kept, report = safety_ai.apply_ai_review(
            self._segments(), [], verdicts, mode="flag")
        assert len(kept) == 3
        flagged = [s for s in kept if (s.get("safety") or {}).get("ai_flagged")]
        assert len(flagged) == 1 and flagged[0]["safety"]["ai_reason"] == "hate"

    def test_no_verdicts_keeps_all(self):
        kept, report = safety_ai.apply_ai_review(self._segments(), [], {}, mode="block")
        assert len(kept) == 3 and report == []


# ---------------------------------------------------------------------------
# Safety filter: censor mode + allowlist
# ---------------------------------------------------------------------------

TRANSCRIPT = [
    {"start": 0.0, "end": 10.0, "text": "كلام نظيف عن الطبخ"},
    {"start": 10.0, "end": 20.0, "text": "هؤلاء القردة والخنازير"},
]


class TestCensorMode:
    def test_censor_mode_keeps_and_annotates(self):
        segs = [{"title": "نظيف", "start_time": 0.0, "end_time": 10.0},
                {"title": "فيه شتيمة", "start_time": 10.0, "end_time": 20.0}]
        kept, report = sf.analyze_segments(segs, TRANSCRIPT, mode="censor")
        assert len(kept) == 2  # nothing dropped — bleeping happens post-cut
        censored = [s for s in kept if (s.get("safety") or {}).get("action") == "censor"]
        assert len(censored) == 1
        assert report["censored"] == 1

    def test_allow_terms_disable_blocklist_hit(self):
        segs = [{"title": "x", "start_time": 10.0, "end_time": 20.0}]
        kept, report = sf.analyze_segments(
            segs, TRANSCRIPT, mode="block",
            extra_terms_path=None)
        assert len(kept) == 0  # blocked normally
        # now with allowlist via project folder
        import json as _json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "safety_terms.json"), "w", encoding="utf-8") as f:
                _json.dump({"allow_terms": ["قردة", "خنازير"]}, f, ensure_ascii=False)
            kept2, _ = sf.analyze_segments(segs, TRANSCRIPT, project_folder=d, mode="block")
            assert len(kept2) == 1  # allowlisted → kept
