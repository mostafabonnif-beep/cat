"""Pure helper utilities for the WebUI.

Extracted from app.py to keep the interface module focused on UI wiring.
No gradio/psutil imports here so this module stays import-light and testable.
"""
import os
import re
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from i18n.i18n import DEFAULT_LANGUAGE, I18nAuto

i18n = I18nAuto(DEFAULT_LANGUAGE)

PROGRESS_STAGES = ["download", "transcribe", "ai", "cut", "edit", "subtitles", "done"]
# Internal stage IDs stay stable for the pipeline; the visible workbench is Arabic.
STAGE_LABELS_AR = {
    "download": "التنزيل",
    "transcribe": "التفريغ الصوتي",
    "ai": "تحليل الذكاء الاصطناعي",
    "cut": "القص واختيار المقاطع",
    "edit": "المونتاج والتحسين",
    "subtitles": "الترجمة والتنسيق",
    "done": "اكتمل",
}


def empty_progress_state(current=None):
    current = current or i18n("Loading...")
    loading = i18n("Waiting for a run...")  # initial/empty state, not real loading
    state = {k: {"percent": 0, "message": loading} for k in PROGRESS_STAGES}
    state["overall"] = 0
    state["current"] = current
    return state


def convert_color_to_ass(hex_color, alpha="00"):
    try:
        if not hex_color:
            return f"&H{alpha}FFFFFF&"
        hex_clean = hex_color.lstrip('#').strip()
        if hex_clean.lower().startswith("rgb"):
            nums = re.findall(r"[\d\.]+", hex_clean)
            if len(nums) >= 3:
                r, g, b = [max(0, min(255, int(float(n)))) for n in nums[:3]]
                return f"&H{alpha}{b:02X}{g:02X}{r:02X}&".upper()
        if len(hex_clean) == 3:
            hex_clean = ''.join(c * 2 for c in hex_clean)
        if len(hex_clean) == 6 and re.fullmatch(r"[0-9a-fA-F]{6}", hex_clean):
            r, g, b = hex_clean[0:2], hex_clean[2:4], hex_clean[4:6]
            return f"&H{alpha}{b}{g}{r}&".upper()
    except Exception:
        pass
    return f"&H{alpha}FFFFFF&"


def safe_int(value, default):
    try:
        return int(value)
    except Exception:
        return default


def safe_float(value, default):
    try:
        return float(value)
    except Exception:
        return default


def normalize_path(path):
    if not path:
        return path
    return os.path.normpath(str(path))


def build_subtitle_config(font_name, font_size, font_color, highlight_color, outline_color, outline_thickness, shadow_color, shadow_size, is_bold, is_italic, is_uppercase, vertical_pos, alignment, h_size, w_block, gap, mode, under, strike, border_s, remove_punc, caption_animation="none", auto_emoji=False):
    return {
        "font": font_name,
        "base_size": safe_int(font_size, 12),
        "base_color": convert_color_to_ass(font_color),
        "highlight_color": convert_color_to_ass(highlight_color),
        "outline_color": convert_color_to_ass(outline_color),
        "outline_thickness": safe_float(outline_thickness, 1.5),
        "shadow_color": convert_color_to_ass(shadow_color),
        "shadow_size": safe_float(shadow_size, 2),
        "vertical_position": safe_int(vertical_pos, 210),
        "alignment": safe_int(alignment, 2),
        "bold": 1 if is_bold else 0,
        "italic": 1 if is_italic else 0,
        "underline": 1 if under else 0,
        "strikeout": 1 if strike else 0,
        "border_style": safe_int(border_s, 1),
        "words_per_block": safe_int(w_block, 3),
        "gap_limit": safe_float(gap, 0.5),
        "mode": mode,
        "highlight_size": safe_int(h_size, 14),
        "uppercase": 1 if is_uppercase else 0,
        "remove_punctuation": bool(remove_punc),
        "caption_animation": caption_animation if caption_animation in {"none", "pop", "scale", "pop_scale", "bounce"} else "none",
        "auto_emoji": bool(auto_emoji),
    }


# ---------------------------------------------------------------------------
# Panel renderers (used by webui/app.py)
# ---------------------------------------------------------------------------

_PANEL_STYLE = (
    "font-family:inherit;padding:10px 12px;border-radius:10px;"
    "background:#111827;border:1px solid #1f2937;min-height:120px;"
)


def render_progress_html(state):
    """HTML progress panel: per-stage bars + overall percentage (dark theme)."""
    state = state or {}
    stages = state.get("stages", {}) if isinstance(state.get("stages"), dict) else state
    overall = int(state.get("overall", 0) or 0)
    current = state.get("current", "")
    rows = []
    for stage in PROGRESS_STAGES:
        info = stages.get(stage) if isinstance(stages, dict) else state.get(stage, {})
        if not isinstance(info, dict):
            info = {}
        percent = int(info.get("percent", 0) or 0)
        message = info.get("message", "") or ""
        color = "#22c55e" if percent >= 100 else ("#f97316" if percent > 0 else "#6b7280")
        rows.append(
            '<div style="margin:4px 0;font-size:12px;color:#e5e7eb;">'
            '<span style="display:inline-block;width:90px;color:#9ca3af;">{}</span>'
            '<span style="display:inline-block;width:55%;height:8px;background:#374151;'
            'border-radius:4px;vertical-align:middle;overflow:hidden;">'
            '<span style="display:inline-block;width:{}%;height:8px;background:{};border-radius:4px;"></span>'
            '</span> <b style="color:{};">{}</b> <span style="color:#d1d5db;">{}</span></div>'.format(
                _html_escape(STAGE_LABELS_AR.get(stage, stage)), percent, color, color, "%d%%" % percent, _html_escape(message)))
    return (
        '<div style="' + _PANEL_STYLE + '">'
        '<div style="font-size:14px;margin-bottom:8px;color:#f9fafb;">'
            '<b>📊 الإجمالي {}%</b> — {}</div>{}</div>'.format(
            overall, _html_escape(current), "".join(rows)))


def render_tasks_html(state):
    """HTML tasks panel: current stage + last few messages (dark theme)."""
    state = state or {}
    current = state.get("current", "")
    stages = state.get("stages", {}) if isinstance(state.get("stages"), dict) else state
    lines = [            '<b style="color:#f9fafb;">🧩 المهمة الحالية</b>',

             '<span style="color:#fdba74;">{}</span>'.format(_html_escape(current))]
    for stage in PROGRESS_STAGES:
        info = stages.get(stage) if isinstance(stages, dict) else state.get(stage, {})
        if isinstance(info, dict) and info.get("message"):
            lines.append('<i style="color:#9ca3af;">{}</i>: <span style="color:#d1d5db;">{}</span>'.format(
                _html_escape(STAGE_LABELS_AR.get(stage, stage)), _html_escape(str(info["message"]))))
    return '<div style="' + _PANEL_STYLE + 'font-size:13px;line-height:1.7;">{}</div>'.format("<br>".join(lines[:8]))


def _html_escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# Error report summarizer (v6.4) — turn raw traceback tails into scannable
# cards with a friendly hint, instead of dumping 30 raw lines.
# ---------------------------------------------------------------------------

KNOWN_ERROR_HINTS = [
    # --- Transcription dependency conflicts must outrank generic YouTube hints ---
    ("huggingface-hub", i18n("WhisperX dependency conflict: install huggingface-hub>=0.34.0,<1.0 with uv pip, then restart the app")),
    ("huggingface_hub", i18n("WhisperX dependency conflict: install huggingface-hub>=0.34.0,<1.0 with uv pip, then restart the app")),
    ("tokenizers", i18n("WhisperX dependency conflict: transformers caps tokenizers at 0.23.0 (and 0.23.0 was never released) — run: uv pip install \"tokenizers==0.22.2\", then restart the app")),
    ("0.23.1", i18n("WhisperX dependency conflict: transformers caps tokenizers at 0.23.0 (and 0.23.0 was never released) — run: uv pip install \"tokenizers==0.22.2\", then restart the app")),
    # --- Gemini API errors first: common when keys expire or quota ends (v6.9) ---
    ("api key not valid", i18n("Invalid Gemini API key — check it or create a new one at aistudio.google.com/apikey, then save it in Settings")),
    ("api_key_invalid", i18n("Invalid Gemini API key — check it or create a new one at aistudio.google.com/apikey, then save it in Settings")),
    ("permission_denied", i18n("Gemini API key rejected (PERMISSION_DENIED) — make sure the key is active and the Generative Language API is enabled in your Google Cloud project")),
    ("resource_exhausted", i18n("Free Gemini quota or daily limit exhausted — wait for it to reset, raise the quota, or use another key")),
    ("quota exceeded", i18n("Free Gemini quota or daily limit exhausted — wait for it to reset, raise the quota, or use another key")),
    ("rate limit", i18n("YouTube/Gemini is rate-limiting temporarily — wait a minute and retry")),
    # --- YouTube / download errors ---
    ("private video", i18n("Private video — use browser cookies: from the 🔒 menu or rerun with --cookies-from-browser chrome")),
    ("sign in", i18n("Video requires YouTube sign-in — use your browser cookies from the 🔒 menu")),
    ("cookiesfrombrowser", i18n("Could not read browser cookies (Chrome encryption) — try Firefox or an exported cookies.txt file")),
    ("confirm your age", i18n("Age-restricted video — use your browser cookies")),
    ("age-restrict", i18n("Age-restricted video — use your browser cookies")),
    ("video unavailable", i18n("Video unavailable (deleted or geo-blocked)")),
    ("google-generativeai", i18n("Gemini library not installed — run: pip install google-generativeai (or rerun install_dependencies.bat)")),
    ("google.genai", i18n("Gemini library not installed — run: pip install google-genai (or rerun install_dependencies.bat)")),
    ("gemini sdk", i18n("Gemini library not installed — run: pip install google-generativeai")),
    ("generativelanguage", i18n("Gemini API error — likely an invalid key or exhausted quota: try the 🔌 Test Connection button in the AI settings")),
    ("no viral segments", i18n("The AI returned no clips — likely an invalid Gemini key or exhausted quota: try 🔌 Test Connection in Settings")),
    ("403", i18n("YouTube blocked the download (403) — update yt-dlp: uv pip install -U yt-dlp, use browser cookies, or retry in a few minutes")),
    ("forbidden", i18n("YouTube blocked the download (403) — update yt-dlp: uv pip install -U yt-dlp, use browser cookies, or retry in a few minutes")),
    ("np.nan", i18n("Version conflict: numpy 2.x is incompatible with pyannote/whisperx — run: uv pip install 'numpy<2'")),
    ("numpy 2.0", i18n("Version conflict: numpy 2.x is incompatible with pyannote/whisperx — run: uv pip install 'numpy<2'")),
    ("invalid model size", i18n("Whisper model not supported by the installed faster-whisper — update it: uv pip install -U faster-whisper, or pick another model like large-v3 or medium from the list")),
    ("expected one of", i18n("Whisper model not supported by the installed faster-whisper — update it: uv pip install -U faster-whisper, or pick another model like large-v3 or medium from the list")),
    ("whisperx", i18n("Transcription component not installed — rerun install_dependencies.bat and choose to install whisperx")),
    ("torch", i18n("Transcription component not installed — rerun install_dependencies.bat and choose to install whisperx")),
    ("out of memory", i18n("Out of memory — close other programs or use a smaller Whisper model")),
    ("ffmpeg", i18n("FFmpeg not installed or not on PATH — run install_dependencies.bat and choose to download FFmpeg")),
    ("429", i18n("YouTube is rate-limiting temporarily (429) — wait a minute and retry")),
    ("connection", i18n("Internet connection problem or DNS blocking")),
]


def summarize_error(text, max_title=160):
    """Turn a raw error blob into (title, detail, hint).

    title  — first meaningful line (ERROR: … if present)
    detail — full text (capped)
    hint   — friendly Arabic guidance for known problems ("" if unknown)
    """
    text = (text or "").strip()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    title = lines[0] if lines else i18n("Unknown error")
    for ln in lines:
        low = ln.lower()
        if low.startswith("error") or "error:" in low[:40]:
            title = ln
            break
    hint = ""
    # prefer matching the hint against the TITLE line first (the real error),
    # so older messages in the log tail don't hijack the hint
    low_title = title.lower()
    for key, msg in KNOWN_ERROR_HINTS:
        if key in low_title:
            hint = msg
            break
    if not hint:
        low_text = text.lower()
        for key, msg in KNOWN_ERROR_HINTS:
            if key in low_text:
                hint = msg
                break
    return title[:max_title], text[:3000], hint


def render_error_html(error_items):
    """HTML errors panel: scannable cards (title + hint + collapsible detail).

    Accepts strings (old format) or dicts {"title","detail","hint","code"}.
    """
    if not error_items:
        return ""
    cards = []
    for item in error_items:
        if isinstance(item, dict):
            title = item.get("title") or i18n("Error")
            detail = item.get("detail") or ""
            hint = item.get("hint") or ""
            code = item.get("code")
        else:
            title, detail, hint = summarize_error(item)
            code = None
        badge = '<span style="background:#b00020;color:#fff;border-radius:3px;padding:1px 6px;font-size:11px;">' + i18n("Error") + '</span>'
        if code:
            badge = ('<span style="background:#7f1d1d;color:#fff;border-radius:3px;padding:1px 6px;font-size:11px;">' + i18n("Exit code {}") + '</span>').format(code)
        hint_html = (
            '<div style="color:#7a5c00;background:#fff7e0;border:1px solid #f0dc9a;'
            'border-radius:4px;padding:4px 8px;margin-top:4px;font-size:12px;">💡 {}</div>'
            .format(_html_escape(hint))) if hint else ""
        detail_html = (
            '<details style="margin-top:4px;"><summary style="cursor:pointer;'
            'font-size:12px;color:#666;">' + i18n("Technical Details") + '</summary>'
            '<pre style="white-space:pre-wrap;background:#1e1e1e;color:#eee;'
            'border-radius:4px;padding:8px;font-size:11px;max-height:200px;'
            'overflow:auto;">{}</pre></details>'.format(_html_escape(detail))) if detail else ""
        cards.append(
            '<div style="border:1px solid #f0c4c4;background:#fff5f5;border-radius:6px;'
            'margin:6px 0;padding:6px 10px;">'
            '<div style="font-size:13px;font-weight:600;color:#b00020;">{} {}</div>'
            '{}{}</div>'.format(badge, _html_escape(title), hint_html, detail_html))
    return "<div style='font-family:sans-serif;'>" + "".join(cards) + "</div>"
