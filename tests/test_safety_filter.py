# -*- coding: utf-8 -*-
"""Tests for the YouTube policy safety filter (hate speech shield)."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import safety_filter as sf

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_arabic_diacritics_and_tatweel(self):
        assert sf.normalize_text("حَـــرَامّّ") == "حرام"

    def test_alef_and_ya_folding(self):
        assert sf.normalize_text("أبإبآبى") == "ابابابي"

    def test_taa_marbuta(self):
        assert sf.normalize_text("كراهية") == "كراهيه"

    def test_definite_article_stripped(self):
        assert sf.normalize_text("القردة") == "قرده"
        assert sf.normalize_text("والخنازير") == "خنازير"

    def test_leetspeak(self):
        assert "faggot" in sf.normalize_text("f@gg0t")

    def test_repeated_letters(self):
        assert sf.normalize_text("كــراااهية") == "كراهيه"

    def test_empty(self):
        assert sf.normalize_text("") == ""
        assert sf.normalize_text(None) == ""


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

class TestMatching:
    def test_arabic_hate_dehumanization(self):
        matches = sf.find_matches("هؤلاء القردة والخنازير لا يستحقون العيش")
        terms = {m["term"] for m in matches}
        assert "قردة" in terms and "خنازير" in terms
        assert all(m["severity"] == "high" for m in matches)

    def test_arabic_call_to_violence(self):
        matches = sf.find_matches("يجب علينا ان اذبحهم جميعا")
        assert any(m["category"] == "violence_threat" for m in matches)

    def test_arabic_obfuscated(self):
        # diacritics + tatweel obfuscation
        matches = sf.find_matches("اذْبَـــحُهُم")
        assert any(m["term"] == "اذبحهم" for m in matches)

    def test_english_slur_and_threat(self):
        matches = sf.find_matches("you are such a f@gg0t, i will kill you")
        cats = {m["category"] for m in matches}
        assert "hate_slur" in cats and "violence_threat" in cats

    def test_clean_text_passes(self):
        assert sf.find_matches("كلام عادي عن الطبخ والوصفات والسفر") == []
        assert sf.find_matches("a normal video about cooking recipes") == []

    def test_clean_religious_text_passes(self):
        # normal religious discourse must not be blocked
        assert sf.find_matches("الحمد لله هذا درس عن الصلاة والصيام والزكاة") == []

    def test_low_severity_not_blocking_by_default(self):
        # "كفار" alone is context-dependent → low severity only
        matches = sf.find_matches("قال تعالى عن الكفار في سورة الكافرون")
        assert all(m["severity"] == "low" for m in matches)

    def test_min_severity_filter(self):
        matches = sf.find_matches("قال تعالى عن الكفار", min_severity="high")
        assert matches == []

    def test_maghrebi_dialect(self):
        matches = sf.find_matches("هذا الزامل يستهزئ بالناس")
        assert any(m["term"] == "زامل" for m in matches)


# ---------------------------------------------------------------------------
# Segment analysis
# ---------------------------------------------------------------------------

TRANSCRIPT = [
    {"start": 0.0, "end": 10.0, "text": "مرحبا بكم في فيديو جديد عن الطبخ"},
    {"start": 10.0, "end": 20.0, "text": "اليوم سنطبخ طبخة لذيذة جدا"},
    {"start": 20.0, "end": 30.0, "text": "هؤلاء القردة والخنازير يجب اذبحهم"},
    {"start": 30.0, "end": 40.0, "text": "والآن نضيف الملح والفلفل"},
]


def _segments():
    return [
        {"title": "طبخة لذيذة", "start_time": 0.0, "end_time": 20.0},
        {"title": "مقطع خطير", "start_time": 20.0, "end_time": 30.0},
        {"title": "توابل", "start_time": 30.0, "end_time": 40.0},
    ]


class TestAnalyzeSegments:
    def test_block_mode_removes_violating_segment(self):
        kept, report = sf.analyze_segments(_segments(), TRANSCRIPT, mode="block")
        titles = [s["title"] for s in kept]
        assert "مقطع خطير" not in titles
        assert "طبخة لذيذة" in titles and "توابل" in titles
        assert report["blocked"] == 1 and report["kept"] == 2

    def test_flag_mode_keeps_everything_annotated(self):
        kept, report = sf.analyze_segments(_segments(), TRANSCRIPT, mode="flag")
        assert len(kept) == 3
        flagged = [s for s in kept if s.get("safety", {}).get("flagged")]
        assert len(flagged) == 1
        assert flagged[0]["title"] == "مقطع خطير"
        assert "violence_threat" in flagged[0]["safety"]["reasons"]
        assert report["flagged"] == 1

    def test_off_mode_untouched(self):
        kept, report = sf.analyze_segments(_segments(), TRANSCRIPT, mode="off")
        assert len(kept) == 3

    def test_report_has_match_details(self):
        _, report = sf.analyze_segments(_segments(), TRANSCRIPT, mode="block")
        blocked = [e for e in report["segments"] if e["status"] == "blocked"]
        assert blocked
        m = blocked[0]["matches"]
        assert any(x["category"] == "hate_dehumanize" for x in m)
        assert any(x["category"] == "violence_threat" for x in m)
        assert all("approx_time" in x for x in m)

    def test_extra_terms_blocking(self, tmp_path):
        extra = tmp_path / "terms.json"
        extra.write_text(json.dumps(
            {"extra_terms": [{"term": "طبخة", "severity": "high"}]},
            ensure_ascii=False), encoding="utf-8")
        kept, report = sf.analyze_segments(
            _segments(), TRANSCRIPT, mode="block", extra_terms_path=str(extra))
        titles = [s["title"] for s in kept]
        assert "طبخة لذيذة" not in titles  # custom term blocked it
        assert "مقطع خطير" not in titles


# ---------------------------------------------------------------------------
# High-level helper (project folder integration)
# ---------------------------------------------------------------------------

class TestApplySafetyFilter:
    def _make_project(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        viral = {"segments": _segments()}
        (project / "viral_segments.txt").write_text(
            json.dumps(viral, ensure_ascii=False), encoding="utf-8")
        # TSV transcript (ms timestamps)
        lines = ["start\tend\ttext"]
        for seg in TRANSCRIPT:
            lines.append(f"{seg['start']*1000}\t{seg['end']*1000}\t{seg['text']}")
        (project / "input.tsv").write_text("\n".join(lines), encoding="utf-8")
        return str(project), viral

    def test_writes_report_and_filters(self, tmp_path):
        project, viral = self._make_project(tmp_path)
        result = sf.apply_safety_filter(viral, project, mode="block")
        assert len(result["segments"]) == 2
        report_path = os.path.join(project, "safety_report.json")
        assert os.path.exists(report_path)
        report = json.loads(open(report_path, encoding="utf-8").read())
        assert report["blocked"] == 1
        assert report["mode"] == "block"

    def test_off_mode_returns_input(self, tmp_path):
        project, viral = self._make_project(tmp_path)
        result = sf.apply_safety_filter(viral, project, mode="off")
        assert result is viral

    def test_no_segments_key(self, tmp_path):
        project, _ = self._make_project(tmp_path)
        assert sf.apply_safety_filter({"foo": 1}, project, mode="block") == {"foo": 1}
