# -*- coding: utf-8 -*-
"""Title / publish-metadata fixes.

Covers:
* scripts/title_text helpers (script detection, scripts_match, emoji
  counting / strip_excess_emoji, fit_publish_title word-boundary fitting)
* Arabic clickbait & engagement-bait rules in metadata_compliance
* title/caption language-mismatch and excessive-emoji findings (metadata axis)
* upload_gate.effective_title and the audit that vets the shipped
  recommended_title instead of the raw stored title
* wiring: the final upload/clipboard publish titles (YouTube request body via
  upload_gate, batch filename-fallback via publish_panel) go through
  fit_publish_title instead of a silent [:100] slice
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


# ---------------------------------------------------------------------------
# title_text: fit_publish_title — word-boundary fitting for platform caps
# ---------------------------------------------------------------------------

ELLIPSIS = tt.ELLIPSIS  # "\u2026" — single-char ellipsis used by the fitter


class TestFitPublishTitle:
    def test_short_title_unchanged_no_ellipsis(self):
        assert tt.fit_publish_title("Short title") == "Short title"
        assert tt.fit_publish_title("Another very normal title") == \
            "Another very normal title"

    def test_exactly_at_limit_unchanged(self):
        text = "a" * 100
        assert tt.fit_publish_title(text) == text
        assert tt.fit_publish_title("ab cd", 5) == "ab cd"

    def test_trims_input_first(self):
        assert tt.fit_publish_title("  padded  ") == "padded"
        # padding does not count toward the budget and never triggers '…'
        # (trimmed length 5 fits inside the limit)
        assert tt.fit_publish_title("  short  ", 10) == "short"

    def test_long_title_cut_at_last_word_boundary(self):
        # 11 words of 10 chars → 120 chars; last whitespace at/before 99 is
        # index 98, so the fit keeps 9 whole words (98 chars) + '…' = 99.
        title = " ".join(["abcdefghij"] * 11)
        assert len(title) == 120
        result = tt.fit_publish_title(title)
        assert result == title[:98] + ELLIPSIS
        assert result.endswith(ELLIPSIS)
        assert len(result) == 99 <= 100
        assert result[:-1] == title[:98]      # pure prefix, no word split
        assert title[len(result) - 1] == " "  # the cut landed on a space

    def test_boundary_whitespace_at_limit_minus_one(self):
        # Space sits exactly at limit-1 (index 2): the whole first word fits.
        assert tt.fit_publish_title("ab cd", 3) == "ab" + ELLIPSIS
        assert len(tt.fit_publish_title("ab cd", 3)) == 3
        # Consecutive spaces never leave a dangling space before '…'.
        result = tt.fit_publish_title("aa bb   cc dd", 8)
        assert result.endswith(ELLIPSIS)
        assert not result[:-1].endswith(" ")
        assert len(result) <= 8

    def test_single_long_token_hard_cut(self):
        # 60-char token, no whitespace → hard cut at limit-1 (49) + '…'.
        token = "abcdefghij" * 6
        result = tt.fit_publish_title(token, 50)
        assert result == token[:49] + ELLIPSIS
        assert len(result) == 50

    def test_arabic_title_cuts_at_arabic_word_boundary(self):
        # 40 Arabic words of 4 letters → 199 chars; the space at index 99
        # (== limit-1) is a legal boundary, so 20 whole words survive.
        title = " ".join(["كلمة"] * 40)
        assert tt.is_arabic_script(title)
        result = tt.fit_publish_title(title)
        assert len(result) == 100
        assert result == title[:99] + ELLIPSIS
        assert result.endswith(ELLIPSIS)
        # the kept text ends with a complete Arabic word (letter, not space)
        assert tt.is_arabic_script(result[:-1][-4:])
        assert tt.detect_text_script(result[:-1]) == "ar"

    def test_zwsp_is_a_word_boundary(self):
        # ZWSP (U+200B) is used as an invisible word separator (Arabic social
        # titles). Python's re \s does NOT match it, so the fitter extends the
        # whitespace class explicitly; a ZWSP-joined phrase must never be cut
        # mid-"word". 11 ZWSP-joined 10-char segments → cut after segment 8.
        title = "\u200b".join(["abcdefghij"] * 11)
        result = tt.fit_publish_title(title)
        assert len(result) == 99
        assert result == title[:98] + ELLIPSIS
        assert title[len(result) - 1] == "\u200b"

    def test_none_and_empty(self):
        assert tt.fit_publish_title(None) == ""
        assert tt.fit_publish_title("") == ""
        assert tt.fit_publish_title("   ") == ""  # whitespace-only input

    def test_non_positive_limit_returns_empty(self):
        assert tt.fit_publish_title("some title", 0) == ""
        assert tt.fit_publish_title("some title", -5) == ""
        assert tt.fit_publish_title(None, 0) == ""


# ---------------------------------------------------------------------------
# Wiring: final publish titles go through fit_publish_title (no silent slices)
# ---------------------------------------------------------------------------

class TestPublishTitleWiring:
    def test_youtube_request_title_fitted_at_100(self, tmp_path, monkeypatch):
        """upload_gate.YouTubeUploader builds snippet.title via fit_publish_title.

        Mirrors tests/test_upload_gate.py's fake-google setup: the request
        body is captured before any real SDK/network call happens.
        """
        import sys as _sys

        from scripts import upload_gate as ug

        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake video bytes")
        captured = {}

        class FakeCreds:
            valid = True

        class FakeMedia:
            def __init__(self, path, chunksize, resumable):
                captured["media_path"] = path

        class FakeRequest:
            def next_chunk(self):
                return None, {"id": "WIRED1", "status": "uploaded"}

        class FakeVideos:
            def insert(self, part, body, media_body):
                captured["body"] = body
                return FakeRequest()

        class FakeService:
            def videos(self):
                return FakeVideos()

        fake_discovery = type(_sys)("googleapiclient.discovery")
        fake_discovery.build = lambda *a, **k: FakeService()
        fake_http = type(_sys)("googleapiclient.http")
        fake_http.MediaFileUpload = FakeMedia
        monkeypatch.setitem(_sys.modules, "googleapiclient.discovery", fake_discovery)
        monkeypatch.setitem(_sys.modules, "googleapiclient.http", fake_http)
        monkeypatch.setenv("YT_PRIVACY", "unlisted")

        uploader = ug.YouTubeUploader(str(tmp_path), dry_run=False)
        monkeypatch.setattr(uploader, "_load_or_create_token", lambda: FakeCreds())

        long_title = " ".join(["abcdefghij"] * 11)  # 120 chars > YouTube cap
        result = uploader.upload(str(video), long_title, "cap", [], index=0)
        assert result["status"] == "uploaded"
        shipped = captured["body"]["snippet"]["title"]
        assert shipped == tt.fit_publish_title(long_title, 100)
        assert shipped == long_title[:98] + ELLIPSIS
        assert len(shipped) <= 100
        assert shipped.endswith(ELLIPSIS)

        # Short titles are never touched by the upload path. Use distinct
        # video content: the content-guard registry fingerprints published
        # files and refuses to republish the same bytes twice.
        video2 = tmp_path / "clip2.mp4"
        video2.write_bytes(b"different video bytes")
        result = uploader.upload(str(video2), "My Title", "cap", [], index=0)
        assert captured["body"]["snippet"]["title"] == "My Title"

    def test_publish_panel_batch_filename_fallback_is_fitted(self, tmp_path, monkeypatch):
        """Batch clips without a segment suggestion get a word-boundary title.

        stream_upload_batch derives the fallback publish title from the
        filename; that title must be fitted to the platform cap (default 100)
        instead of a raw [:100] slice of the filename.
        """
        from webui import publish_panel as pp

        project = str(tmp_path / "proj")
        os.makedirs(project, exist_ok=True)
        # Stem longer than 100 chars with real word boundaries; >99 chars is
        # enough to force truncation and words are 18 chars so the cut lands
        # cleanly between words (no viral_segments.txt → filename fallback).
        stem = "long segment name " * 10  # 180 chars
        assert len(stem) > 100
        clip_path = os.path.join(project, stem + ".mp4")
        with open(clip_path, "wb") as stream:
            stream.write(b"clip")

        captured = {}

        def fake_stream_upload(*args, **kwargs):
            captured["title"] = args[3]
            captured["platform"] = args[1]
            yield "done"
            return pp._publish_result("uploaded", args[2], args[3])

        monkeypatch.setattr(pp, "stream_upload", fake_stream_upload)
        updates = list(pp.stream_upload_batch(project, "youtube", [clip_path],
                                              True, "warn"))
        assert captured["platform"] == "youtube"
        assert captured["title"] == tt.fit_publish_title(stem, 100)
        assert captured["title"].endswith(ELLIPSIS)
        assert len(captured["title"]) <= 100
        assert any("اكتمل رفع/جدولة كل الملفات" in item for item in updates)

    def test_publish_panel_batch_keys_limit_on_platform(self, tmp_path, monkeypatch):
        """The same shared batch path uses TikTok's 150 cap for TikTok clips."""
        from webui import publish_panel as pp

        project = str(tmp_path / "proj")
        os.makedirs(project, exist_ok=True)
        stem = "long segment name " * 10
        clip_path = os.path.join(project, stem + ".mp4")
        with open(clip_path, "wb") as stream:
            stream.write(b"clip")
        captured = {}

        def fake_stream_upload(*args, **kwargs):
            captured["title"] = args[3]
            yield "done"
            return pp._publish_result("uploaded", args[2], args[3])

        monkeypatch.setattr(pp, "stream_upload", fake_stream_upload)
        list(pp.stream_upload_batch(project, "tiktok", [clip_path], True, "warn"))
        assert captured["title"] == tt.fit_publish_title(stem, 150)
        assert len(captured["title"]) <= 150

    def test_publish_panel_short_suggestion_passes_through(self, tmp_path, monkeypatch):
        """Existing suggested titles are not truncated by the batch path."""
        from webui import publish_panel as pp

        project = str(tmp_path / "proj")
        final_dir = os.path.join(project, "final")
        os.makedirs(final_dir, exist_ok=True)
        with open(os.path.join(project, "viral_segments.txt"), "w", encoding="utf-8") as f:
            json.dump({"segments": [{"title": "Clean Short Title",
                                     "recommended_title": "Clean Short Title",
                                     "caption": ""}]}, f, ensure_ascii=False)
        clip_path = os.path.join(final_dir, "000_clip.mp4")
        with open(clip_path, "wb") as stream:
            stream.write(b"clip")
        captured = {}

        def fake_stream_upload(*args, **kwargs):
            captured["title"] = args[3]
            yield "done"
            return pp._publish_result("uploaded", args[2], args[3])

        monkeypatch.setattr(pp, "stream_upload", fake_stream_upload)
        list(pp.stream_upload_batch(project, "youtube", [clip_path], True, "warn"))
        assert captured["title"] == "Clean Short Title"
