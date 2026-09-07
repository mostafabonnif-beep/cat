# -*- coding: utf-8 -*-
"""Title / publish-metadata fixes.

Covers:
* scripts/title_text helpers (script detection, scripts_match, emoji
  counting / strip_excess_emoji)
* Arabic clickbait & engagement-bait rules in metadata_compliance
* title/caption language-mismatch and excessive-emoji findings (metadata axis)
* upload_gate.effective_title and the audit that vets the shipped
  recommended_title instead of the raw stored title
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import metadata_compliance as mc  # noqa: E402
from scripts import title_text as tt  # noqa: E402
from scripts import upload_gate as ug  # noqa: E402


def _write(project, name, data):
    with open(os.path.join(project, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# title_text: script detection
# ---------------------------------------------------------------------------

class TestScriptDetection:
    def test_arabic_detected(self):
        assert tt.detect_text_script("مرحبا بالعالم") == "ar"
        assert tt.detect_text_script("عنوان فيديو جديد") == "ar"
        # Arabic Supplement block (U+0750-077F)
        assert tt.detect_text_script("\u0762\u0763\u0764") == "ar"

    def test_english_detected(self):
        assert tt.detect_text_script("Hello world") == "en"
        assert tt.detect_text_script("Top 5 Editing Tips 2026") == "en"
        assert tt.detect_text_script("Café au lait") == "en"

    def test_mixed_detected(self):
        assert tt.detect_text_script("مرحبا Hello World") == "mixed"

    def test_none_for_no_letters(self):
        assert tt.detect_text_script("") == "none"
        assert tt.detect_text_script("123 !!! ...") == "none"
        assert tt.detect_text_script("🔥🔥🔥") == "none"  # emoji are not letters
        # other scripts (Cyrillic) are neither ar nor en for this policy
        assert tt.detect_text_script("Привет мир") == "none"

    def test_has_arabic_helper(self):
        assert tt.is_arabic_script("abc") is False
        assert tt.is_arabic_script("مرحبا") is True
        assert tt.is_arabic_script("") is False


class TestScriptsMatch:
    def test_same_script_matches(self):
        assert tt.scripts_match("عنوان عربي", "وصف عربي") is True
        assert tt.scripts_match("English title", "English caption") is True

    def test_empty_side_is_compatible(self):
        assert tt.scripts_match("", "وصف عربي") is True
        assert tt.scripts_match("English title", "") is True
        assert tt.scripts_match("", "") is True

    def test_ar_en_mismatch(self):
        assert tt.scripts_match("English title", "وصف عربي") is False

    def test_mixed_tolerated(self):
        assert tt.scripts_match("English + عربي title", "وصف عربي") is True
        assert tt.scripts_match("عنوان عربي", "Mixed caption نص عربي") is True


# ---------------------------------------------------------------------------
# title_text: emoji helpers
# ---------------------------------------------------------------------------

class TestEmojiHelpers:
    def test_count_emoji(self):
        assert tt.count_emoji("") == 0
        assert tt.count_emoji("plain text") == 0
        assert tt.count_emoji("🔥") == 1
        assert tt.count_emoji("🔥🔥🔥🔥🔥 hi") == 5
        # variation selector is not a separate emoji
        assert tt.count_emoji("❤️") == 1

    def test_strip_zero_emoji_unchanged(self):
        text = "no emoji here at all"
        assert tt.strip_excess_emoji(text) == text

    def test_strip_within_budget_unchanged(self):
        text = "one 🔥 fire"
        assert tt.strip_excess_emoji(text) == text
        assert tt.strip_excess_emoji("a 🔥 b ❤️ c", max_emoji=2) == "a 🔥 b ❤️ c"

    def test_strip_excess_keeps_first(self):
        cleaned = tt.strip_excess_emoji("🔥a🔥b🔥c🔥d🔥e")
        assert tt.count_emoji(cleaned) == 1
        assert cleaned == "🔥abcde"

    def test_strip_with_budget(self):
        cleaned = tt.strip_excess_emoji("🔥a🔥b🔥c", max_emoji=2)
        assert tt.count_emoji(cleaned) == 2
        assert cleaned == "🔥a🔥bc"

    def test_strip_drops_variation_selectors_when_cleaning(self):
        # ❤️ = U+2764 + U+FE0F; count is 1 so this stays unchanged.
        assert tt.strip_excess_emoji("love ❤️") == "love ❤️"
        # over budget: excess emoji AND the variation selectors are removed
        cleaned = tt.strip_excess_emoji("😀❤️😀 hi")
        assert cleaned == "😀 hi"

    def test_empty_input(self):
        assert tt.strip_excess_emoji("") == ""
        assert tt.strip_excess_emoji(None) is None


# ---------------------------------------------------------------------------
# metadata_compliance: Arabic clickbait / engagement-bait patterns
# ---------------------------------------------------------------------------

class TestArabicPatterns:
    def test_arabic_clickbait_flagged_low(self):
        res = mc.check_metadata("لن تصدق ما حدث في هذه القرية!", "", [])
        assert any(f["category"] == "clickbait" and f["severity"] == "low"
                   and "لن تصدق" in f["matched"] for f in res["findings"])
        # low severity never fails the gate
        assert res["ok"] is True
        assert res["severity"] == "low"

    def test_darija_and_egyptian_clickbait(self):
        for title in ("ماشي تصدق هادشي اللي شفت", "مش هتصدق اللي حصل النهارده"):
            res = mc.check_metadata(title, "", [])
            assert any(f["category"] == "clickbait" and f["severity"] == "low"
                       for f in res["findings"]), title

    def test_arabic_engagement_bait(self):
        res = mc.check_metadata(
            "", "اكتب نعم في التعليقات واشترك في القناة لمزيد من الفيديوهات", [])
        assert any(f["category"] == "engagement_bait" and f["severity"] == "low"
                   for f in res["findings"])

    def test_normal_arabic_sentence_not_flagged(self):
        res = mc.check_metadata(
            "طريقة تحضير القهوة التركية في المنزل",
            "شرح كامل خطوة بخطوة مع المكونات", [])
        assert res["ok"] is True
        assert res["findings"] == []

    def test_english_rules_still_intact(self):
        # existing English behavior must be unchanged by the Arabic additions
        res = mc.check_metadata("You won't believe this!", "", [])
        assert any(f["category"] == "clickbait" for f in res["findings"])
        assert res["severity"] == "low"


# ---------------------------------------------------------------------------
# metadata_compliance: language mismatch + excessive emoji (metadata axis)
# ---------------------------------------------------------------------------

class TestLanguageMismatch:
    def test_en_title_ar_caption_finding(self):
        finding = mc.check_title_language("Top 5 Editing Tips", "شاهد الشرح الكامل")
        assert finding is not None
        assert finding["category"] == "language_mismatch"
        assert finding["severity"] == "low"

    def test_matching_scripts_no_finding(self):
        assert mc.check_title_language("عنوان عربي", "وصف عربي") is None
        assert mc.check_title_language("English title", "English caption") is None

    def test_empty_side_no_finding(self):
        assert mc.check_title_language("", "وصف عربي") is None
        assert mc.check_title_language("English title", "") is None

    def test_mixed_script_no_finding(self):
        assert mc.check_title_language("مرحبا Hello", "وصف عربي") is None

    def test_axis_includes_language_mismatch(self):
        axis = mc.metadata_axis("Top 5 Editing Tips", "شاهد الشرح الكامل", [])
        assert any(f["category"] == "language_mismatch" and f["severity"] == "low"
                   for f in axis["findings"])
        # low severity: shape/ok/score unchanged
        assert axis["ok"] is True
        assert axis["severity"] == "low"
        assert axis["score"] == 0


class TestExcessiveEmoji:
    def test_more_than_three_emoji_flagged(self):
        axis = mc.metadata_axis("🔥🔥🔥🔥🔥 Title", "caption text", [])
        assert any(f["category"] == "excessive_emoji" and f["severity"] == "low"
                   for f in axis["findings"])
        assert axis["ok"] is True
        assert axis["score"] == 0

    def test_emoji_split_across_title_and_caption(self):
        axis = mc.metadata_axis("title 🔥", "caption ❤️ 😂 😎", [])
        assert any(f["category"] == "excessive_emoji" for f in axis["findings"])

    def test_three_or_fewer_not_flagged(self):
        axis = mc.metadata_axis("title 🔥", "caption ❤️ 😂", [])
        assert not any(f["category"] == "excessive_emoji" for f in axis["findings"])
        axis = mc.metadata_axis("clean title", "clean caption", [])
        assert not any(f["category"] == "excessive_emoji" for f in axis["findings"])

    def test_existing_axis_shape_and_clean_score(self):
        axis = mc.metadata_axis("A normal title", "A normal caption", ["shorts"])
        assert axis["ok"] is True
        assert axis["severity"] == "low"
        assert axis["score"] == 0
        assert axis["findings"] == []


# ---------------------------------------------------------------------------
# upload_gate: effective_title + audit vets the shipped title
# ---------------------------------------------------------------------------

class TestEffectiveTitle:
    def test_prefers_recommended_title(self):
        seg = {"title": "raw title", "recommended_title": "  shipped title  "}
        assert ug.effective_title(seg) == "shipped title"

    def test_falls_back_to_title(self):
        assert ug.effective_title({"title": "only title"}) == "only title"

    def test_empty_when_no_title(self):
        assert ug.effective_title({}) == ""
        assert ug.effective_title({"recommended_title": ""}) == ""
        assert ug.effective_title(None) == ""
        assert ug.effective_title("not a dict") == ""

    def test_strips_whitespace(self):
        assert ug.effective_title({"title": "  padded  "}) == "padded"


class TestAuditVetsShippedTitle:
    def _project(self, tmp_path, scorecard_segments, viral_segments):
        project = str(tmp_path)
        _write(project, ug.SCORECARD, {"segments": scorecard_segments})
        _write(project, ug.VIRAL_SEGMENTS_FILE, {"segments": viral_segments})
        return project

    def test_clean_recommended_title_overrides_risky_raw_title(self, tmp_path):
        # Scorecard persists the raw LLM title; publish_panel ships the
        # recommended_title. The audit must pass because the *shipped* title
        # is clean (the raw title alone would have blocked the clip).
        project = self._project(
            tmp_path,
            scorecard_segments=[{"index": 0, "title": "This cures cancer"}],
            viral_segments=[{"title": "This cures cancer",
                             "recommended_title": "Top 5 Editing Tips"}])
        allowed, blocked = ug.audit_project(project)
        assert allowed == [0]
        assert blocked == []

    def test_risky_recommended_title_blocks_clean_raw_title(self, tmp_path):
        # The reverse trap: raw title is clean but the shipped recommended
        # title violates policy — the gate must refuse it.
        project = self._project(
            tmp_path,
            scorecard_segments=[{"index": 0, "title": "A calm cooking tutorial"}],
            viral_segments=[{"title": "A calm cooking tutorial",
                             "recommended_title": "This cures cancer"}])
        allowed, blocked = ug.audit_project(project)
        assert allowed == []
        assert len(blocked) == 1
        assert blocked[0]["index"] == 0
        assert blocked[0]["title"] == "This cures cancer"
        assert any(r["source"] == "metadata_compliance"
                   for r in blocked[0]["reasons"])

    def test_audit_falls_back_when_no_viral_segments(self, tmp_path):
        # Legacy project / tests: no viral_segments.txt → vets entry title.
        project = str(tmp_path)
        _write(project, ug.SCORECARD, {
            "segments": [
                {"index": 0, "title": "Clean"},
                {"index": 1, "title": "Dangerous cure"},
            ]})
        _write(project, ug.PUBLISH_BLOCKLIST, {
            "blocked": [{"index": 1, "title": "Dangerous cure",
                         "axes": {"reuse": {"score": 75}}}]})
        allowed, blocked = ug.audit_project(project)
        assert allowed == [0]
        assert len(blocked) == 1
        assert blocked[0]["index"] == 1
