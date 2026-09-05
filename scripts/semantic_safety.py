"""Context-aware offline safety heuristics for hate and incitement.

This module complements the literal blocklist.  It does not claim to replace
human review or a platform policy classifier: it looks for high-signal
combinations such as a target-group reference plus an exclusion/dehumanization
verb, or a violent imperative plus a collective target.  Counter-speech and
news/educational framing are treated as review signals rather than automatic
allowance, so creators still get a conservative publish decision.
"""

import re
from typing import Any, Dict, List, Optional

from scripts.safety_filter import normalize_text

_GROUPS = (
    r"(?:مهاجر|مهاجرين|مهاجرون|لاجئ|لاجئين|لاجئون|اجنبي|اجانب|يهود|"
    r"مسلم|مسلمين|مسلمون|مسيحي|مسيحيين|نصارى|شيعي|شيعة|سني|سنة|ملحد|ملحدين|"
    r"نساء|نساءنا|رجال|مثليين|مثليات|ذوي اعاقه|معاقين|سود|بيض|افارقه|افريقيين|"
    r"سعودي|سعوديين|خليجي|خليجيين|اماراتي|اماراتيين|كويتي|كويتيين|قطري|قطريين|"
    r"سوري|سوريين|ايراني|ايرانيين|صومالي|صوماليين|صيني|صينيين|هندي|هنود|"
    r"عرب|عربي|امازيغ|امازيغي|بدو|غجر|عرق|جماعه|طائفه|قوم|شعب|هؤلاء الناس|هذولا الناس|"
    r"these people|those people|"
    r"immigrants|refugees|foreigners|jews|muslims|christians|women|men|gay people|"
    r"disabled people|black people|white people|that group)"
)

_VIOLENT_VERBS = (
    r"(?:قتل|اقتل|اقتلو|اقتلوهم|اقتلهم|نقتلهم|نقتلك|اذبح|اذبحو|اذبحوهم|"
    r"احرق|احرقوهم|نحرقهم|ابيد|أبيد|ابيدوهم|اطرد|اطردوهم|طهر|اسحق|دمر|"
    r"خليهم يموتوا|خليهم يموتو|ما يستاهلوش يعيشوا|"
    r"kill|murder|slaughter|burn|exterminate|wipe out|destroy|deport)"
)

_EXCLUSION_VERBS = (
    r"(?:لا مكان ل|لا يستحق(?:ون)? (?:الحياة|العيش|حياة|عيش)|ما يستاهل(?:وش)? الحياة|يجب طرد|"
    r"اطردوا|اخرجوا|لا نريدهم|ما نحبهمش|تخلصوا من|احرموا|منعوا|"
    r"inferior|subhuman|should not exist|do not belong|send them away|remove them)"
)

_DEHUMANIZING = (
    r"(?:حشرات|خنازير|حيوانات|طفيليات|جرذان|قمامة|حثالة|"
    r"vermin|pigs|animals|parasites|cockroaches|trash|scum|subhuman)"
)

_COUNTER_SPEECH = (
    r"(?:ضد كراهيه|لا اؤيد|لا نؤيد|ارفض|ندين|يدين|ادانه|خطاب كراهيه|"
    r"توعيه|خبر|اخبار|وثائقي|تاريخ|تعليم|نحذر من|لا يجوز|"
    r"against hate|do not support|condemn|condemning|news|documentary|"
    r"educational|history|warning|not acceptable)"
)


def _matched(pattern: str, text: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _recommendation(action: str, category: Optional[str]) -> str:
    if action == "block":
        return "احذف المقطع أو أعد صياغته دون استهداف جماعة أو دعوة إلى العنف، ثم راجعه يدوياً قبل النشر."
    if action == "review":
        return "لا تنشر تلقائياً؛ راجع السياق والصوت كاملاً، واستبدل العبارة بصياغة تعليمية محايدة إن أمكن."
    return "لم تُرصد إشارة دلالية عالية الخطورة، لكن تبقى المراجعة البشرية مطلوبة قبل النشر."


def analyze_text(text: str) -> Dict[str, Any]:
    """Return a conservative, JSON-safe semantic verdict for one text."""
    normalized = normalize_text(text)
    if not normalized:
        return {
            "action": "allow",
            "confidence": 0.0,
            "category": None,
            "signals": [],
            "explanation": "empty_text",
            "recommendation": _recommendation("allow", None),
        }

    signals: List[str] = []
    has_group = _matched(_GROUPS, normalized)
    has_violence = _matched(_VIOLENT_VERBS, normalized)
    has_exclusion = _matched(_EXCLUSION_VERBS, normalized)
    has_dehumanizing = _matched(_DEHUMANIZING, normalized)
    counter = _matched(_COUNTER_SPEECH, normalized)

    if has_group:
        signals.append("protected_or_collective_target")
    if has_violence:
        signals.append("violent_or_coercive_verb")
    if has_exclusion:
        signals.append("exclusion_or_dehumanization_frame")
    if has_dehumanizing:
        signals.append("dehumanizing_comparison")
    if counter:
        signals.append("counter_speech_or_educational_context")

    if has_group and has_violence and not counter:
        return {
            "action": "block",
            "confidence": 0.96,
            "category": "hate_or_violence_incitement",
            "signals": signals,
            "explanation": "collective target combined with a violent or coercive call",
            "recommendation": _recommendation("block", "hate_or_violence_incitement"),
        }
    if has_group and (has_exclusion or has_dehumanizing) and not counter:
        return {
            "action": "block",
            "confidence": 0.93,
            "category": "hate_or_dehumanization",
            "signals": signals,
            "explanation": "collective target combined with exclusion or dehumanization",
            "recommendation": _recommendation("block", "hate_or_dehumanization"),
        }
    if (has_violence and has_exclusion) or (has_dehumanizing and has_exclusion) or (has_group and counter and (has_violence or has_dehumanizing or "قتل" in normalized)):
        return {
            "action": "review",
            "confidence": 0.72,
            "category": "context_required",
            "signals": signals,
            "explanation": "high-risk terms require human/contextual review",
            "recommendation": _recommendation("review", "context_required"),
        }
    if has_exclusion or has_violence:
        return {
            "action": "review",
            "confidence": 0.62,
            "category": "harassment_or_threat_context",
            "signals": signals,
            "explanation": "potentially harmful language without a clear target/context",
            "recommendation": _recommendation("review", "harassment_or_threat_context"),
        }
    if has_dehumanizing:
        return {
            "action": "allow",
            "confidence": 0.18,
            "category": None,
            "signals": signals,
            "explanation": "animal or degrading term without a protected-group target",
            "recommendation": _recommendation("allow", None),
        }
    return {
        "action": "allow",
        "confidence": 0.12,
        "category": None,
        "signals": signals,
        "explanation": "no high-signal semantic combination detected",
        "recommendation": _recommendation("allow", None),
    }


def analyze_segments(segments: List[dict]) -> List[dict]:
    """Analyze segment title/caption/text and return indexed verdicts."""
    results = []
    for index, segment in enumerate(segments or []):
        text = " ".join(
            str(segment.get(key, "") or "")
            for key in ("text", "title", "caption", "reasoning")
        ).strip()
        verdict = analyze_text(text)
        verdict["index"] = index
        verdict["title"] = segment.get("title", "")
        verdict["start_time"] = segment.get("start_time")
        verdict["end_time"] = segment.get("end_time")
        verdict["text_preview"] = text[:240]
        results.append(verdict)
    return results
