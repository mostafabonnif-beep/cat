import json

from scripts import safety_filter, semantic_safety, upload_gate


def test_collective_violent_call_is_blocked():
    verdict = semantic_safety.analyze_text("اقتلوا المهاجرين وطردوهم")
    assert verdict["action"] == "block"
    assert verdict["confidence"] >= 0.9
    assert verdict["category"] == "hate_or_violence_incitement"


def test_counter_speech_is_review_not_silent_allow():
    verdict = semantic_safety.analyze_text("نحن ضد الكراهية ولا نؤيد قتل المهاجرين")
    assert verdict["action"] == "review"
    assert "counter_speech_or_educational_context" in verdict["signals"]


def test_normal_text_is_allowed():
    verdict = semantic_safety.analyze_text("نصائح عملية لتحسين جودة الفيديو والصوت")
    assert verdict["action"] == "allow"

def test_identity_reference_alone_is_allowed():
    verdict = semantic_safety.analyze_text("سعودي يشرح وصفة طبخ")
    assert verdict["action"] == "allow"

def test_animal_description_is_not_hate_speech():
    verdict = semantic_safety.analyze_text("القنفذ حيوان أليف ومفيد للغاية")
    assert verdict["action"] == "allow"
    assert verdict["category"] is None


def test_identity_plus_dehumanization_is_blocked():
    verdict = semantic_safety.analyze_text("السعوديين حشرات ويجب طردهم")
    assert verdict["action"] == "block"
    assert "protected_or_collective_target" in verdict["signals"]
    assert "dehumanizing_comparison" in verdict["signals"]


def test_safety_filter_persists_semantic_verdict(tmp_path):
    segments = [{
        "title": "Risky",
        "caption": "",
        "start_time": 0,
        "end_time": 8,
        "text": "اقتلوا المهاجرين",
    }]
    kept, report = safety_filter.analyze_segments(segments, mode="block")
    assert kept == []
    assert report["blocked"] == 1
    assert report["segments"][0]["semantic"]["action"] == "block"


def test_upload_gate_blocks_manual_review(tmp_path):
    (tmp_path / "safety_report.json").write_text(json.dumps({
        "segments": [{
            "index": 0,
            "status": "manual_review",
            "title": "Context required",
            "semantic": {"action": "review", "explanation": "context required"},
        }]
    }), encoding="utf-8")
    verdict = upload_gate.check_clip(str(tmp_path), index=0, title="Clean", caption="Clean", hashtags=[])
    assert verdict["allowed"] is False
    assert any(reason["source"] == "semantic_safety" for reason in verdict["reasons"])
