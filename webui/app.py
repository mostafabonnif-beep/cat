import atexit
import base64
import datetime
import html as html_lib
import json
import os
import subprocess
import sys
import time
import uuid

import gradio as gr
import psutil

try:
    # Package mode: ``python -m webui.app`` or ``import webui.app``.
    from . import (
        backup,
        batch_queue,
        file_inputs,
        learn_panel,
        library,
        project_store,
        publish_panel,
        render_queue,
        runtime,
        segments_review,
        settings_store,
        style,
        telegram_control,
    )
    from . import subtitle_editor as editor
    from . import subtitle_handler as subs
except ImportError:
    # Script mode kept for the Windows/Linux launchers (``python webui/app.py``).
    import backup
    import batch_queue  # Module for Batch Queue Logic
    import file_inputs
    import learn_panel
    import library  # Module for Library Logic
    import project_store
    import publish_panel  # Module for Publish & Upload Logic
    import render_queue
    import runtime  # frozen-exe helpers (sys.executable re-invocation)
    import segments_review  # Module for Segments Review Logic
    import settings_store  # Module for persistent AI settings (save/load Gemini key)
    import style  # Learn (strike feedback) & Performance (analytics) panels
    import subtitle_editor as editor  # Module for Editor Logic
    import subtitle_handler as subs  # Module for Subtitles
    import telegram_control  # Optional local Telegram queue control
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# Path to the main script. Frozen exe: the exe itself (nothing on disk);
# source run: main_improved.py. WORKING_DIR holds user projects (VIRALS).
if runtime.is_frozen():
    MAIN_SCRIPT_PATH = sys.executable
    WORKING_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    MAIN_SCRIPT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main_improved.py")
    WORKING_DIR = os.path.dirname(MAIN_SCRIPT_PATH)
sys.path.append(WORKING_DIR)

from i18n.i18n import DEFAULT_LANGUAGE, I18nAuto

i18n = I18nAuto(DEFAULT_LANGUAGE)

# Version banner at startup — helps confirm you run the latest code
try:
    from app_version import VERSION as _VERSION
    print(f"OUSSAMA Cutter WebUI v{_VERSION}")
except Exception:
    pass

def tr(key):
    return i18n(key)


def prepare_youtube_preflight(project_path, oauth_file=None, *, dry_run=True,
                              privacy_status="private", publish_at=None,
                              schedule_interval_minutes=60, expected_clips=0,
                              full_access=False):
    """Validate YouTube readiness before the expensive cutting pipeline.

    This function never starts an interactive OAuth flow and never uploads. It
    validates/stores a selected client file, rejects stale credentials after a
    replacement, verifies the channel with read-only API access, checks the
    local circuit breaker and validates every planned publish timestamp.
    """
    from webui.youtube_credentials import (
        replace_client_secrets,
        store_client_secrets,
    )
    from webui.youtube_credentials import status as credential_status

    result = {
        "ready": False,
        "mode": "dry_run" if dry_run else "actual_upload_selected",
        "channel": {},
        "client_secrets_path": "",
        "scheduled_count": 0,
        "quota": "not_verifiable_before_upload",
        "warnings": [],
    }
    selected = file_inputs.first_path(oauth_file) if oauth_file else None
    if selected:
        stored = store_client_secrets(selected)
        if stored.get("changed"):
            # store_client_secrets writes the new file; replacement is still
            # required to invalidate a token issued for the old client.
            stored = replace_client_secrets(selected, invalidate_token=True)
        result["client_secrets_path"] = stored["path"]
    else:
        current = credential_status(full_access)
        if not current.get("client_secrets_present"):
            raise RuntimeError("client_secrets.json غير موجود؛ اختر ملف OAuth قبل بدء المعالجة")
        result["client_secrets_path"] = current["client_secrets_path"]

    try:
        interval = float(schedule_interval_minutes)
    except (TypeError, ValueError):
        raise ValueError("فاصل الجدولة غير صالح") from None
    if interval < 1 or interval > 10080:
        raise ValueError("فاصل الجدولة يجب أن يكون بين 1 و10080 دقيقة")

    schedule_start = None
    if publish_at:
        raw = str(publish_at).strip().replace("Z", "+00:00")
        try:
            schedule_start = datetime.datetime.fromisoformat(raw)
        except ValueError:
            raise ValueError("وقت الجدولة يجب أن يكون ISO 8601 صالحاً") from None
        if schedule_start.tzinfo is None:
            raise ValueError("وقت الجدولة يجب أن يتضمن منطقة زمنية مثل +00:00 أو Z")
        now = datetime.datetime.now(schedule_start.tzinfo)
        if schedule_start <= now:
            raise ValueError("وقت الجدولة يجب أن يكون في المستقبل")
        count = max(1, int(expected_clips or 1))
        last_at = schedule_start + datetime.timedelta(minutes=interval * (count - 1))
        result["scheduled_count"] = count
        result["schedule_start"] = schedule_start.isoformat()
        result["schedule_last"] = last_at.isoformat()
        if str(privacy_status or "private").lower() != "private":
            raise ValueError("YouTube يفرض Private على الفيديوهات التي لها publishAt")

    # Projects under VIRALS share this registry. The extra roots also cover
    # legacy/ad-hoc projects and make a previous channel incident fail closed.
    from scripts import content_guard
    seen_registries = set()
    for candidate in (project_path, VIRALS_DIR, WORKING_DIR):
        if not candidate:
            continue
        state = content_guard.channel_status(candidate, "youtube")
        registry = str(state.get("registry_path") or candidate)
        if registry in seen_registries:
            continue
        seen_registries.add(registry)
        if state.get("locked"):
            detail = "قاطع مخاطر قناة YouTube مقفول بسبب حادثة سياسة مسجلة محلياً"
            if not dry_run:
                raise RuntimeError(detail)
            result["warnings"].append(detail + "؛ Dry Run فقط")

    from scripts.upload_gate import YouTubeUploader
    uploader = YouTubeUploader(
        project_path or WORKING_DIR,
        dry_run=True,
        client_secrets_path=result["client_secrets_path"],
        oauth_full_access=full_access,
    )
    result["channel"] = uploader.verify_channel()
    result["ready"] = True
    return result


def watermark_preview_html(logo_path="", position="bottom-right", size_fraction=0.12, opacity=0.9):
    """Render a small, local-only watermark preview without serving arbitrary files."""
    position = str(position or "bottom-right")
    anchors = {
        "top-left": ("top:10px; left:10px", "أعلى يسار"),
        "top-right": ("top:10px; right:10px", "أعلى يمين"),
        "bottom-left": ("bottom:10px; left:10px", "أسفل يسار"),
        "bottom-right": ("bottom:10px; right:10px", "أسفل يمين"),
        "center": ("top:50%; left:50%; transform:translate(-50%,-50%)", "الوسط"),
    }
    anchor, label = anchors.get(position, anchors["bottom-right"])
    try:
        size = max(5, min(30, float(size_fraction or 0.12) * 100))
    except (TypeError, ValueError):
        size = 12
    try:
        alpha = max(0.1, min(1.0, float(opacity or 0.9)))
    except (TypeError, ValueError):
        alpha = 0.9
    image = ""
    path = str(logo_path or "").strip()
    if path and os.path.isfile(path):
        try:
            with open(path, "rb") as stream:
                encoded = base64.b64encode(stream.read()).decode("ascii")
            mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
            image = '<img alt="logo preview" src="data:{};base64,{}" style="width:{}%;opacity:{};object-fit:contain;" />'.format(mime, encoded, size, alpha)
        except OSError:
            image = ""
    if not image:
        image = '<div style="width:{}%;opacity:{};color:#fff;background:#f97316;padding:6px 8px;border-radius:6px;font-size:11px;text-align:center;">شعار القناة</div>'.format(size, alpha)
    return ('<div style="max-width:420px;margin:6px 0 0;padding:10px;border:1px solid #374151;border-radius:10px;background:linear-gradient(135deg,#111827,#374151);">'
            '<div style="height:150px;position:relative;background:linear-gradient(135deg,#0f172a 0%,#1e293b 50%,#111827 100%);border-radius:7px;overflow:hidden;">'
            '<div style="position:absolute;inset:0;background:repeating-linear-gradient(45deg,rgba(255,255,255,.03) 0,rgba(255,255,255,.03) 1px,transparent 1px,transparent 8px);"></div>'
            '<div style="position:absolute;{}">{}</div></div>'
            '<div style="color:#cbd5e1;font-size:12px;margin-top:7px;">الموضع: {} · الحجم: {:.0f}% · الشفافية: {:.0f}%</div></div>'.format(anchor, image, label, size, alpha * 100))


# --- AI model lists (were referenced but never defined — fixed in v6.1) ---
GEMINI_MODELS = [
    "gemini-2.5-flash-lite-preview-09-2025",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-pro",
]
G4F_MODELS = [
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-3.5-turbo",
    "claude-3-haiku",
    "llama-3.1-8b",
    "gemini-1.5-flash",
    "gemini-pro",
    "mistral-7b",
    "mixtral-8x7b",
]


def get_local_models():
    """List .gguf models in the models/ folder (local LLM backend)."""
    models_dir = os.path.join(WORKING_DIR, "models")
    if not os.path.isdir(models_dir):
        return []
    return sorted(f for f in os.listdir(models_dir) if f.endswith(".gguf"))



# --- PRESETS DEFINITIONS ---
FACE_PRESETS = {
    "Default (Balanced)": {"thresh": 0.35, "two_face": 0.60, "conf": 0.40, "dead_zone": 150},
    "Stable (Focus Main)": {"thresh": 0.60, "two_face": 0.80, "conf": 0.60, "dead_zone": 200},
    "Sensitive (Catch All)": {"thresh": 0.10, "two_face": 0.40, "conf": 0.30, "dead_zone": 100},
    "High Precision": {"thresh": 0.40, "two_face": 0.65, "conf": 0.75, "dead_zone": 150},
}

EXPERIMENTAL_PRESETS = {
    "Default (Off)": {"focus": False, "mar": 0.03, "score": 1.5, "motion": False, "motion_th": 3.0, "motion_sens": 0.05, "decay": 2.0},
    "Active Speaker (Balanced)": {"focus": True, "mar": 0.03, "score": 1.5, "motion": True, "motion_th": 3.0, "motion_sens": 0.05, "decay": 2.0},
    "Active Speaker (Sensitive)": {"focus": True, "mar": 0.02, "score": 1.0, "motion": True, "motion_th": 2.0, "motion_sens": 0.10, "decay": 1.0},
    "Active Speaker (Stable)": {"focus": True, "mar": 0.05, "score": 2.5, "motion": False, "motion_th": 5.0, "motion_sens": 0.02, "decay": 3.0},
}
# ---------------------------

VIRALS_DIR = os.path.join(WORKING_DIR, "VIRALS")
MODELS_DIR = os.path.join(WORKING_DIR, "models")

# Ensure directories exist
os.makedirs(VIRALS_DIR, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)

# Durable background queue used by the Batch Queue tab. It is intentionally
# single-worker because the legacy pipeline uses one global child process and
# GPU memory should not be duplicated by concurrent renders.
batch_render_queue = render_queue.RenderQueue(
    os.path.join(VIRALS_DIR, ".batch_queue.json"), max_workers=1
)
batch_render_queue.prune()


def _project_path_for_name(project_name):
    """Resolve a dropdown project name without allowing path traversal."""
    try:
        path = project_store.safe_project_path(VIRALS_DIR, project_name)
    except (TypeError, ValueError, OSError):
        return None
    return path


# Global variables
current_process = None
current_batch_job_ids = []
telegram_service = None


def _start_telegram_control():
    """Start the opt-in local Telegram control plane once per WebUI process."""
    global telegram_service
    if telegram_service is not None:
        return telegram_service
    try:
        telegram_service = telegram_control.start_from_environment(
            batch_render_queue,
            project_root=VIRALS_DIR,
        )
    except Exception as exc:
        # Telegram is optional; a bad token or offline API must not break WebUI.
        print("[telegram] disabled after startup error: {}".format(str(exc)[:500]))
        telegram_service = None
    if telegram_service is not None:
        atexit.register(telegram_service.stop)
    return telegram_service

try:
    from .pipeline import build_command
    from .utils import (
        PROGRESS_STAGES,
        build_subtitle_config,
        empty_progress_state,
        normalize_path,
        render_error_html,
        render_progress_html,
        render_tasks_html,
        safe_float,
        safe_int,
        summarize_error,
    )
except ImportError:
    from pipeline import build_command
    from utils import (
        PROGRESS_STAGES,
        build_subtitle_config,
        empty_progress_state,
        normalize_path,
        render_error_html,
        render_progress_html,
        render_tasks_html,
        safe_float,
        safe_int,
        summarize_error,
    )

PROGRESS_ORDER = PROGRESS_STAGES
_safe_int = safe_int
_safe_float = safe_float
_normalize_path = normalize_path
_build_subtitle_config = build_subtitle_config


# ---------------------------------------------------------------------------
# v6.1 fixes — helpers that were referenced by the UI but never defined
# (face presets, experimental presets, subtitle template persistence)
# ---------------------------------------------------------------------------

TEMPLATES_FILE = os.path.join(WORKING_DIR, "subtitle_templates.json")


def load_templates():
    """All saved subtitle/settings templates ({} when none)."""
    if not os.path.exists(TEMPLATES_FILE):
        return {}
    try:
        with open(TEMPLATES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_template(name, payload):
    """Persist a template dict. Returns an error string or None."""
    templates = load_templates()
    templates[name] = payload
    try:
        with open(TEMPLATES_FILE, "w", encoding="utf-8") as f:
            json.dump(templates, f, ensure_ascii=False, indent=2)
        return None
    except Exception as e:
        return str(e)


def template_choices():
    return sorted(load_templates().keys())


def apply_face_preset(preset_name):
    preset = FACE_PRESETS.get(preset_name, {})
    return (
        gr.update(value=preset.get("thresh", 0.35)),
        gr.update(value=preset.get("two_face", 0.60)),
        gr.update(value=preset.get("conf", 0.40)),
        gr.update(value=preset.get("dead_zone", 150)),
    )


def apply_experimental_preset(preset_name):
    preset = EXPERIMENTAL_PRESETS.get(preset_name, {})
    return (
        gr.update(value=bool(preset.get("focus", False))),
        gr.update(value=preset.get("mar", 0.03)),
        gr.update(value=preset.get("score", 1.5)),
        gr.update(value=bool(preset.get("motion", False))),
        gr.update(value=preset.get("motion_th", 3.0)),
        gr.update(value=preset.get("motion_sens", 0.05)),
        gr.update(value=preset.get("decay", 2.0)),
    )


# ---------------------------------------------------------------------------
# Persistent AI settings (v6.9) — save the Gemini key once, never retype it
# ---------------------------------------------------------------------------

_KEY_SOURCE_LABELS = {
    settings_store.KEY_SOURCE_ENV: i18n("from environment variable"),
    settings_store.KEY_SOURCE_SECURE: i18n("from encrypted store"),
    settings_store.KEY_SOURCE_FILE: i18n("saved in api_config.json"),
    settings_store.KEY_SOURCE_NONE: "",
}


def settings_status_text(api_key=None, api_keys=None, key_mode=None):
    """Show masked Gemini key count and the selected rotation policy."""
    saved = settings_store.load_ui_settings()
    keys = list(api_keys if api_keys is not None else saved.get("api_keys", []))
    if api_key and not keys:
        keys = [api_key]
    keys = [str(key or "").strip() for key in keys if str(key or "").strip()]
    mode = key_mode or saved.get("key_mode", "auto")
    if keys:
        masks = ", ".join("K{} {}".format(i + 1, settings_store.mask_key(key)) for i, key in enumerate(keys[:3]))
        src = _KEY_SOURCE_LABELS.get(saved["key_source"], "")
        suffix = " — ✅ {}{}".format(i18n("saved"), " ({} )".format(src) if src else "")
        policy = i18n("automatic rotation") if mode == "auto" else i18n("fixed key {}" ).format(mode)
        return "🔑 **Gemini API:** {}\n\n🔁 **{}:** {}{}".format(masks, i18n("Key policy"), policy, suffix)
    return "⚠️ **{}:** {}".format(
        i18n("Gemini API Keys"),
        i18n("not set — add up to three keys and save the settings"))


def _model_choices_for(backend, saved_model):
    """Model dropdown choices/value per backend, keeping the saved model."""
    if backend == "gemini":
        choices, default = list(GEMINI_MODELS), GEMINI_MODELS[1]
        model_visible, refresh_visible, api_visible = True, False, True
    elif backend == "g4f":
        choices, default = list(G4F_MODELS), G4F_MODELS[5]
        model_visible, refresh_visible, api_visible = True, False, False
    elif backend == "local":
        models = get_local_models()
        choices = models if models else [i18n("No models found")]
        default = choices[0]
        model_visible, refresh_visible, api_visible = True, True, False
    else:
        choices, default = [], saved_model or ""
        model_visible, refresh_visible, api_visible = False, False, False
    val = saved_model or default
    if val and val not in choices:
        choices = choices + [val]
    return choices, val, model_visible, refresh_visible, api_visible


def load_saved_settings():
    """On UI load: prefill backend, three keys, policy and model."""
    s = settings_store.load_ui_settings()
    backend = s["ai_backend"]
    choices, val, model_visible, refresh_visible, api_visible = _model_choices_for(
        backend, s["ai_model"])
    keys = list(s.get("api_keys", []))[:3]
    keys += [""] * (3 - len(keys))
    chunk = s["chunk_size"]
    if backend == "local" and not chunk:
        chunk = 30000
    return (
        gr.update(value=backend),
        gr.update(value=keys[0], visible=api_visible),
        gr.update(value=keys[1], visible=api_visible),
        gr.update(value=keys[2], visible=api_visible),
        gr.update(value=s.get("key_mode", "auto"), visible=api_visible),
        gr.update(choices=choices, value=val, visible=model_visible),
        gr.update(visible=refresh_visible),
        gr.update(value=chunk),
        settings_status_text(api_keys=keys, key_mode=s.get("key_mode", "auto")),
    )


def _save_and_status(backend, key1, key2, key3, key_mode, model, chunk, note=None):
    keys = [key1, key2, key3]
    ok, err = settings_store.save_ui_settings(
        ai_backend=backend, api_keys=keys, key_mode=key_mode,
        ai_model=model, chunk_size=chunk)
    status = settings_status_text(api_keys=keys, key_mode=key_mode)
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    if ok:
        tail = "💾 {} {}".format(note or i18n("Settings saved automatically"), stamp)
    else:
        tail = "❌ {}: {}".format(i18n("Error saving settings"), err)
    return status + "\n\n" + tail


def on_backend_change(backend, key1, key2, key3, key_mode, model, chunk):
    """Backend switched: refresh model choices and persist all key slots."""
    show_api, model_upd, refresh_upd, chunk_upd = update_ai_ui(backend)
    if backend == "manual":
        chunk_upd = gr.update(value=chunk)
    status = _save_and_status(backend, key1, key2, key3, key_mode, model, chunk)
    hidden = gr.update(visible=bool(show_api.value) if hasattr(show_api, "value") else backend == "gemini")
    return hidden, hidden, hidden, hidden, model_upd, refresh_upd, chunk_upd, status


def on_settings_changed(backend, key1, key2, key3, key_mode, model, chunk):
    return _save_and_status(backend, key1, key2, key3, key_mode, model, chunk)


def save_settings_click(backend, key1, key2, key3, key_mode, model, chunk):
    return _save_and_status(backend, key1, key2, key3, key_mode, model, chunk,
                            note=i18n("Settings saved"))


def test_api_connection(backend, key1, key2, key3, key_mode, model):
    """Test the selected key without printing its value."""
    if backend != "gemini":
        return "ℹ️ " + i18n("Connection test is only available for Gemini.")
    keys = [str(key or "").strip() for key in (key1, key2, key3) if str(key or "").strip()]
    if key_mode in {"1", "2", "3"}:
        selected = keys[int(key_mode) - 1] if int(key_mode) <= len(keys) else ""
    else:
        selected = keys[0] if keys else ""
    ok, msg = settings_store.test_gemini_connection(
        selected, model if model in GEMINI_MODELS else "gemini-2.5-flash")
    if ok:
        return "✅ " + i18n("Connection OK — the selected key works.")
    return "❌ " + i18n("Connection failed:") + " " + str(msg)[:300]


# ---------------------------------------------------------------------------
# v6.9.2 — remember EVERY WebUI form field (not just the API key). The list
# below is filled with the real components once the UI is built, then wired
# with a single autosave + a demo.load restore.
# ---------------------------------------------------------------------------

PREF_FIELDS = []  # [(component, key), ...] — populated after the UI is built

# keys → i18n label for the restore-status line (optional; None hides it)
_PREF_SAVE_KEYS = {"platform": "Platform template", "safety_mode": "Safety filter"}


def _collect_prefs():
    """Read current values of every persisted form field."""
    prefs = {}
    for comp, key in PREF_FIELDS:
        try:
            prefs[key] = comp.value
        except Exception:
            pass
    return prefs


def autosave_webui_prefs():
    """Persist the whole form (called on every field change / run start)."""
    ok, err = settings_store.save_webui_prefs(_collect_prefs())
    if ok:
        return ""
    return "❌ {}: {}".format(i18n("Error saving settings"), err)


def restore_webui_prefs():
    """Apply saved form preferences on UI load."""
    prefs = settings_store.load_webui_prefs()
    if not prefs:
        return [gr.update() for _ in PREF_FIELDS]
    return [gr.update(value=prefs.get(key)) for _, key in PREF_FIELDS]


def kill_process():
    global current_process
    queue_cancelled = 0
    try:
        for job in batch_render_queue.active():
            batch_render_queue.cancel(job.id)
            queue_cancelled += 1
    except Exception:
        queue_cancelled = 0
    if current_process:
        try:
            parent = psutil.Process(current_process.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
            current_process = None
            state = empty_progress_state(tr("Process stopped by user."))
            return (
                tr("Process terminated."),
                gr.update(value=tr("Start Processing"), interactive=True),
                gr.update(interactive=False),
                render_progress_html(state),
                render_tasks_html(state),
                render_error_html([tr("Process stopped by user.")]),
            )
        except Exception as e:
            return (tr("Error terminating process: {}").format(e), gr.update(), gr.update(), gr.update(), gr.update(), gr.update())
    message = tr("Cancellation requested for {} queue job(s).").format(queue_cancelled) if queue_cancelled else tr("No process running.")
    state = empty_progress_state(message)
    return (message, gr.update(), gr.update(interactive=False), render_progress_html(state), render_tasks_html(state), render_error_html([]))



def _expected_project_clip_count(project_path):
    """Return the current segment count used as the upload completeness check."""
    path = os.path.join(project_path, "viral_segments.txt")
    try:
        with open(path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
        segments = data.get("segments", []) if isinstance(data, dict) else []
        return len(segments) if isinstance(segments, list) else 0
    except (OSError, ValueError, TypeError):
        return 0


def run_viral_cutter(input_source, project_name, url, video_file, segments, viral, themes, min_duration, max_duration, model, transcription_device, ai_backend, api_key, ai_model_name, chunk_size, workflow, face_model, face_mode, face_detect_interval, no_face_mode, 
                     face_filter_thresh, face_two_thresh, face_conf_thresh, face_dead_zone, focus_active_speaker, active_speaker_mar, active_speaker_score_diff, include_motion, active_speaker_motion_threshold, active_speaker_motion_sensitivity, active_speaker_decay,
                     face_smoothing, face_headroom,
                     use_custom_subs, font_name, font_size, font_color, highlight_color, outline_color, outline_thickness, shadow_color, shadow_size, is_bold, is_italic, is_uppercase, vertical_pos, alignment,
                     h_size, w_block, gap, mode, under, strike, border_s, remove_punc, caption_animation="none", auto_emoji=False, video_quality=None, use_youtube_subs=True, translate_target=None, safety_mode="block", safety_ai=True,
                     platform=None, metadata_gate=None, title_language=None, polish=False, music=None, logo=None,
                     broll=None, broll_query=None, broll_opacity=None,
                     sfx_dir=None, sfx_volume=None,
                     cookies_browser=None, sponsorblock=None, live_wait_minutes=None, output_aspect=None, reframe_mode=None,
                     force_new_segments=False, visual_check="auto", visual_gate="warn",
                     visual_frames=4, visual_model=None, auto_download_visual=False,
                     watermark_position="bottom-right", watermark_size=0.12, watermark_opacity=0.90,
                     require_youtube_connection=True, auto_upload_after_processing=False,
                     auto_upload_dry_run=True, auto_upload_source="auto",
                     auto_upload_specific_file=None, auto_upload_privacy="private",
                     auto_upload_publish_at="", auto_upload_public_confirm=False,
                     auto_upload_interval_minutes=60, auto_upload_oauth_file=None):

    # NOTE: parameter order MUST match the `inputs=[...]` order of every
    # .click() that targets this function (start / review-render / batch).
    # v6.8 fix: the tail used to be (platform, polish, music, logo,
    # metadata_gate, cookies, title_language) while the UI sent (platform,
    # metadata_gate, title_language, polish, music, logo, cookies) — so
    # polish was always "warn" (truthy → --polish on) and cookies/title
    # language selections silently landed in the wrong parameters.
    
    global current_process
    progress_state = empty_progress_state(i18n("Starting"))
    error_items = []
    logs = []
    project_folder_path = None
    pipeline_completed = False

    def record_project_state(status, error=None):
        if not project_folder_path or not os.path.isdir(project_folder_path):
            return
        try:
            outputs = []
            for root, _dirs, files in os.walk(project_folder_path):
                for filename in files:
                    if filename.lower().endswith(".mp4") and filename.lower() != "input.mp4":
                        outputs.append(os.path.relpath(os.path.join(root, filename), project_folder_path))
            outputs.sort()
            project_store.update_manifest(
                project_folder_path,
                status=status,
                outputs=outputs[:500],
                last_error=(str(error)[:4000] if error else None),
            )
            project_store.append_event(
                project_folder_path,
                "pipeline_" + str(status),
                {"outputs": len(outputs), "error": str(error)[:500] if error else None},
            )
        except Exception as exc:
            logs.append("Project state warning: {}".format(exc))

    def fail(message, *, keep_start_enabled=False):
        error_items.append(message)
        progress_state["current"] = message
        return (
            "\n".join(logs + [f"ERROR: {message}"]),
            gr.update(value=i18n("Start Processing"), interactive=True),
            gr.update(visible=False, interactive=not keep_start_enabled),
            None,
            render_progress_html(progress_state),
            render_tasks_html(progress_state),
            render_error_html(error_items),
        )

    def set_progress(stage, percent, message):
        progress_state[stage] = {"percent": int(percent), "message": message}
        progress_state["current"] = message
        progress_state["overall"] = int(sum(progress_state[s]["percent"] for s in PROGRESS_ORDER) / len(PROGRESS_ORDER))

    def emit_log(message):
        logs.append(message)
        return "\n".join(logs)

    validation = validate_processing_config(
        input_source, project_name, url, video_file, segments,
        min_duration, max_duration, workflow, ai_backend,
        transcription_device, safety_mode, visual_check, visual_model,
        logo, music, auto_upload_after_processing, auto_upload_dry_run,
        auto_upload_source, auto_upload_specific_file, auto_upload_privacy,
        auto_upload_publish_at, auto_upload_public_confirm,
        auto_upload_interval_minutes,
    )
    if validation["errors"]:
        detail = "❌ لا يمكن بدء المعالجة قبل إصلاح الإعدادات:\n" + "\n".join(
            "- " + str(item) for item in validation["errors"]
        )
        yield fail(detail)
        return
    for warning in validation["warnings"]:
        logs.append("⚠️ " + str(warning))

    try:
        set_progress("download", 0, i18n("Preparing"))
        emit_log(i18n("Preparing run..."))
        yield "", gr.update(value=i18n("Running..."), interactive=False), gr.update(visible=True, interactive=True), None, render_progress_html(progress_state), render_tasks_html(progress_state), render_error_html(error_items)

        input_source = input_source or "YouTube URL"

        source_args = []
        if input_source == "Existing Project":
            if not project_name:
                yield fail(i18n("Error: No project selected."))
                return
            try:
                full_project_path = project_store.safe_project_path(VIRALS_DIR, project_name)
            except ValueError:
                yield fail(i18n("Error: Project path not found."))
                return
            project_folder_path = full_project_path
            if not os.path.isdir(full_project_path):
                yield fail(i18n("Error: Project path not found."))
                return
            project_store.load_manifest(full_project_path, create=True, name=project_name)
            source_args = ["--project-path", full_project_path]
        elif input_source == "Upload Video":
            if not video_file:
                yield fail(i18n("Error: No video file uploaded."))
                return

            # Gradio gives us a temporary local path. Keep that file in place:
            # copying a large local video into VIRALS wastes disk space and
            # creates duplicate media. The project manifest stores a reference
            # so the pipeline can resume without moving the original file.
            local_video_path = getattr(video_file, "name", video_file)
            if isinstance(local_video_path, dict):
                local_video_path = local_video_path.get("path") or local_video_path.get("name")
            local_video_path = os.path.abspath(os.fspath(local_video_path)) if local_video_path else ""
            if not os.path.isfile(local_video_path):
                yield fail(i18n("Error: The selected local video file is not accessible."))
                return

            original_filename = os.path.basename(local_video_path)
            name_no_ext = os.path.splitext(original_filename)[0]
            safe_name = "".join([c for c in name_no_ext if c.isalnum() or c in " _-"]).strip() or "Untitled_Upload"
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            project_name_upload = f"{safe_name}_{timestamp}"
            project_path, _manifest = project_store.create_project(
                VIRALS_DIR,
                project_name_upload,
                source={
                    "type": "local",
                    "path": local_video_path,
                    "filename": original_filename,
                    "managed": False,
                },
                settings={"created_from": "webui", "storage": "external_reference"},
                exist_ok=False,
            )
            project_folder_path = project_path
            project_store.append_event(
                project_path,
                "local_source_registered",
                {"filename": original_filename, "path": local_video_path, "copied": False},
            )
            source_args = ["--project-path", project_path, "--skip-youtube-subs"]
            emit_log("يتم استخدام الفيديو المحلي مباشرة؛ لم يتم إنشاء نسخة مكررة داخل VIRALS.")
        else:
            if not url:
                yield fail(i18n("Error: No URL provided."))
                return
            source_args = ["--url", url]
            if video_quality:
                source_args.extend(["--video-quality", video_quality])
            if not use_youtube_subs:
                source_args.append("--skip-youtube-subs")

        # The preflight is intentionally after project/source registration but
        # before subtitles, download, transcription or cutting. This lets the
        # circuit breaker use the same durable registry as the target project.
        if require_youtube_connection or auto_upload_after_processing:
            try:
                preflight = prepare_youtube_preflight(
                    project_folder_path,
                    auto_upload_oauth_file,
                    dry_run=bool(auto_upload_dry_run),
                    privacy_status=auto_upload_privacy or "private",
                    publish_at=auto_upload_publish_at or None,
                    schedule_interval_minutes=auto_upload_interval_minutes,
                    expected_clips=segments,
                )
                channel = preflight["channel"]
                emit_log("[oauth] ✅ قناة YouTube جاهزة قبل بدء التقطيع: {} ({})".format(
                    channel.get("title") or "قناة YouTube", channel.get("id") or "غير متاح"))
                emit_log("[upload] الوضع المحدد: {} — لا يمكن التحقق من quota مسبقاً؛ ستُعالج أخطاء quota لكل مقطع بأمان.".format(
                    "Dry Run (محاكاة، لا رفع فعلي)" if auto_upload_dry_run else "رفع فعلي بعد نجاح pipeline"))
                for warning in preflight.get("warnings", []):
                    emit_log("⚠️ " + warning)
            except Exception as exc:
                yield fail("❌ جاهزية YouTube غير مكتملة؛ تم إيقاف المعالجة قبل التقطيع: {}".format(str(exc)[:500]))
                return

        subtitle_config_path = None
        if use_custom_subs:
            subtitle_config = _build_subtitle_config(
                font_name, font_size, font_color, highlight_color, outline_color,
                outline_thickness, shadow_color, shadow_size, is_bold, is_italic,
                is_uppercase, vertical_pos, alignment, h_size, w_block, gap, mode,
                under, strike, border_s, remove_punc, caption_animation, auto_emoji,
            )
            subtitle_config_path = os.path.join(WORKING_DIR, "temp_subtitle_config.json")
            with open(subtitle_config_path, "w", encoding="utf-8") as f:
                json.dump(subtitle_config, f, indent=4)

        # v6.9 preflight: fail fast with a clear message instead of letting
        # the run die mid-pipeline on an obvious configuration error.
        if ai_backend == "gemini":
            preflight_key = (api_key or "").strip()
            if not preflight_key:
                # the field may be empty even though a key is saved — the CLI
                # resolves env/secure/file on its own, so only hard-fail when
                # NOTHING is configured anywhere.
                saved = settings_store.load_ui_settings()
                if not saved["api_key"]:
                    yield fail(i18n("Error: Gemini API key is missing. Paste it in the AI settings (saved automatically) or set the GEMINI_API_KEY environment variable."))
                    return
            elif not settings_store.looks_like_gemini_key(preflight_key):
                emit_log(i18n("Warning: the API key does not look like a Gemini key (usually starts with 'AIza'). Continuing anyway."))

        cmd = build_command(
            MAIN_SCRIPT_PATH, source_args,
            segments=segments, viral=viral, themes=themes,
            min_duration=min_duration, max_duration=max_duration, model=model,
            transcription_device=transcription_device,
            ai_backend=ai_backend, api_key=api_key, ai_model_name=ai_model_name,
            chunk_size=chunk_size, workflow=workflow, face_model=face_model,
            face_mode=face_mode, face_detect_interval=face_detect_interval,
            no_face_mode=no_face_mode, face_filter_thresh=face_filter_thresh,
            face_two_thresh=face_two_thresh, face_conf_thresh=face_conf_thresh,
            face_dead_zone=face_dead_zone, focus_active_speaker=focus_active_speaker,
            active_speaker_mar=active_speaker_mar,
            active_speaker_score_diff=active_speaker_score_diff,
            include_motion=include_motion,
            active_speaker_motion_threshold=active_speaker_motion_threshold,
            active_speaker_motion_sensitivity=active_speaker_motion_sensitivity,
            active_speaker_decay=active_speaker_decay,
            face_smoothing=face_smoothing, face_headroom=face_headroom,
            translate_target=translate_target,
            subtitle_config_path=subtitle_config_path,
            safety_mode=safety_mode,
            safety_ai="on" if safety_ai else "off",
            # v6 features (Roadmap 5.2 / Sprint 3 / 2.4)
            platform=platform,
            polish=polish,
            music=music,
            logo=logo,
            broll=broll,
            broll_query=broll_query,
            broll_opacity=broll_opacity,
            sfx_dir=sfx_dir,
            sfx_volume=sfx_volume,
            metadata_gate=metadata_gate,
            cookies_browser=cookies_browser,
            sponsorblock=sponsorblock,
            live_wait_minutes=live_wait_minutes,
            title_language=title_language,
            output_aspect=output_aspect,
            reframe_mode=reframe_mode,
            force_new_segments=force_new_segments,
            visual_check=visual_check,
            visual_gate=visual_gate,
            visual_frames=visual_frames,
            visual_model=visual_model,
            auto_download_visual=auto_download_visual,
            watermark_position=watermark_position,
            watermark_size=watermark_size,
            watermark_opacity=watermark_opacity,
        )

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
        # SECURITY: the API key must never appear in argv/process listings
        # (pipeline.py deliberately omits --api-key). Hand it to the child
        # through its environment instead — but never clobber a key the user
        # already exported explicitly.
        if ai_backend == "gemini":
            try:
                _saved_ai = settings_store.load_ui_settings()
                _resolved_keys = [str(key).strip() for key in _saved_ai.get("api_keys", []) if str(key or "").strip()][:3]
                _resolved_key = (_saved_ai.get("api_key") or "").strip()
                _key_mode = _saved_ai.get("key_mode", "auto")
            except Exception:
                _resolved_keys, _resolved_key, _key_mode = [], "", "auto"
            if _resolved_keys:
                env["VIRALCUTTER_GEMINI_KEYS"] = json.dumps(_resolved_keys, ensure_ascii=False)
                env["VIRALCUTTER_GEMINI_KEY_MODE"] = str(_key_mode)
                env.setdefault("VIRALCUTTER_GEMINI_KEY", _resolved_keys[0])
            elif _resolved_key:
                env.setdefault("VIRALCUTTER_GEMINI_KEY", _resolved_key)
        # mask the API key in the echoed command — never print secrets to
        # the visible log (v6.9 fix: keys leaked into screenshots/logs before)
        def _mask_cmd(cmd_list):
            masked = []
            skip_next = False
            for part in cmd_list:
                if skip_next:
                    masked.append(settings_store.mask_key(str(part)) or "***")
                    skip_next = False
                    continue
                masked.append(str(part))
                if part == "--api-key":
                    skip_next = True
            return " ".join(masked)
        debug_cmd = _mask_cmd([x for x in cmd if x])
        emit_log(f"Command: {debug_cmd}")
        yield "\n".join(logs), gr.update(value=i18n("Running..."), interactive=False), gr.update(visible=True, interactive=True), None, render_progress_html(progress_state), render_tasks_html(progress_state), render_error_html(error_items)

        current_process = subprocess.Popen(
            cmd,
            cwd=WORKING_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            env=env,
        )
        project_folder_path = None
        last_update_time = time.time()
        current_buffer = []

        while True:
            line = current_process.stdout.readline()
            if not line and current_process.poll() is not None:
                break
            if not line:
                continue

            line = line.rstrip("\n")
            if line.startswith("DEVICE|"):
                try:
                    _, actual, requested, name = (line.split("|", 3) + [""])[:4]
                    if actual == "cuda":
                        message = "يعمل الآن بواسطة NVIDIA GPU / CUDA{}".format(" — " + name if name else "")
                    elif actual == "cpu":
                        message = "يعمل الآن بواسطة CPU"
                    else:
                        message = "الجهاز المطلوب غير متاح: {}".format(requested)
                    logs.append("[الجهاز] " + message)
                    progress_state["transcribe"] = {"percent": max(22, int(progress_state.get("transcribe", {}).get("percent", 0))), "message": message}
                    progress_state["current"] = message
                    progress_state["overall"] = int(sum(progress_state[s]["percent"] for s in PROGRESS_ORDER) / len(PROGRESS_ORDER))
                    yield "\n".join(logs), gr.update(visible=True, interactive=False), gr.update(visible=True, interactive=True), None, render_progress_html(progress_state), render_tasks_html(progress_state), render_error_html(error_items)
                except Exception as e:
                    error_items.append({"title": "Bad device status: {}".format(e), "detail": str(e), "hint": ""})
                continue
            if line.startswith("PROGRESS|"):
                try:
                    _, stage, percent, message = line.split("|", 3)
                    if stage in progress_state:
                        progress_state[stage] = {"percent": int(percent), "message": message}
                        progress_state["current"] = message
                        progress_state["overall"] = int(sum(progress_state[s]["percent"] for s in PROGRESS_ORDER) / len(PROGRESS_ORDER))
                except Exception as e:
                    error_items.append({"title": "Bad progress line: {}".format(e),
                                         "detail": str(e), "hint": ""})
                continue

            current_buffer.append(line)
            if len(current_buffer) > 200:
                current_buffer = current_buffer[-200:]
            logs.append(line)
            if len(logs) > 1000:
                del logs[: len(logs) - 1000]
            if "Project Folder:" in line:
                parts = line.split("Project Folder:")
                if len(parts) > 1:
                    project_folder_path = parts[1].strip()

            current_time = time.time()
            if current_time - last_update_time > 0.2:
                yield "\n".join(logs), gr.update(visible=True, interactive=False), gr.update(visible=True, interactive=True), None, render_progress_html(progress_state), render_tasks_html(progress_state), render_error_html(error_items)
                last_update_time = current_time

        return_code = current_process.poll()
        if return_code not in (0, None):
            tail = "\n".join(current_buffer[-30:])
            title, detail, hint = summarize_error(
                tail or "Process exited with code {}".format(return_code))
            error_items.append({"title": title, "detail": detail,
                                "hint": hint, "code": return_code})
            record_project_state("failed", tail or "process exited with code {}".format(return_code))
            yield "\n".join(logs), gr.update(value=i18n("Start Processing"), interactive=True), gr.update(visible=True, interactive=True), None, render_progress_html(progress_state), render_tasks_html(progress_state), render_error_html(error_items)
            return

        pipeline_completed = True
        yield "\n".join(logs), gr.update(visible=True, interactive=False), gr.update(visible=True, interactive=True), None, render_progress_html(progress_state), render_tasks_html(progress_state), render_error_html(error_items)
    except FileNotFoundError as e:
        yield fail(f"{i18n('Error: Missing file or tool.')} {e}")
        return
    except subprocess.CalledProcessError as e:
        yield fail(f"{i18n('Error: Process failed.')} {e}")
        return
    except Exception as e:
        title, detail, hint = summarize_error(str(e))
        error_items.append({"title": "Error running process: {}".format(title),
                            "detail": detail, "hint": hint})
        yield "\n".join(logs + [f"Error running process: {str(e)}"]), gr.update(visible=True, interactive=False), gr.update(visible=True, interactive=True), None, render_progress_html(progress_state), render_tasks_html(progress_state), render_error_html(error_items)
    finally:
        if current_process:
            if current_process.stdout:
                try:
                    current_process.stdout.close()
                except Exception:
                    pass
            if current_process.poll() is None:
                try:
                    current_process.terminate()
                    current_process.wait(timeout=5)
                except Exception:
                    try:
                        current_process.kill()
                    except Exception:
                        pass
            current_process = None
        time.sleep(0.5)
        if use_custom_subs:
            try:
                subtitle_config_path = os.path.join(WORKING_DIR, "temp_subtitle_config.json")
                if os.path.exists(subtitle_config_path):
                    os.remove(subtitle_config_path)
            except Exception:
                pass

    if project_folder_path and os.path.exists(project_folder_path):
        record_project_state("completed")
    html_output = ""
    if project_folder_path and os.path.exists(project_folder_path):
        try:
            from scripts.project_report import write_report
            write_report(project_folder_path, html_report=True)
        except Exception as report_error:
            emit_log(i18n("Project report could not be generated: {}").format(report_error))
        html_output = library.generate_project_gallery(project_folder_path, is_full_path=True)
    else:
        html_output = f"<h3>{i18n('Error: Project folder could not be determined from logs.')}</h3>"
    # Optional automatic publish is deliberately the final stage and only runs
    # after a successful pipeline. It reuses the same upload gate as manual UI.
    if pipeline_completed and auto_upload_after_processing and project_folder_path and os.path.isdir(project_folder_path):
        selected_file = file_inputs.first_path(auto_upload_specific_file)
        upload_source = auto_upload_source
        if upload_source == "auto" and str(polish).lower() != "on":
            # Do not select a stale final_polished/ from an earlier run when
            # this run deliberately disabled the enhancement pass.
            upload_source = "final"
        clips = publish_panel.list_clips(project_folder_path, upload_source, selected_file)
        expected_clips = _expected_project_clip_count(project_folder_path)
        incomplete_upload = (
            upload_source not in {"specific_file", "file"}
            and expected_clips > 0
            and len(clips) < expected_clips
        )
        polish_issue = None
        if not auto_upload_dry_run and str(polish).lower() == "on":
            polish_report_path = os.path.join(project_folder_path, "polish_report.json")
            try:
                with open(polish_report_path, "r", encoding="utf-8") as stream:
                    polish_report = json.load(stream)
                summary = polish_report.get("summary", {})
                degraded = int(summary.get("degraded", 0) or 0)
                if degraded:
                    polish_issue = "تم منع الرفع الحقيقي لأن {} مقطعاً خرج من polish بحالة fallback/degraded.".format(degraded)
            except (OSError, ValueError, TypeError) as exc:
                polish_issue = "تعذر التحقق من polish_report قبل الرفع الحقيقي: {}".format(str(exc)[:220])
        if polish_issue:
            error_items.append({"title": "التحسين الاحترافي", "detail": polish_issue, "hint": "راجع polish_report.json وأصلح المرحلة الفاشلة ثم أعد التشغيل."})
            logs.append("[auto-upload] ⛔ " + polish_issue)
        elif not auto_upload_dry_run and str(auto_upload_privacy or "private").lower() == "public" and not auto_upload_public_confirm:
            error_items.append({"title": "الرفع التلقائي", "detail": "تم إيقاف النشر العام لعدم وجود تأكيد صريح.", "hint": "فعّل تأكيد النشر العام قبل إلغاء وضع Dry Run."})
            logs.append("[auto-upload] ❌ تم منع النشر العام دون تأكيد.")
        elif not clips:
            error_items.append({"title": "الرفع التلقائي", "detail": "لم يتم العثور على ملفات MP4 في مصدر الرفع المحدد.", "hint": "اختر final_polished أو final أو cuts أو ملفاً محدداً."})
            logs.append("[auto-upload] ❌ لا توجد ملفات صالحة من مصدر الرفع: {}".format(upload_source))
        elif incomplete_upload:
            error_items.append({
                "title": "الرفع التلقائي",
                "detail": "المخرجات غير مكتملة: وُجد {} ملفاً من أصل {} segment حالياً.".format(len(clips), expected_clips),
                "hint": "راجع القص والتقرير ثم أعد المحاولة؛ لن يبدأ رفع دفعة ناقصة.",
            })
            logs.append("[auto-upload] ⛔ أوقف الرفع لأن الدفعة ناقصة ({}/{}).".format(len(clips), expected_clips))
        else:
            logs.append("[auto-upload] بدء رفع {} ملفاً من {} — dry_run={}".format(len(clips), upload_source, auto_upload_dry_run))
            for upload_update in publish_panel.stream_upload_batch(
                project_folder_path, "youtube", clips, bool(auto_upload_dry_run), "warn",
                privacy_status=auto_upload_privacy or "private",
                publish_at=auto_upload_publish_at or None,
                schedule_interval_minutes=auto_upload_interval_minutes,
                require_existing_auth=True,
                public_confirm=bool(auto_upload_public_confirm),
            ):
                logs.append(str(upload_update))
                if len(logs) > 1000:
                    del logs[: len(logs) - 1000]
                yield "\n".join(logs), gr.update(visible=True, interactive=False), gr.update(visible=True, interactive=True), html_output, render_progress_html(progress_state), render_tasks_html(progress_state), render_error_html(error_items)
            batch_report_path = os.path.join(project_folder_path, "publish_batch_report.json")
            try:
                with open(batch_report_path, "r", encoding="utf-8") as stream:
                    batch_summary = (json.load(stream) or {}).get("summary") or {}
                if int(batch_summary.get("failed", 0) or 0) or int(batch_summary.get("blocked", 0) or 0):
                    error_items.append({
                        "title": "نتيجة الرفع الدفعي",
                        "detail": "لم تكتمل الدفعة: failed={}، blocked={}، راجع publish_batch_report.json.".format(
                            batch_summary.get("failed", 0), batch_summary.get("blocked", 0)),
                        "hint": "أصلح السبب ثم أعد تشغيل retry_failed_only للفاشل فقط.",
                    })
            except (OSError, ValueError, TypeError):
                pass

    set_progress("done", 100, i18n("Completed"))
    yield "\n".join(logs), gr.update(value=tr("Start Processing"), interactive=True), gr.update(visible=True, interactive=False), html_output, render_progress_html(progress_state), render_tasks_html(progress_state), render_error_html(error_items)

def validate_processing_config(input_source, project_name, url, video_file,
                               segments, min_duration, max_duration, workflow,
                               ai_backend, transcription_device, safety_mode,
                               visual_check, visual_model, logo_path, music_path,
                               auto_upload, auto_upload_dry_run, auto_upload_source,
                               auto_upload_specific_file, auto_upload_privacy,
                               auto_upload_publish_at, auto_upload_public_confirm,
                               auto_upload_interval_minutes=60):
    """Validate a run before network access, media download, or OAuth.

    This is deliberately pure validation: it never logs in, uploads, downloads,
    or repairs dependencies. The same function is called by the visible
    «فحص الإعدادات» button and by the processing generator as a final gate.
    """
    errors = []
    warnings = []
    source = input_source or "YouTube URL"
    if source not in {"YouTube URL", "Existing Project", "Upload Video"}:
        errors.append("مصدر الإدخال غير معروف.")
    if source == "YouTube URL" and not batch_queue.is_supported_url(url):
        errors.append("أدخل رابط YouTube صالحاً (watch أو shorts أو live أو youtu.be).")
    if source == "Existing Project":
        if not project_name:
            errors.append("اختر مشروعاً موجوداً قبل البدء.")
        elif not _project_path_for_name(project_name):
            errors.append("المشروع المحدد غير موجود أو غير مسموح به.")
    if source == "Upload Video":
        local_video = file_inputs.first_path(video_file)
        if not local_video or not os.path.isfile(local_video):
            errors.append("اختر ملف فيديو محلياً صالحاً قبل البدء.")

    try:
        clip_count = int(float(segments))
        if clip_count < 1 or clip_count > 50:
            errors.append("عدد المقاطع يجب أن يكون بين 1 و50.")
    except (TypeError, ValueError):
        errors.append("عدد المقاطع يجب أن يكون رقماً صحيحاً.")
    try:
        low, high = float(min_duration), float(max_duration)
        if low <= 0 or high <= 0 or high < low:
            errors.append("المدة الدنيا والعليا غير متوافقتين.")
        if high > 900:
            warnings.append("المدة العليا أكبر من 15 دقيقة؛ قد لا تناسب القالب العمودي.")
    except (TypeError, ValueError):
        errors.append("أدخل مدداً رقمية صحيحة للمقاطع.")

    if transcription_device not in {None, "auto", "cpu", "cuda"}:
        errors.append("وضع جهاز التفريغ غير معروف.")
    if transcription_device == "cuda":
        warnings.append("تم اختيار CUDA يدوياً؛ راجع بطاقة حالة الجهاز قبل التشغيل للتأكد من توفر RTX.")
    if ai_backend == "gemini":
        # The key may be loaded from settings_store by the child process; this
        # validator must not block a valid saved setup or print secret values.
        warnings.append("سيُستخدم مفتاح Gemini المحفوظ إن وُجد؛ لا تُظهر المفاتيح في السجل.")
    if safety_mode in {None, "off"}:
        warnings.append("تم تعطيل حاجز الكلمات الحساسة؛ راجع المحتوى يدوياً قبل النشر.")
    if visual_check == "on" and not visual_model and not auto_upload_dry_run:
        warnings.append("الفحص البصري الإلزامي يحتاج مسار نموذج ONNX محلياً أو إعداد تنزيل صريح.")

    for label, value in (("الشعار", logo_path), ("الموسيقى", music_path)):
        if value:
            path = file_inputs.first_path(value)
            if path and not os.path.isfile(path):
                errors.append("ملف {} غير موجود: {}".format(label, path))
    if auto_upload and auto_upload_source == "specific_file":
        selected = file_inputs.first_path(auto_upload_specific_file)
        if not selected or not os.path.isfile(selected):
            errors.append("اختر ملف MP4 محدداً لمصدر الرفع.")
        elif not str(selected).lower().endswith(".mp4"):
            errors.append("ملف الرفع المحدد يجب أن يكون MP4.")
    try:
        raw_interval = auto_upload_interval_minutes
        if raw_interval is None or (isinstance(raw_interval, str) and not raw_interval.strip()):
            raw_interval = 60
        schedule_interval = float(raw_interval)
        if schedule_interval <= 0 or schedule_interval > 10080:
            errors.append("فاصل الجدولة يجب أن يكون بين 1 و10080 دقيقة.")
    except (TypeError, ValueError):
        errors.append("فاصل الجدولة يجب أن يكون رقماً صحيحاً بالدقائق.")
    if auto_upload and str(auto_upload_privacy or "private").lower() == "public":
        if auto_upload_dry_run:
            warnings.append("النشر العام في Dry Run محاكاة فقط ولا يتصل بالمنصة.")
        elif not auto_upload_public_confirm:
            errors.append("النشر العام الحقيقي متوقف: فعّل تأكيد النشر العام أولاً.")
    if auto_upload_publish_at:
        try:
            stamp = str(auto_upload_publish_at).strip().replace("Z", "+00:00")
            scheduled = datetime.datetime.fromisoformat(stamp)
            if scheduled.tzinfo is None:
                errors.append("وقت الجدولة يجب أن يتضمن منطقة زمنية مثل +00:00 أو Z؛ لن يبدأ التقطيع بدونه.")
            elif scheduled <= datetime.datetime.now(scheduled.tzinfo):
                errors.append("وقت الجدولة يجب أن يكون في المستقبل قبل بدء التقطيع.")
        except ValueError:
            errors.append("وقت الجدولة يجب أن يكون ISO 8601 صالحاً.")
    if auto_upload and not auto_upload_dry_run and auto_upload_privacy == "public":
        warnings.append("سيتم تطبيق بوابة الأمان ومنع التكرار قبل أي اتصال فعلي.")
    if workflow == "Full" and transcription_device == "cpu":
        warnings.append("وضع CPU يعمل لكنه أبطأ على الفيديوهات الطويلة.")
    return {"errors": errors, "warnings": warnings}


def validation_status_html(report):
    """Render validation output as safe, direction-aware HTML."""
    errors = report.get("errors", [])
    warnings = report.get("warnings", [])
    if not errors and not warnings:
        return '<div class="vc-validation vc-validation-ok">✅ الإعدادات الأساسية سليمة ويمكن بدء المعالجة.</div>'
    blocks = []
    if errors:
        blocks.append("<strong>❌ يجب الإصلاح قبل البدء:</strong><br>" + "<br>".join("• " + html_lib.escape(str(item)) for item in errors))
    if warnings:
        blocks.append("<strong>⚠️ تنبيهات:</strong><br>" + "<br>".join("• " + html_lib.escape(str(item)) for item in warnings))
    tone = "vc-validation-error" if errors else "vc-validation-warn"
    return '<div class="vc-validation {}">{}</div>'.format(tone, "<br><br>".join(blocks))


def validate_processing_ui(*args):
    return validation_status_html(validate_processing_config(*args))


def transcription_runtime_status():
    """Return a lightweight, user-facing CPU/GPU and WhisperX status card."""
    lines = ["### حالة التفريغ والجهاز"]
    try:
        import torch
        torch_version = getattr(torch, "__version__", "installed")
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            memory = ""
            try:
                total = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
                memory = " — {:.1f} GB VRAM".format(total)
            except Exception:
                pass
            lines.append("✅ **CUDA / GPU:** {}{} — Torch {}".format(name, memory, torch_version))
            lines.append("💡 الوضع التلقائي يستخدم كرت الشاشة، ويمكن اختيار CPU يدوياً.")
        else:
            lines.append("⚠️ **CPU:** CUDA غير متاحة — Torch {} يعمل على المعالج.".format(torch_version))
    except Exception as exc:
        lines.append("❌ **Torch:** غير متاح — {}".format(str(exc)[:180]))
    try:
        import whisperx
        lines.append("✅ **WhisperX:** {}".format(getattr(whisperx, "__version__", "جاهز")))
    except Exception as exc:
        lines.append("❌ **WhisperX:** غير متاح — {}".format(str(exc)[:180]))
    return "\n\n".join(lines)


css = style.CSS

try:
    from . import header
except ImportError:
    import header

# --- Gradio version compatibility -------------------------------------------
# Gradio 6 moved `theme`/`css` from the Blocks constructor to launch(); on
# Gradio 4/5 they only exist on Blocks. Detect once and route accordingly.
try:
    _GRADIO_MAJOR = int(str(gr.__version__).split(".", 1)[0])
except Exception:
    _GRADIO_MAJOR = 4

vc_theme = gr.themes.Soft(primary_hue="orange", neutral_hue="slate")
# Dark theme: the app CSS targets a dark surface; tint the Gradio theme so
# every component (forms, tables, panels) matches instead of light-on-dark.
vc_theme.set(
    body_background_fill="#0b0b0b",
    body_text_color="#e5e7eb",
    body_text_color_subdued="#94a3b8",
    background_fill_primary="#0b0b0b",
    background_fill_secondary="#111827",
    block_background_fill="#111827",
    block_border_color="#1f2937",
    block_label_text_color="#9ca3af",
    block_info_text_color="#94a3b8",
    input_background_fill="#1f1f1f",
    input_border_color="#333333",
    input_placeholder_color="#6b7280",
    border_color_primary="#1f2937",
    panel_background_fill="#0f172a",
    panel_border_color="#1f2937",
    table_even_background_fill="#111827",
    table_odd_background_fill="#0b0b0b",
    table_border_color="#1f2937",
    table_text_color="#e5e7eb",
    accordion_text_color="#e5e7eb",
    button_secondary_background_fill="#1f2937",
    button_secondary_border_color="#374151",
    button_secondary_text_color="#e5e7eb",
    checkbox_background_color="#1f1f1f",
    checkbox_border_color="#444444",
    checkbox_label_background_fill="#1f2937",
    checkbox_label_background_fill_selected="#f97316",
    checkbox_label_text_color="#e5e7eb",
    checkbox_label_text_color_selected="#ffffff",
    slider_color="#f97316",
    loader_color="#f97316",
    code_background_fill="#1e1e1e",
)
if _GRADIO_MAJOR >= 6:
    _blocks_kwargs = {"title": "OUSSAMA Cutter"}
    _launch_theme_kwargs = {"theme": vc_theme, "css": css}
else:
    _blocks_kwargs = {"title": "OUSSAMA Cutter", "theme": vc_theme, "css": css}
    _launch_theme_kwargs = {}

with gr.Blocks(**_blocks_kwargs) as demo:
    if _GRADIO_MAJOR >= 6:
        # mount_gradio_app has no css param — inject the stylesheet inline so
        # the dark surface applies on every Gradio version.
        gr.HTML("<style>{}</style>".format(css))
    gr.HTML(header.description)
    with gr.Accordion("📡 مراقبة التشغيل", open=False, elem_id="vc-monitor"):
        with gr.Row(elem_classes=["vc-panels"]):
            with gr.Column(scale=1, min_width=280):
                gr.Markdown("### 📊 " + i18n("Progress"))
                progress_panel = gr.HTML(value=render_progress_html(empty_progress_state()))
            with gr.Column(scale=1, min_width=280):
                gr.Markdown("### 🧩 " + i18n("Tasks"))
                tasks_panel = gr.HTML(value=render_tasks_html(empty_progress_state()))
            with gr.Column(scale=1, min_width=280):
                gr.Markdown("### ⚠️ " + i18n("Errors"))
                errors_panel = gr.HTML(value=render_error_html([]))
    with gr.Tabs():
        with gr.Tab("🏠 " + i18n("Home")):
            gr.Markdown(f"### {i18n('Home')}")
            gr.HTML(header.home_quickstart())
            gr.Markdown("### 🔧 " + i18n("System status"))
            with gr.Row():
                home_status_html = gr.HTML(value=header.env_status_html(), scale=6)
                home_check_btn = gr.Button("🔄 " + i18n("Re-check system"), size="sm", scale=1)
            telegram_status_html = gr.HTML(value=telegram_control.status_html())

            def refresh_home_status():
                return header.env_status_html(force=True), telegram_control.status_html()

            home_check_btn.click(
                refresh_home_status,
                outputs=[home_status_html, telegram_status_html],
            )

        with gr.Tab("📥 " + i18n("Create New")):
            with gr.Row(elem_id="vc-create-toolbar", elem_classes=["vc-create-toolbar"]):
                create_config_status = gr.HTML(
                    value='<div class="vc-validation vc-validation-neutral">افحص الإعدادات قبل البدء؛ لن يبدأ أي تنزيل أو تسجيل دخول من هذا الفحص.</div>',
                    scale=5,
                )
                validate_config_btn = gr.Button("✅ فحص الإعدادات قبل البدء", variant="secondary", size="sm", scale=1)
            with gr.Row(elem_id="vc-create-layout"):
                with gr.Column(scale=1):
                    gr.Markdown("### 1️⃣ " + i18n("Source"))
                    gr.Markdown("**إرشاد:** اختر رابط يوتيوب، مشروعاً موجوداً، أو فيديو محلياً. الفيديو المحلي يُستخدم من مكانه ولا يُنسخ إلى VIRALS.", elem_classes=["vc-help-card"])
                    input_source = gr.Radio([(i18n("YouTube URL"), "YouTube URL"), (i18n("Existing Project"), "Existing Project"), (i18n("Upload Video"), "Upload Video")], label=i18n("Input Source"), value="YouTube URL")
                    url_input = gr.Textbox(label=i18n("YouTube URL"), placeholder="https://www.youtube.com/watch?v=...", visible=True)
                    video_upload = gr.File(label=i18n("Drag & drop a video here or click to browse"), file_count="single", file_types=["video"], visible=False, elem_id="video_upload_box")
                    upload_hint = gr.Markdown(i18n("Drop a video here for fastest upload."), visible=False)

                    with gr.Row():
                        video_quality_input = gr.Dropdown(choices=["best", "1080p", "720p", "480p"], label=i18n("Video Quality"), value="best")
                        translate_input = gr.Dropdown(choices=["None", "pt", "en", "es", "fr", "de", "it", "ru", "ja", "ko", "zh-CN", "ar"], label=i18n("Translate Subtitles To"), value="None")
                        use_youtube_subs_input = gr.Checkbox(label=i18n("Use YouTube Subtitles"), value=True, info=i18n("Download and use official subtitles if available. (Recommended, it speeds up the process)"))
                    with gr.Row():
                        force_new_segments_input = gr.Checkbox(
                            label=i18n("Generate new segments (ignore existing)"),
                            value=False,
                            info=i18n("Re-runs the AI analysis from scratch instead of reusing the saved segments (uses API credits)."))

                    project_selector = gr.Dropdown(choices=[], label=i18n("Choose a Project"), visible=False)

                    def on_source_change(source):
                        if source == "YouTube URL":
                            return gr.update(visible=True), gr.update(visible=False), gr.update(visible=False), gr.update(value="Full"), gr.update(visible=False)
                        if source == "Upload Video":
                            return gr.update(visible=False), gr.update(visible=False), gr.update(visible=True), gr.update(value="Full"), gr.update(visible=True)
                        projs = library.get_existing_projects(force_refresh=True)
                        return gr.update(visible=False), gr.update(choices=projs, visible=True), gr.update(visible=False), gr.update(value="Subtitles Only"), gr.update(visible=False)

                    gr.Markdown("### ✂️ " + i18n("Cut & Subtitles"))
                    with gr.Row():
                        segments_input = gr.Number(label=i18n("Number of Clips"), value=3, precision=0)
                        viral_input = gr.Checkbox(label=i18n("Viral Mode"), value=True)
                    themes_input = gr.Textbox(label=i18n("Themes"), placeholder=i18n("funny, sad..."), visible=False)
                    viral_input.change(lambda x: gr.update(visible=not x), viral_input, themes_input)
                    with gr.Row():
                        min_dur_input = gr.Number(label=i18n("Min Duration (s)"), value=15)
                        max_dur_input = gr.Number(label=i18n("Max Duration (s)"), value=90)
                with gr.Column(scale=1):
                    gr.Markdown("### 🤖 " + i18n("AI"))
                    gr.Markdown("**اختيار عملي:** استخدم Auto لاختيار CUDA عند توفر NVIDIA، وأضف حتى ثلاثة مفاتيح Gemini لتدويرها تلقائياً عند نفاد الحصة.", elem_classes=["vc-help-card"])
                    with gr.Row():
                        ai_backend_input = gr.Dropdown(choices=[(i18n("Gemini"), "gemini"), (i18n("G4F"), "g4f"), (i18n("Local (GGUF)"), "local"), (i18n("Manual"), "manual")], label=i18n("AI Backend"), value="gemini", scale=2)
                        api_key_input = gr.Textbox(label=i18n("Gemini API Key 1"), type="password", scale=3)
                    with gr.Row():
                        api_key_2_input = gr.Textbox(label=i18n("Gemini API Key 2"), type="password", visible=True, scale=3)
                        api_key_3_input = gr.Textbox(label=i18n("Gemini API Key 3"), type="password", visible=True, scale=3)
                        gemini_key_mode_input = gr.Dropdown(
                            choices=[(i18n("Auto rotate on quota"), "auto"), (i18n("Always use key 1"), "1"), (i18n("Always use key 2"), "2"), (i18n("Always use key 3"), "3")],
                            label=i18n("Gemini key policy"), value="auto", scale=2)
                    settings_status = gr.Markdown(elem_id="ai_settings_status")
                    with gr.Row():
                        save_settings_btn = gr.Button("💾 " + i18n("Save Settings"), variant="secondary", size="sm", scale=1)
                        test_key_btn = gr.Button("🔌 " + i18n("Test Connection"), variant="secondary", size="sm", scale=1)
                        settings_hint = gr.Markdown("💡 " + i18n("Add up to 3 keys. Auto rotate switches after quota or key errors; keys are masked in the status."), scale=3)
                    with gr.Row():
                        ai_model_input = gr.Dropdown(choices=GEMINI_MODELS, label=i18n("AI Model"), value=GEMINI_MODELS[1], allow_custom_value=True, visible=True, scale=5)
                        refresh_models_btn = gr.Button("🔄", size="sm", visible=False, scale=0, min_width=50)
                        chunk_size_input = gr.Number(label=i18n("Chunk Size"), value=70000, precision=0, scale=2)

                    def update_ai_ui(backend):
                        show_api = (backend == "gemini")
                        show_refresh = (backend == "local")
                        if backend == "gemini":
                            new_choices = GEMINI_MODELS
                            new_val = GEMINI_MODELS[1]
                            new_chunk = 70000
                        elif backend == "g4f":
                            new_choices = G4F_MODELS
                            new_val = G4F_MODELS[5]
                            new_chunk = 70000
                        elif backend == "local":
                            models = get_local_models()
                            new_choices = models if models else [i18n("No models found")]
                            new_val = new_choices[0]
                            new_chunk = 30000
                        else:
                            new_choices = ai_model_input.choices or []
                            new_val = ai_model_input.value
                            new_chunk = chunk_size_input.value
                        return gr.update(visible=show_api), gr.update(choices=new_choices, value=new_val, visible=(backend != "manual")), gr.update(visible=show_refresh), gr.update(value=new_chunk)

                    def refresh_local_models():
                        models = get_local_models()
                        val = models[0] if models else i18n("No models found")
                        return gr.update(choices=models, value=val)

                    refresh_models_btn.click(refresh_local_models, outputs=ai_model_input)
                    # v7.5: persist all three keys and the rotation policy
                    ai_settings_inputs = [ai_backend_input, api_key_input, api_key_2_input, api_key_3_input, gemini_key_mode_input, ai_model_input, chunk_size_input]
                    ai_backend_input.change(on_backend_change, inputs=ai_settings_inputs, outputs=[api_key_input, api_key_2_input, api_key_3_input, gemini_key_mode_input, ai_model_input, refresh_models_btn, chunk_size_input, settings_status])
                    for component in [api_key_input, api_key_2_input, api_key_3_input, gemini_key_mode_input, ai_model_input, chunk_size_input]:
                        component.change(on_settings_changed, inputs=ai_settings_inputs, outputs=settings_status)
                    save_settings_btn.click(save_settings_click, inputs=ai_settings_inputs, outputs=settings_status)
                    test_key_btn.click(test_api_connection, inputs=ai_settings_inputs[:5] + [ai_model_input], outputs=settings_status)
                    # Prefill the saved keys/model/backend on startup
                    demo.load(load_saved_settings, outputs=[ai_backend_input, api_key_input, api_key_2_input, api_key_3_input, gemini_key_mode_input, ai_model_input, refresh_models_btn, chunk_size_input, settings_status])
                    with gr.Row():
                        model_input = gr.Dropdown(["tiny", "small", "medium", "large", "large-v1", "large-v2", "large-v3", "turbo", "large-v3-turbo", "distil-large-v2", "distil-medium.en", "distil-small.en", "distil-large-v3"], label=i18n("Whisper Model"), value="large-v3-turbo", scale=2)
                        transcription_device_input = gr.Dropdown(
                            choices=[(i18n("Auto (recommended)"), "auto"), (i18n("CPU"), "cpu"), (i18n("NVIDIA GPU / CUDA"), "cuda")],
                            label=i18n("Transcription device"), value="auto", scale=1)
                        hardware_refresh_btn = gr.Button("🔄", size="sm", scale=0, min_width=50)
                    hardware_status = gr.Markdown("جاري فحص Torch وWhisperX...", elem_id="transcription_runtime_status")
                    hardware_refresh_btn.click(transcription_runtime_status, outputs=hardware_status)
                    demo.load(transcription_runtime_status, outputs=hardware_status)
                    with gr.Row():
                        workflow_input = gr.Dropdown(choices=[(i18n("Full"), "Full"), (i18n("Cut Only"), "Cut Only"), (i18n("Subtitles Only"), "Subtitles Only")], label=i18n("Workflow"), value="Full")
                        face_model_input = gr.Dropdown(["insightface", "mediapipe"], label=i18n("Face Model"), value="insightface")
                    gr.Markdown("### 🛡️ " + i18n("Safety"))
                    gr.Markdown("**قاعدة النشر الآمن:** الحظر يوقف المقطع قبل القص والرفع، وcensor يكتم الكلمات الحساسة، أما flag فيُبقي المقطع للمراجعة اليدوية.", elem_classes=["vc-help-card"])
                    safety_mode_input = gr.Dropdown(
                        choices=[(i18n("Block violating segments (recommended)"), "block"), (i18n("Bleep violating words (keep clip)"), "censor"), (i18n("Flag only (keep segments)"), "flag"), (i18n("Off"), "off")],
                        label=i18n("🛡️ Safety filter (hate speech)"),
                        value="block",
                        info=i18n("Blocks clips containing hate speech / incitement to violence before cutting — protects your channel from YouTube strikes."),
                    )
                    safety_ai_input = gr.Checkbox(
                        label=i18n("Extra AI review (catches contextual violations)"),
                        value=True,
                        info=i18n("Sends surviving clips to the AI for a second policy check (Gemini/G4F only)."),
                    )
                    with gr.Accordion("👁️ الفحص البصري للمحتوى الحساس", open=False):
                        gr.Markdown(
                            "**المرحلة الحالية:** يستخدم نموذج ONNX محلياً لفحص المحتوى الجنسي/العري والمشاهد الرسومية. "
                            "هذا ليس كاشفاً كاملاً للعنف أو الكراهية؛ لذلك تبقى مراجعة النص والصورة النهائية ضرورية.",
                            elem_classes=["vc-help-card"],
                        )
                        with gr.Row():
                            visual_check_input = gr.Dropdown(
                                choices=[("تلقائي — افحص إذا كان النموذج موجوداً", "auto"),
                                         ("تشغيل إلزامي — أوقف العمل عند غياب النموذج", "on"),
                                         ("إيقاف الفحص البصري", "off")],
                                label="حالة الفحص البصري",
                                value="auto",
                            )
                            visual_gate_input = gr.Dropdown(
                                choices=[("تحذير — سجّل النتيجة واسمح بالاستمرار", "warn"),
                                         ("حظر — امنع النشر عند نتيجة مرتفعة", "block"),
                                         ("تجاهل النتيجة", "off")],
                                label="سياسة نتيجة الفحص",
                                value="warn",
                            )
                            visual_frames_input = gr.Slider(
                                label="عدد الإطارات المفحوصة لكل مقطع",
                                minimum=2, maximum=12, value=4, step=1,
                                info="4 مناسب كبداية؛ زد العدد للفيديوهات السريعة على حساب الزمن.",
                            )
                        with gr.Row():
                            with gr.Column():
                                visual_model_input = gr.Textbox(
                                    label="مسار نموذج ONNX المحلي (اختياري)",
                                    placeholder="models/nudenet_lite.onnx",
                                    value="",
                                )
                                visual_model_file_input = gr.File(
                                    label="اختر نموذج ONNX من الكمبيوتر",
                                    file_count="single", file_types=[".onnx"], type="filepath",
                                )
                            auto_download_visual_input = gr.Checkbox(
                                label="تنزيل النموذج عند غيابه",
                                value=False,
                                info="التنزيل اختياري ويحتاج اتصالاً؛ اتركه مغلقاً في بيئة إنتاج مغلقة.",
                            )
                    # --- v6: platform template + professional polish (Roadmap 5.2 / Sprint 3) ---
                    with gr.Accordion(i18n("✨ Pro editing & platforms (v6)"), open=False):
                        gr.Markdown("### " + i18n("🎯 Platform & publishing"))
                        with gr.Row():
                            platform_input = gr.Dropdown(
                                choices=[(i18n("(No platform template)"), ""), (i18n("YouTube Shorts (9:16, ≤60s)"), "yt_shorts"),
                                         (i18n("TikTok (9:16, ≤90s)"), "tiktok"),
                                         (i18n("Instagram Reels (9:16, ≤90s)"), "reels"),
                                         (i18n("YouTube Standard (16:9, ≤10min)"), "yt_standard")],
                                label=i18n("📱 Platform template"),
                                value="",
                            )
                            metadata_gate_input = gr.Dropdown(
                                choices=[(i18n("Warn (flag risky metadata)"), "warn"),
                                         (i18n("Block (stop run on risky metadata)"), "block"),
                                         (i18n("Off"), "off")],
                                label=i18n("Metadata gate (title/caption/hashtags)"),
                                value="warn",
                            )
                        title_language_input = gr.Dropdown(
                            choices=[(i18n("Auto (same as video language)"), "auto"),
                                     (i18n("Arabic"), "ar"),
                                     (i18n("English"), "en"),
                                     (i18n("Français"), "fr"),
                                     (i18n("Español"), "es"),
                                     (i18n("Português"), "pt"),
                                     (i18n("Deutsch"), "de"),
                                     (i18n("Türkçe"), "tr")],
                            label=i18n("🌐 Titles & captions language"),
                            value="auto",
                            info=i18n("'Auto' matches the video language; choose Arabic to force all titles/captions in Arabic."),
                        )
                        gr.Markdown("### " + i18n("🎬 Editing quality"))
                        polish_input = gr.Checkbox(
                            label=i18n("✨ Professional polish (jump cuts + punch zoom + music + watermark)"),
                            value=False,
                            info=i18n("Removes silence/fillers, adds punch-in zoom, background music with auto-duck and your logo."),
                        )
                        with gr.Row():
                            with gr.Column():
                                music_input = gr.Textbox(label=i18n("Background music file"), placeholder="music/bed.m4a", value="")
                                music_file_input = gr.File(
                                    label="اختر ملف الموسيقى من الكمبيوتر",
                                    file_count="single", file_types=["audio"], type="filepath",
                                )
                            with gr.Column():
                                broll_input = gr.Textbox(label=i18n("B-Roll video file (optional)"), placeholder="assets/broll.mp4", value="")
                                broll_file_input = gr.File(
                                    label="اختر فيديو B-Roll من الكمبيوتر",
                                    file_count="single", file_types=["video"], type="filepath",
                                )
                        with gr.Row():
                            broll_query_input = gr.Textbox(label=i18n("B-Roll Pexels query (optional)"), placeholder="city technology business", value="")
                            broll_opacity_input = gr.Slider(label=i18n("B-Roll opacity"), minimum=0.05, maximum=0.85, value=0.28, step=0.01)
                        with gr.Row():
                            with gr.Column():
                                sfx_dir_input = gr.Textbox(label=i18n("Auto SFX folder (optional)"), placeholder="assets/sfx", value="")
                                sfx_files_input = gr.File(
                                    label="اختر مؤثرات صوتية متعددة من الكمبيوتر",
                                    file_count="multiple", file_types=["audio"], type="filepath",
                                )
                            with gr.Column():
                                sfx_volume_input = gr.Slider(label=i18n("Auto SFX volume"), minimum=0.02, maximum=1.0, value=0.22, step=0.01)
                                logo_input = gr.Textbox(label=i18n("Channel logo (PNG)"), placeholder="logo.png", value="")
                                logo_file_input = gr.File(
                                    label="اختر شعار القناة PNG من الكمبيوتر",
                                    file_count="single", file_types=[".png", ".jpg", ".jpeg"], type="filepath",
                                )
                        with gr.Row():
                            watermark_position_input = gr.Dropdown(
                                choices=[("أعلى يسار", "top-left"), ("أعلى يمين", "top-right"),
                                         ("أسفل يسار", "bottom-left"), ("أسفل يمين", "bottom-right"),
                                         ("الوسط", "center")],
                                label="موضع الشعار", value="bottom-right",
                                info="اختر موضعاً لا يغطي الترجمة أو عناصر الفيديو المهمة.",
                            )
                            watermark_size_input = gr.Slider(
                                label="حجم الشعار", minimum=0.05, maximum=0.30, value=0.12, step=0.01,
                                info="نسبة عرض الشعار من عرض الفيديو (5% إلى 30%).",
                            )
                            watermark_opacity_input = gr.Slider(
                                label="شفافية الشعار", minimum=0.10, maximum=1.00, value=0.90, step=0.05,
                                info="1.00 = واضح بالكامل، 0.10 = شفاف جداً.",
                            )
                        watermark_preview = gr.HTML(
                            value=watermark_preview_html("", "bottom-right", 0.12, 0.90),
                            label="معاينة الشعار",
                        )
                        with gr.Row():
                            aspect_input = gr.Dropdown(
                                choices=[(i18n("(9:16 — Shorts/Reels/TikTok)"), "9:16"),
                                         (i18n("4:5 — Instagram feed"), "4:5"),
                                         (i18n("1:1 — Square"), "1:1"),
                                         (i18n("16:9 — Standard YouTube"), "16:9")],
                                label=i18n("📐 Output framing (aspect ratio)"),
                                value="9:16",
                                info=i18n("Reframes the final clips after subtitle burning. 4:5/1:1 center-crop, 16:9 blur-pads."),
                            )
                            reframe_mode_input = gr.Dropdown(
                                choices=[(i18n("Auto (best for the chosen aspect)"), ""),
                                         (i18n("Crop (fill + center-crop)"), "crop"),
                                         (i18n("Pad (blurred bars)"), "pad")],
                                label=i18n("Reframe mode"),
                                value="",
                            )
                        gr.Markdown("### " + i18n("🔒 YouTube login"))
                        gr.Markdown(
                            "**ملحوظة:** كوكيز المتصفح مخصصة لتنزيل فيديوهات YouTube الخاصة أو المقيدة. "
                            "أما رفع الفيديو إلى قناتك فيتم من تبويب النشر عبر OAuth وملف `client_secrets.json`.",
                            elem_classes=["vc-help-card"],
                        )
                        cookies_input = gr.Dropdown(
                            choices=[(i18n("(No cookies — public videos only)"), ""),
                                     (i18n("Chrome cookies (private/age-restricted)"), "chrome"),
                                     (i18n("Edge cookies"), "edge"),
                                     (i18n("Firefox cookies"), "firefox")],
                            label=i18n("🔒 YouTube login (cookies)"),
                            value="",
                            info=i18n("Useful for private or age-restricted videos you have access to."),
                        )
                        sponsorblock_input = gr.Dropdown(
                            choices=[(i18n("Off (keep the video as-is)"), ""),
                                     (i18n("Sponsor segments only"), "sponsor"),
                                     (i18n("Sponsor + intro + outro"), "sponsor,intro,outro"),
                                     (i18n("All SponsorBlock categories"), "all")],
                            label=i18n("🚫 SponsorBlock (skip in-video ads)"),
                            value="",
                            info=i18n("v7.19: removes sponsored segments at download time so cuts never include ad reads."),
                        )
                        live_wait_minutes_input = gr.Number(
                            label=i18n("🔴 Live stream: wait for end (minutes)"),
                            value=0,
                            minimum=0,
                            step=15,
                            info=i18n("v7.20: if the URL is a live stream / premiere (e.g. https://youtube.com/live/ID), wait up to this many minutes for it to end, then download the VOD automatically. 0 = off (download immediately)."),
                        )
                        gr.Markdown("### 🔐 ربط قناة YouTube قبل المعالجة")
                        gr.Markdown(
                            "الخطوات: اختر ملف `client_secrets.json` من جهازك، ثم اضغط «اربط القناة الآن» أو فعّل شرط التحقق. "
                            "بعد موافقة Google سيظهر اسم القناة هنا. تغيير الملف يلغي التوكن القديم ويطلب ربطاً جديداً.",
                            elem_classes=["vc-help-card"],
                        )
                        auto_upload_oauth_file_input = gr.File(
                            label="1) ملف Google OAuth — client_secrets.json",
                            file_count="single", file_types=[".json"], type="filepath",
                        )
                        auto_upload_channel_status = gr.Markdown(
                            value="⚠️ لم يتم ربط قناة YouTube بعد. اختر ملف OAuth ثم اضغط «اربط القناة الآن».",
                            elem_classes=["vc-help-card"],
                        )
                        with gr.Row():
                            connect_youtube_before_start_btn = gr.Button(
                                "🔐 2) اربط القناة الآن", variant="primary",
                            )
                            require_youtube_connection_input = gr.Checkbox(
                                label="3) تحقق من اتصال القناة قبل البدء", value=True,
                                info="يبدأ OAuth عند تفعيله، ثم يمنع المعالجة إذا انتهت صلاحية الاتصال.",
                            )
                        gr.Markdown("### 🚀 إعدادات الرفع التلقائي بعد نجاح التقطيع")
                        with gr.Row():
                            auto_upload_after_processing_input = gr.Checkbox(
                                label="رفع تلقائي بعد انتهاء المعالجة", value=False,
                                info="يبدأ بعد نجاح pipeline فقط؛ الوضع التجريبي مفعّل افتراضياً.",
                            )
                            auto_upload_dry_run_input = gr.Checkbox(
                                label="وضع تجريبي (لا ترفع فعلياً)", value=True,
                                info="ألغِ التحديد فقط عندما تريد رفعاً حقيقياً إلى YouTube.",
                            )
                        with gr.Row():
                            auto_upload_source_input = gr.Dropdown(
                                choices=[("تلقائي: final_polished ثم final ثم cuts", "auto"),
                                         ("final_polished", "final_polished"),
                                         ("final", "final"), ("cuts", "cuts"),
                                         ("ملف MP4 محدد", "specific_file")],
                                label="مصدر ملفات الرفع", value="auto",
                            )
                            auto_upload_specific_file_input = gr.File(
                                label="اختر ملف MP4 محدد للرفع عند الحاجة",
                                file_count="single", file_types=[".mp4"], type="filepath",
                            )
                        with gr.Row():
                            auto_upload_privacy_input = gr.Dropdown(
                                choices=[("خاص — Private", "private"), ("غير مدرج — Unlisted", "unlisted"),
                                         ("عام — Public", "public")],
                                label="خصوصية فيديوهات الرفع", value="private",
                            )
                            auto_upload_publish_at_input = gr.Textbox(
                                label="وقت أول مقطع (ISO 8601، اختياري)",
                                placeholder="2026-08-20T18:30:00+00:00", value="",
                                info="إذا حُدد، يزداد وقت كل مقطع تلقائياً حسب الفاصل أدناه.",
                            )
                        auto_upload_interval_minutes_input = gr.Number(
                            label="الفاصل بين المقاطع المجدولة (دقائق)",
                            value=60, minimum=1, maximum=10080, step=1,
                            info="يُضاف هذا الفاصل تلقائياً إلى وقت كل مقطع تالٍ.",
                        )
                        auto_upload_public_confirm_input = gr.Checkbox(
                            label="تأكيد النشر العام (مطلوب فقط عند Public + إلغاء Dry Run)",
                            value=False,
                            info="لن يُرفع أي ملف بوضع Public فعلياً من دون هذا التأكيد الصريح.",
                        )
                    with gr.Row():
                        safety_update_btn = gr.Button(i18n("🔄 Update safety word list"), size="sm")
                        safety_update_status = gr.Markdown(i18n("Loading…"), elem_id="safety_update_status")

                    def safety_list_status_text():
                        try:
                            from scripts.safety_updater import load_cached_pack
                            pack = load_cached_pack()
                            parts = []
                            if pack:
                                parts.append(i18n("Safety word list: v{} ({} terms)").format(
                                    pack.get("version", "?"), len(pack.get("terms", []))))
                            else:
                                parts.append(i18n("Safety word list: built-in (no updates downloaded yet)"))
                            try:
                                from scripts.youtube_policy_watch import load_feed
                                feed = load_feed()
                                checked = feed.get("checked_at") or ""
                                parts.append("YouTube Policy Watch: {}".format(
                                    checked[:10] if checked else i18n("not checked yet")))
                            except Exception:
                                pass
                            return "\n".join(parts)
                        except Exception:
                            return i18n("Safety word list: built-in")

                    def policy_watch_summary():
                        try:
                            from scripts.youtube_policy_watch import check_policy_pages
                            result = check_policy_pages()
                            if result.get("status") == "changed":
                                return "🔔 " + i18n("YouTube policy pages changed: {} — update the word list soon.").format(
                                    ", ".join(result.get("changes", [])))
                            if result.get("status") == "offline":
                                return i18n("(YouTube policy pages unreachable — offline)")
                            return "✅ " + i18n("YouTube policies unchanged.")
                        except Exception:
                            return ""

                    def run_safety_update():
                        try:
                            from scripts.safety_updater import check_and_update
                            result = check_and_update(force=True)
                            status = result.get("status")
                            if status == "updated":
                                msg = i18n("Safety list updated: {}\n{}").format(
                                    result.get("message", ""), policy_watch_summary())
                            elif status == "up-to-date":
                                msg = i18n("Safety list is current (v{})\n{}").format(
                                    result.get("version", "?"), policy_watch_summary())
                            elif status == "offline":
                                msg = i18n("Safety list update failed (offline). Using local list.\n{}").format(
                                    policy_watch_summary())
                            else:
                                msg = i18n("Safety list update failed. Using local list.\n{}").format(
                                    policy_watch_summary())
                            return msg + "\n\n" + safety_list_status_text()
                        except Exception as e:
                            return i18n("Safety list update failed: {}\n{}").format(
                                e, policy_watch_summary())

                    demo.load(lambda: safety_list_status_text(), outputs=safety_update_status)
                    safety_update_btn.click(run_safety_update, outputs=safety_update_status)
                    with gr.Row():
                        face_mode_input = gr.Dropdown(
                            choices=[
                                (i18n("Auto"), "auto"),
                                ("1 — Single speaker", "1"),
                                ("2 — Two speakers", "2"),
                                ("3 — Three speakers", "3"),
                                ("4 — Four speakers", "4"),
                                ("Multi — up to 4", "multi"),
                                ("Grid — up to 4", "grid"),
                            ],
                            label=i18n("Face Mode"),
                            value="auto",
                            info=i18n("Multi/Grid keeps up to four visible speakers in a stable portrait layout."),
                        )
                        face_detect_interval_input = gr.Textbox(label=i18n("Face Detect Interval"), value="0.17,1.0")
                        no_face_mode_input = gr.Dropdown(choices=[(i18n("Padding (9:16)"), "padding"), (i18n("Zoom (Center)"), "zoom")], label=i18n("No Face Fallback"), value="zoom")
                    input_source.change(on_source_change, inputs=input_source, outputs=[url_input, project_selector, video_upload, workflow_input, upload_hint])

            # File pickers populate the existing string-path fields used by the CLI.
            music_file_input.change(
                lambda value: file_inputs.first_path(value),
                inputs=music_file_input, outputs=music_input)
            broll_file_input.change(
                lambda value: file_inputs.first_path(value),
                inputs=broll_file_input, outputs=broll_input)
            logo_file_input.change(
                lambda value: file_inputs.first_path(value),
                inputs=logo_file_input, outputs=logo_input)
            def _logo_preview(path, position, size, opacity):
                return watermark_preview_html(path, position, size, opacity)
            for _logo_component in (logo_input, logo_file_input, watermark_position_input,
                                    watermark_size_input, watermark_opacity_input):
                _logo_component.change(
                    _logo_preview,
                    inputs=[logo_input, watermark_position_input, watermark_size_input, watermark_opacity_input],
                    outputs=watermark_preview)
            sfx_files_input.change(
                lambda value: file_inputs.common_parent(value),
                inputs=sfx_files_input, outputs=sfx_dir_input)
            visual_model_file_input.change(
                lambda value: file_inputs.first_path(value),
                inputs=visual_model_file_input, outputs=visual_model_input)

            with gr.Accordion(i18n("Advanced Face Settings"), open=False):
                gr.Markdown("**التتبع المتقدم:** فعّل المتحدث النشط عند وجود أكثر من شخص؛ سيستخدم حركة الفم وطاقة الصوت مع تثبيت زمني لتقليل تبدّل الإطار.", elem_classes=["vc-help-card"])
                face_preset_input = gr.Dropdown(choices=[(i18n(k), k) for k in FACE_PRESETS.keys()], label=i18n("Configuration Presets"), value="Default (Balanced)", interactive=True)
                with gr.Row():
                    face_filter_thresh_input = gr.Slider(label=i18n("Ignore Small Faces (0.0 - 1.0)"), minimum=0.0, maximum=1.0, value=0.35, step=0.05, info=i18n("Relative size to ignore background."))
                    face_two_thresh_input = gr.Slider(label=i18n("Threshold for 2 Faces (0.0 - 1.0)"), minimum=0.0, maximum=1.0, value=0.60, step=0.05, info=i18n("Size of 2nd face to activate split mode."))
                    face_conf_thresh_input = gr.Slider(label=i18n("Minimum Confidence (0.0 - 1.0)"), minimum=0.0, maximum=1.0, value=0.40, step=0.05, info=i18n("Ignore detections with low confidence."))
                    face_dead_zone_input = gr.Slider(label=i18n("Dead Zone (Stabilization)"), minimum=0, maximum=200, value=150, step=5, info=i18n("Movement pixels to ignore."))
                with gr.Row():
                    face_smoothing_input = gr.Slider(label=i18n("Smoothness (Camera Jitter)"), minimum=0.05, maximum=1.0, value=0.55, step=0.05, info=i18n("v7.18: EMA smoothing of the crop box. Lower = steadier camera, higher = more responsive."))
                    face_headroom_input = gr.Slider(label=i18n("Headroom (Face Position)"), minimum=0.0, maximum=0.35, value=0.12, step=0.02, info=i18n("v7.18: shift the crop up so the face sits in the upper third (talking-head framing)."))
                face_preset_input.change(apply_face_preset, inputs=face_preset_input, outputs=[face_filter_thresh_input, face_two_thresh_input, face_conf_thresh_input, face_dead_zone_input])
                with gr.Accordion(i18n("Experimental: Active Speaker & Motion"), open=False):
                    experimental_preset_input = gr.Dropdown(choices=[(i18n(k), k) for k in EXPERIMENTAL_PRESETS.keys()], label=i18n("Configuration Presets"), value="Active Speaker (Balanced)", interactive=True)
                    focus_active_speaker_input = gr.Checkbox(label=i18n("Focus on Active Speaker (automatic)"), value=True, info="يحتاج InsightFace؛ عند تعذر InsightFace سيعمل تثبيت الوجه فقط ويُسجل ذلك في tracking_report.json.")
                    with gr.Row():
                        active_speaker_mar_input = gr.Slider(label=i18n("MAR Threshold (Mouth Open)"), minimum=0.01, maximum=0.20, value=0.03, step=0.005, info=i18n("Mouth open sensitivity."))
                        active_speaker_score_diff_input = gr.Slider(label=i18n("Score Difference"), minimum=0.5, maximum=10.0, value=1.5, step=0.5, info=i18n("Minimum difference to focus on 1 face."))
                    with gr.Row():
                        include_motion_input = gr.Checkbox(label=i18n("Consider Motion"), value=True, info=i18n("Increases score with motion (gestures)."))
                    with gr.Row():
                        active_speaker_motion_threshold_input = gr.Slider(label=i18n("Motion Dead Zone"), minimum=0.0, maximum=20.0, value=3.0, step=0.5, info=i18n("Pixels ignored."))
                        active_speaker_motion_sensitivity_input = gr.Slider(label=i18n("Motion Sensitivity"), minimum=0.01, maximum=0.5, value=0.05, step=0.01, info=i18n("Points per pixel."))
                        active_speaker_decay_input = gr.Slider(label=i18n("Switch Speed"), minimum=0.5, maximum=5.0, value=2.0, step=0.5, info=i18n("Speed to lose focus."))
                    experimental_preset_input.change(apply_experimental_preset, inputs=experimental_preset_input, outputs=[focus_active_speaker_input, active_speaker_mar_input, active_speaker_score_diff_input, include_motion_input, active_speaker_motion_threshold_input, active_speaker_motion_sensitivity_input, active_speaker_decay_input])
            with gr.Accordion(i18n("Subtitle Settings (Beta)"), open=False):
                preset_input = gr.Dropdown(choices=[(i18n("Manual"), "Manual")] + [(i18n(k), k) for k in subs.SUBTITLE_PRESETS.keys()], label=i18n("Presets"), value="Hormozi (Classic)")
                use_custom_subs = gr.Checkbox(label=i18n("Enable Subtitle Customization (incl. preset)"), value=True)
                preview_html = gr.HTML(value=f"<div style='text-align:center; padding:10px; color:#666;'>{i18n('Select options or preset to preview')}</div>")
                with gr.Row():
                    preview_vid_btn = gr.Button(i18n("🎬 Render Animated Preview (Slow)"), size="sm")
                preview_vid = gr.Video(label=i18n("Animated Preview"), height=300, autoplay=True, interactive=False)
                with gr.Accordion(i18n("Advanced Settings"), open=False):
                    gr.Markdown("### " + tr("Appearance"))
                    with gr.Row():
                        font_name_input = gr.Textbox(label=i18n("Font Name"), value="Montserrat-Regular")
                        font_size_input = gr.Slider(label=i18n("Font Size (Base)"), minimum=8, maximum=80, value=12)
                        highlight_size_input = gr.Slider(label=i18n("Highlight Size"), minimum=8, maximum=80, value=14)
                    with gr.Row():
                        font_color_input = gr.ColorPicker(label=i18n("Base Color"), value="#FFFFFF")
                        highlight_color_input = gr.ColorPicker(label=i18n("Highlight Color"), value="#00FF00")
                        outline_color_input = gr.ColorPicker(label=i18n("Outline Color"), value="#000000")
                        shadow_color_input = gr.ColorPicker(label=i18n("Shadow Color"), value="#000000")
                    gr.Markdown("### " + tr("Styling & Effects"))
                    with gr.Row():
                        outline_thickness_input = gr.Slider(label=i18n("Outline Thickness"), minimum=0, maximum=10, value=1.5)
                        shadow_size_input = gr.Slider(label=i18n("Shadow Size"), minimum=0, maximum=10, value=2)
                        border_style_input = gr.Dropdown(choices=[(i18n("Outline"), 1), (i18n("Opaque Box"), 3)], label=i18n("Border Style"), value=1)
                    with gr.Row():
                        bold_input = gr.Checkbox(label=i18n("Bold"))
                        italic_input = gr.Checkbox(label=i18n("Italic"))
                        uppercase_input = gr.Checkbox(label=i18n("Uppercase"))
                        remove_punc_input = gr.Checkbox(label=i18n("Remove Punctuation"), value=True)
                        underline_input = gr.Checkbox(label=i18n("Underline"))
                        strikeout_input = gr.Checkbox(label=i18n("Strikeout"))
                    gr.Markdown("### " + tr("Positioning & Layout"))
                    with gr.Row():
                        vertical_pos_input = gr.Slider(label=i18n("V-Pos (Margin V)"), minimum=0, maximum=500, value=210)
                        alignment_input = gr.Dropdown(choices=[(i18n("Left"), 1), (i18n("Center"), 2), (i18n("Right"), 3)], label=i18n("Alignment"), value=2)
                        gap_limit_input = gr.Slider(label=i18n("Gap Limit"), minimum=0.0, maximum=5.0, value=0.5, step=0.1)
                        mode_input = gr.Dropdown(choices=[(i18n("Highlight"), "highlight"), (i18n("Word by Word"), "word_by_word"), (i18n("No Highlight"), "no_highlight")], label=i18n("Mode"), value="highlight")
                        words_per_block_input = gr.Slider(label=i18n("Words per Block"), minimum=1, maximum=20, value=3, step=1)
                        caption_animation_input = gr.Dropdown(
                            choices=[(i18n("None"), "none"), (i18n("Pop"), "pop"), (i18n("Scale"), "scale"), (i18n("Pop + Scale"), "pop_scale"), (i18n("Bounce"), "bounce")],
                            label=i18n("Kinetic Caption Animation"), value="none",
                            info=i18n("Applies a word-timed ASS animation to burned captions."),
                        )
                        auto_emoji_input = gr.Checkbox(
                            label=i18n("Auto-Emoji for keywords"), value=False,
                            info=i18n("Adds conservative emojis to supported emotional keywords."),
                        )

                manual_inputs = [
                    font_name_input, font_size_input, font_color_input, highlight_color_input,
                    outline_color_input, outline_thickness_input, shadow_color_input, shadow_size_input,
                    bold_input, italic_input, uppercase_input,
                    highlight_size_input, words_per_block_input, gap_limit_input, mode_input,
                    underline_input, strikeout_input, border_style_input,
                    vertical_pos_input, alignment_input,
                    remove_punc_input
                ]
                preset_input.change(subs.apply_preset, inputs=[preset_input], outputs=manual_inputs)
                for inp in manual_inputs:
                    inp.change(subs.generate_preview_html, inputs=manual_inputs, outputs=preview_html)
                preview_vid_btn.click(subs.render_preview_video, inputs=manual_inputs + [caption_animation_input, auto_emoji_input], outputs=preview_vid)
                demo.load(subs.generate_preview_html, inputs=manual_inputs, outputs=preview_html)
                demo.load(subs.apply_preset, inputs=[preset_input], outputs=manual_inputs)

                with gr.Accordion(i18n("Saved Settings Templates"), open=False):
                    with gr.Row():
                        template_name_input = gr.Textbox(label=i18n("Template Name"), placeholder=i18n("e.g. clean-shorts"))
                        save_template_btn = gr.Button(tr("Save Template"), variant="primary")
                    with gr.Row():
                        template_dropdown = gr.Dropdown(choices=template_choices(), label=i18n("Load Template"), value=None)
                        load_template_btn = gr.Button(tr("Apply Template"), variant="secondary")
                    template_status = gr.Textbox(label=i18n("Template Status"), interactive=False)

                def save_settings_template(name, use_custom, font_name, font_size, font_color, highlight_color, outline_color, outline_thickness, shadow_color, shadow_size, is_bold, is_italic, is_uppercase, vertical_pos, alignment, h_size, w_block, gap, mode, under, strike, border_s, remove_punc, face_mode, face_model, no_face_mode, face_detect_interval):
                    """Save current subtitle + face settings as a named template."""
                    name = (name or "").strip()
                    if not name:
                        return i18n("Template name is required."), gr.update(choices=template_choices())
                    payload = {
                        "subtitle": {
                            "use_custom": bool(use_custom),
                            "font_name": font_name,
                            "font_size": int(font_size),
                            "font_color": font_color,
                            "highlight_color": highlight_color,
                            "outline_color": outline_color,
                            "outline_thickness": outline_thickness,
                            "shadow_color": shadow_color,
                            "shadow_size": shadow_size,
                            "is_bold": bool(is_bold),
                            "is_italic": bool(is_italic),
                            "is_uppercase": bool(is_uppercase),
                            "vertical_pos": int(vertical_pos),
                            "alignment": alignment,
                            "highlight_size": int(h_size),
                            "words_per_block": int(w_block),
                            "gap": gap,
                            "mode": mode,
                            "under": bool(under),
                            "strike": bool(strike),
                            "border_s": border_s,
                            "remove_punc": bool(remove_punc),
                        },
                        "face": {
                            "face_mode": face_mode,
                            "face_model": face_model,
                            "no_face_mode": no_face_mode,
                            "face_detect_interval": face_detect_interval,
                        },
                    }
                    err = save_template(name, payload)
                    if err:
                        return i18n("Error saving template: {}").format(err), gr.update(choices=template_choices())
                    return i18n("Template saved: {}").format(name), gr.update(choices=template_choices(), value=name)

                def load_settings_template(name):
                    """Apply a saved template to subtitle + face settings."""
                    templates = load_templates()
                    payload = templates.get(name)
                    if not payload:
                        return [gr.update() for _ in range(26)] + [i18n("Template not found.")]
                    sub = payload.get("subtitle", payload)  # tolerate legacy flat format
                    face = payload.get("face", {})
                    return [
                        gr.update(value=sub.get("use_custom", True)),
                        gr.update(value=sub.get("font_name", "Montserrat-Regular")),
                        gr.update(value=sub.get("font_size", 12)),
                        gr.update(value=sub.get("font_color", "#FFFFFF")),
                        gr.update(value=sub.get("highlight_color", "#00FF00")),
                        gr.update(value=sub.get("outline_color", "#000000")),
                        gr.update(value=sub.get("outline_thickness", 1.5)),
                        gr.update(value=sub.get("shadow_color", "#000000")),
                        gr.update(value=sub.get("shadow_size", 2)),
                        gr.update(value=sub.get("is_bold", False)),
                        gr.update(value=sub.get("is_italic", False)),
                        gr.update(value=sub.get("is_uppercase", False)),
                        gr.update(value=sub.get("vertical_pos", 210)),
                        gr.update(value=sub.get("alignment", 2)),
                        gr.update(value=sub.get("highlight_size", 14)),
                        gr.update(value=sub.get("words_per_block", 3)),
                        gr.update(value=sub.get("gap", 0.5)),
                        gr.update(value=sub.get("mode", "highlight")),
                        gr.update(value=sub.get("under", False)),
                        gr.update(value=sub.get("strike", False)),
                        gr.update(value=sub.get("border_s", 1)),
                        gr.update(value=sub.get("remove_punc", True)),
                        gr.update(value=face.get("face_mode", "auto")),
                        gr.update(value=face.get("face_model", "insightface")),
                        gr.update(value=face.get("no_face_mode", "zoom")),
                        gr.update(value=face.get("face_detect_interval", "0.17,1.0")),
                        i18n("Template loaded: {}").format(name),
                    ]

                save_template_btn.click(save_settings_template, inputs=[template_name_input, use_custom_subs] + manual_inputs + [face_mode_input, face_model_input, no_face_mode_input, face_detect_interval_input], outputs=[template_status, template_dropdown])
                load_template_btn.click(load_settings_template, inputs=template_dropdown, outputs=[use_custom_subs] + manual_inputs + [face_mode_input, face_model_input, no_face_mode_input, face_detect_interval_input, template_status])

                results_html = gr.HTML(label=tr("Results"))

            with gr.Row(elem_id="vc-bottom-actions", elem_classes=["vc-bottom-actions"]):
                start_btn = gr.Button("🚀 بدء المعالجة", variant="primary", size="lg", scale=3)
                stop_btn = gr.Button("⏹️ إيقاف المعالجة", variant="stop", visible=True, interactive=False, size="lg", scale=2)


        with gr.Tab("👀 " + i18n("Review Segments")):
            gr.Markdown(f"### {i18n('Review Segments')}")
            gr.Markdown(i18n("Review the AI-suggested segments, uncheck what you don't want, then render only the selected ones."))
            gr.Markdown(
                "**طريقة المراجعة الاحترافية:** درجة التقييم الأصلية تقيس قابلية الانتشار، أما درجة الاختيار فتجمع معها قوة الخطاف واكتمال السرد والوضوح والجِدّة. "
                "راجع بدائل A/B، اختر العنوان الذي يطابق محتوى المقطع فعلاً، ثم ألغِ تحديد المقاطع المتشابهة قبل المعالجة.",
                elem_classes=["vc-help-card"],
            )
            with gr.Row():
                review_project_dropdown = gr.Dropdown(choices=library.get_existing_projects(), label=tr("Select Project"), value=None)
                review_refresh_btn = gr.Button(tr("Refresh"), size="sm")
                review_load_btn = gr.Button(i18n("Load Segments"), variant="primary")

            review_df = gr.Dataframe(
                headers=segments_review.HEADERS,
                datatype=["bool", "str", "number", "str", "str", "str", "str", "str", "str", "str", "str", "number", "str"],
                interactive=True,
                label=i18n("Segments"),
                elem_id="review_segments_df",
            )

            with gr.Row():
                review_apply_btn = gr.Button(i18n("Apply Selection"))
                review_restore_btn = gr.Button(i18n("Restore All"))
                review_render_btn = gr.Button(i18n("Render Selected Segments"), variant="primary")
            review_status = gr.Markdown()
            with gr.Row():
                review_export_btn = gr.Button(i18n("Export Publish Metadata"))
            review_export_out = gr.Textbox(label=i18n("Publish Metadata"), lines=8, interactive=False)
            with gr.Row():
                review_risk_btn = gr.Button("🛡️ " + i18n("Risk Scorecard"), variant="secondary")
                review_risk_html_btn = gr.Button(i18n("Save HTML report to the project"), size="sm")
            review_risk_out = gr.HTML(label=i18n("Risk Scorecard"))

            def load_review_segments(project_name):
                if not project_name:
                    return None, i18n("Error: No project selected.")
                project_path = _project_path_for_name(project_name)
                segments = segments_review.load_segments(project_path)
                if not segments:
                    return None, i18n("No viral segments found in this project.")
                return segments_review.rows_from_segments(segments, segments_review.load_safety_map(project_path)), f"**{len(segments)}** ✔"

            def apply_review_selection(project_name, df):
                if not project_name:
                    return i18n("Error: No project selected.")
                project_path = _project_path_for_name(project_name)
                kept, total, cuts_cleared = segments_review.apply_selection(project_path, df)
                msg = i18n("Applied: {} of {} segments selected.").format(kept, total)
                if cuts_cleared:
                    msg += " " + i18n("Stale cuts cleared — they will be re-cut on render.")
                return msg

            def restore_review_segments(project_name):
                if not project_name:
                    return None, i18n("Error: No project selected.")
                project_path = _project_path_for_name(project_name)
                if not segments_review.restore_all(project_path):
                    return None, i18n("No backup found for this project.")
                segments = segments_review.load_segments(project_path)
                return segments_review.rows_from_segments(segments, segments_review.load_safety_map(project_path)), i18n("Selection restored from backup.")

            def run_review_render(project_name, df, *rest):
                if project_name and df is not None:
                    project_path = _project_path_for_name(project_name)
                    try:
                        segments_review.apply_selection(project_path, df)
                    except Exception:
                        pass
                yield from run_viral_cutter("Existing Project", project_name, *rest)

            def export_review_metadata(project_name):
                if not project_name:
                    return i18n("Error: No project selected.")
                project_path = _project_path_for_name(project_name)
                path, text = segments_review.export_publish_metadata(project_path)
                if not path:
                    return i18n("No viral segments found in this project.")
                return text

            review_refresh_btn.click(library.refresh_projects, outputs=review_project_dropdown)
            review_load_btn.click(load_review_segments, inputs=review_project_dropdown, outputs=[review_df, review_status])
            def load_risk_scorecard(project_name, save_html=False):
                if not project_name:
                    return '<div style="color:#f87171;">❌ ' + i18n("Error: No project selected.") + '</div>'
                project_path = _project_path_for_name(project_name)
                try:
                    from scripts import risk_scorecard
                    path = os.path.join(project_path, risk_scorecard.SCORECARD_FILENAME)
                    if not os.path.exists(path):
                        return '<div style="color:#fbbf24;">' + i18n("No risk scorecard yet — run the pipeline first.") + '</div>'
                    with open(path, "r", encoding="utf-8") as fh:
                        report = json.load(fh)
                    html = risk_scorecard.build_scorecard_html(report)
                    if save_html:
                        risk_scorecard.render_html_report(project_path)
                        html += '<div style="margin-top:8px;color:#4ade80;">✅ ' + i18n("Saved: {filename}").format(filename="risk_report.html") + '</div>'
                    return html
                except Exception as e:
                    return '<div style="color:#f87171;">❌ {}</div>'.format(e)

            review_risk_btn.click(lambda p: load_risk_scorecard(p, False), inputs=review_project_dropdown, outputs=review_risk_out)
            review_risk_html_btn.click(lambda p: load_risk_scorecard(p, True), inputs=review_project_dropdown, outputs=review_risk_out)

            review_apply_btn.click(apply_review_selection, inputs=[review_project_dropdown, review_df], outputs=review_status)
            review_restore_btn.click(restore_review_segments, inputs=review_project_dropdown, outputs=[review_df, review_status])
            review_export_btn.click(export_review_metadata, inputs=review_project_dropdown, outputs=review_export_out)

        with gr.Tab("✍️ " + i18n("Subtitle Editor")):
            gr.Markdown("### " + i18n("Edit Subtitles (Smart Mode)"))
            with gr.Row():
                editor_project_dropdown = gr.Dropdown(choices=library.get_existing_projects(), label=i18n("Choose a Project"), value=None, scale=4)
                editor_refresh_btn = gr.Button(tr("Refresh"), size="sm", scale=1)
            editor_file_dropdown = gr.Dropdown(choices=[], label=i18n("Subtitle File (from subs folder)"), value=None)
            with gr.Group():
                editor_status = gr.Textbox(label=i18n("Status"), interactive=False)
            with gr.Row():
                editor_render_single_btn = gr.Button(i18n("🎬 Render Selected (single clip)"), size="sm")
                editor_render_all_btn = gr.Button(i18n("🎬 Render All (background)"), size="sm")
                editor_export_all_btn = gr.Button(i18n("📤 Export All Segments"), size="sm")
            editor_refresh_btn.click(library.refresh_projects, outputs=editor_project_dropdown)


            def update_file_list(proj_name):
                if not proj_name:
                    return gr.update(choices=[], value=None)
                proj_path = _project_path_for_name(proj_name)
                files = editor.list_editable_files(proj_path)
                return gr.update(choices=files, value=files[0] if files else None)

            # v6.8 fix: the file list used to be written into the status Textbox
            # (a Dropdown update into a Textbox) and current_json_path was never
            # set — "Render Selected" could never work. Now a real dropdown.
            editor_project_dropdown.change(update_file_list, inputs=editor_project_dropdown, outputs=editor_file_dropdown)

            def render_single(proj_name, json_file, use_custom, font_name, font_size, font_color, highlight_color, 
                              outline_color, outline_thickness, shadow_color, shadow_size, 
                              is_bold, is_italic, is_uppercase, 
                              h_size, w_block, gap, mode, under, strike, border_s, 
                              vertical_pos, alignment, remove_punc, caption_animation="none", auto_emoji=False):
                if not proj_name:
                    return i18n("No project selected.")
                if not json_file:
                    return i18n("No file loaded.")
                project_dir = _project_path_for_name(proj_name)
                if not project_dir:
                    return i18n("Error: Project path not found.")
                json_path = os.path.join(project_dir, "subs", os.path.basename(str(json_file)))
                subtitle_config_path = os.path.join(WORKING_DIR, "temp_subtitle_config.json")
                if use_custom:
                    subtitle_config = _build_subtitle_config(font_name, font_size, font_color, highlight_color, outline_color, outline_thickness, shadow_color, shadow_size, is_bold, is_italic, is_uppercase, vertical_pos, alignment, h_size, w_block, gap, mode, under, strike, border_s, remove_punc, caption_animation, auto_emoji)
                    with open(subtitle_config_path, "w", encoding="utf-8") as f:
                        json.dump(subtitle_config, f, indent=4)
                else:
                    try:
                        if os.path.exists(subtitle_config_path):
                            os.remove(subtitle_config_path)
                    except Exception:
                        pass
                return editor.render_specific_video(json_path)

            editor_render_single_btn.click(render_single, inputs=[editor_project_dropdown, editor_file_dropdown, use_custom_subs] + manual_inputs + [caption_animation_input, auto_emoji_input], outputs=editor_status)

            def render_all(proj_name, use_custom, font_name, font_size, font_color, highlight_color, 
                           outline_color, outline_thickness, shadow_color, shadow_size, 
                           is_bold, is_italic, is_uppercase, 
                           h_size, w_block, gap, mode, under, strike, border_s, 
                           vertical_pos, alignment, remove_punc, caption_animation="none", auto_emoji=False):
                if not proj_name:
                    return i18n("No project selected.")
                if use_custom:
                    subtitle_config = _build_subtitle_config(font_name, font_size, font_color, highlight_color, outline_color, outline_thickness, shadow_color, shadow_size, is_bold, is_italic, is_uppercase, vertical_pos, alignment, h_size, w_block, gap, mode, under, strike, border_s, remove_punc, caption_animation, auto_emoji)
                    subtitle_config_path = os.path.join(WORKING_DIR, "temp_subtitle_config.json")
                    with open(subtitle_config_path, "w", encoding="utf-8") as f:
                        json.dump(subtitle_config, f, indent=4)
                proj_path = _project_path_for_name(proj_name)
                cmd = runtime.python_cmd(MAIN_SCRIPT_PATH) + ["--project-path", proj_path, "--workflow", "3", "--skip-prompts"]
                if use_custom and os.path.exists(os.path.join(WORKING_DIR, "temp_subtitle_config.json")):
                    cmd.extend(["--subtitle-config", os.path.join(WORKING_DIR, "temp_subtitle_config.json")])
                try:
                    subprocess.Popen(cmd, cwd=WORKING_DIR)
                    return i18n("Render All started in background... Check terminal/logs.")
                except Exception as e:
                    return i18n("Error starting render: {}").format(e)

            editor_render_all_btn.click(render_all, inputs=[editor_project_dropdown, use_custom_subs] + manual_inputs + [caption_animation_input, auto_emoji_input], outputs=editor_status)

            def export_all(project_name):
                if not project_name:
                    return i18n("No project selected.")
                proj_path = _project_path_for_name(project_name)
                return editor.export_all_segments(proj_path)

            editor_export_all_btn.click(export_all, inputs=[editor_project_dropdown], outputs=editor_status)


        with gr.Tab("🚀 " + i18n("Publish & Upload")):
            gr.Markdown(f"### {i18n('Publish & Upload')}")
            gr.Markdown(i18n("Play, translate, check music, then upload each clip through the safety gate."))
            with gr.Row():
                pub_project = gr.Dropdown(choices=library.get_existing_projects(), label=tr("Select Project"), value=None)
                pub_refresh = gr.Button(tr("Refresh"), size="sm")
            with gr.Row():
                pub_source = gr.Dropdown(
                    choices=[("تلقائي: final_polished ثم final ثم cuts", "auto"),
                             ("final_polished", "final_polished"), ("final", "final"),
                             ("cuts", "cuts"), ("ملف محدد من الكمبيوتر", "specific_file")],
                    value="auto", label="مصدر ملفات الرفع",
                    info="يحدد الملفات التي تظهر في قائمة المقاطع وتستخدم للرفع اليدوي.",
                )
                pub_specific_file = gr.File(
                    label="ملف MP4 محدد (عند اختيار ملف محدد)",
                    file_count="single", file_types=[".mp4"], type="filepath",
                )
            with gr.Row():
                with gr.Column(scale=1, min_width=280):
                    pub_clip = gr.Dropdown(choices=[], label=i18n("Select Clip"), value=None)
                    pub_preview = gr.Video(label=i18n("Clip Preview"), interactive=False)
                    pub_sub_preview = gr.Textbox(label="📝", lines=3, interactive=False)
                with gr.Column(scale=1, min_width=320):
                    pub_title = gr.Textbox(label=i18n("Title"), value="")
                    pub_caption = gr.Textbox(label=i18n("Caption / YouTube description"), lines=3, value="")
                    pub_hashtags = gr.Textbox(label=i18n("Hashtags (comma separated)"), value="")
                    gr.Markdown("#### 🔐 تسجيل الدخول إلى YouTube عبر OAuth")
                    gr.Markdown(
                        "اختر ملف `client_secrets.json` من الكمبيوتر. يتم حفظ نسخة خاصة محلياً داخل مجلد إعدادات OUSSAMA Cutter، "
                        "ولا تُعرض قيمة `client_secret` في السجل. عند استبدال الملف يُبطل التوكن القديم ويطلب النظام تسجيل دخول جديداً.",
                        elem_classes=["vc-help-card"],
                    )
                    pub_youtube_oauth = gr.File(
                        label="ملف Google OAuth `client_secrets.json`",
                        file_count="single", file_types=[".json"], type="filepath")
                    with gr.Row():
                        pub_platform = gr.Radio(["youtube", "tiktok", "instagram"], label=i18n("Platform"), value="youtube")
                        pub_music_gate = gr.Radio(["warn", "block", "off"], label=i18n("Music gate"), value="warn")
                    with gr.Row():
                        pub_privacy = gr.Dropdown(
                            ["private", "unlisted", "public"], label=i18n("YouTube privacy"), value="private")
                        pub_publish_at = gr.Textbox(
                            label=i18n("Schedule time (ISO 8601, optional)"),
                            placeholder="2026-08-15T18:30:00+01:00", value="")
                        pub_full_oauth = gr.Checkbox(
                            label=i18n("Request full YouTube OAuth access (advanced)"), value=False)
                        pub_public_confirm = gr.Checkbox(
                            label="تأكيد الرفع العام الحقيقي (مطلوب عند Public)",
                            value=False,
                            info="اتركه مغلقاً للخاص أو غير المدرج؛ Dry Run لا يحتاج تأكيداً.",
                        )

                    with gr.Row():
                        pub_check_oauth_btn = gr.Button("✅ حفظ/التحقق من الملف", variant="secondary")
                        pub_replace_oauth_btn = gr.Button("🔁 استبدال client_secrets", variant="secondary")
                        pub_login_btn = gr.Button("🔐 تسجيل الدخول إلى YouTube", variant="primary")
                    with gr.Row():
                        pub_dry = gr.Checkbox(label=i18n("Dry run (no real upload)"), value=True)
                        pub_upload_btn = gr.Button(i18n("Upload / Schedule"), variant="primary")
                    pub_audit_btn = gr.Button("🔎 تدقيق جاهزية الرفع قبل البدء", variant="secondary")
                    pub_readiness_out = gr.Textbox(
                        label="تقرير الجاهزية قبل الرفع",
                        lines=12,
                        interactive=False,
                        info="يفحص الملفات والتقارير وOAuth وقاطع القناة؛ لا يبدأ رفعاً ولا تسجيل دخول تفاعلياً.",
                    )
                    pub_oauth_status = gr.Textbox(label="حالة OAuth في YouTube", lines=4, interactive=False)
                    pub_log = gr.Textbox(label=i18n("Upload Log"), lines=10, interactive=False)
            with gr.Row():
                with gr.Column(scale=1, min_width=280):
                    with gr.Row():
                        pub_lang = gr.Textbox(label=i18n("Target language"), value="en")
                        pub_translate_btn = gr.Button(i18n("Translate Subtitles"), variant="secondary")
                    pub_translate_out = gr.Textbox(label=i18n("Translation output"), lines=8, interactive=False)
                with gr.Column(scale=1, min_width=280):
                    with gr.Row():
                        pub_music_db = gr.Textbox(label=i18n("Local music DB (JSON cache or folder)"), value="")
                        pub_music_db_file = gr.File(
                            label="اختر ملف قاعدة الموسيقى JSON من الكمبيوتر",
                            file_count="single", file_types=[".json"], type="filepath")
                        pub_music_btn = gr.Button(i18n("Run Music Check"), variant="secondary")
                    pub_music_out = gr.Textbox(label=i18n("Music check output"), lines=10, interactive=False)

            def load_publish_clips(project_name, source="auto", specific_file=None):
                if not project_name:
                    return gr.update(choices=[], value=None), None, "", ""
                project_path = _project_path_for_name(project_name)
                selected = file_inputs.first_path(specific_file)
                clips = publish_panel.list_clips(project_path, source, selected)
                if not clips:
                    return gr.update(choices=[], value=None), None, "", ""
                title, caption = publish_panel.clip_suggestion(project_path, clips[0])
                return (gr.update(choices=clips, value=clips[0]), clips[0],
                        title, caption)

            def select_publish_clip(project_name, clip_path):
                if not project_name or not clip_path:
                    return None, "", "", ""
                project_path = _project_path_for_name(project_name)
                title, caption = publish_panel.clip_suggestion(project_path, clip_path)
                preview = publish_panel.clip_subtitle_preview(project_path, clip_path)
                return clip_path, title, caption, preview

            def translate_publish_clip(project_name, clip_path, lang):
                if not project_name:
                    return i18n("Error: No project selected.")
                project_path = _project_path_for_name(project_name)
                ok, msg = publish_panel.translate_clip(project_path, clip_path, lang)
                if ok:
                    preview = publish_panel.clip_subtitle_preview(project_path, clip_path)
                    return msg + "\n\n" + preview
                return msg

            def music_check_publish(project_name, db_path):
                if not project_name:
                    return i18n("Error: No project selected.")
                project_path = _project_path_for_name(project_name)
                return publish_panel.run_music_check(project_path, db_path or "")

            def publish_readiness(project_name, source, specific_file, platform,
                                  privacy_status, publish_at, dry_run, oauth_file,
                                  full_access=False):
                """Build a local/YouTube readiness report without uploading."""
                if not project_name:
                    return "❌ اختر مشروعاً أولاً."
                project_path = _project_path_for_name(project_name)
                if not project_path or not os.path.isdir(project_path):
                    return "❌ مسار المشروع غير موجود."
                selected = file_inputs.first_path(specific_file)
                clips = publish_panel.list_clips(project_path, source, selected)
                expected = _expected_project_clip_count(project_path)
                lines = ["تدقيق المشروع: {}".format(os.path.basename(project_path))]
                lines.append("الملفات المتاحة من المصدر {}: {}".format(source, len(clips)))
                if expected:
                    lines.append("segments الحالية: {} — اكتمال المخرجات: {}".format(
                        expected, "✅" if len(clips) >= expected or source == "specific_file" else "❌ ناقص"))
                else:
                    lines.append("⚠️ تعذر قراءة عدد segments الحالي؛ راجع viral_segments.txt")
                if not clips:
                    lines.append("❌ لا توجد ملفات MP4 صالحة من المصدر المحدد.")
                else:
                    lines.append("أول ملف مختار: {}".format(os.path.basename(clips[0])))

                polish_path = os.path.join(project_path, "polish_report.json")
                try:
                    with open(polish_path, "r", encoding="utf-8") as stream:
                        polish_report = json.load(stream) or {}
                    summary = polish_report.get("summary") or {}
                    quality = {k: int(summary.get(k, 0) or 0) for k in ("enhanced", "partial", "fallback", "failed", "degraded")}
                    lines.append("Polish: enhanced={}، partial={}، fallback={}، failed={}".format(
                        quality["enhanced"], quality["partial"], quality["fallback"], quality["failed"]))
                    if quality["degraded"] or quality["fallback"] or quality["failed"]:
                        lines.append("⚠️ توجد مخرجات Polish متدهورة؛ لا تستخدم final_polished للرفع الحقيقي قبل مراجعتها.")
                except (OSError, ValueError, TypeError):
                    lines.append("ℹ️ لا يوجد polish_report صالح لهذا المشروع.")

                tracking_path = os.path.join(project_path, "tracking_report.json")
                try:
                    with open(tracking_path, "r", encoding="utf-8") as stream:
                        tracking = json.load(stream) or {}
                    lines.append("التتبع: backend={}، active_speaker_applied={}".format(
                        tracking.get("backend", "غير معروف"), tracking.get("active_speaker_applied", False)))
                except (OSError, ValueError, TypeError):
                    lines.append("ℹ️ لم يُنشأ tracking_report.json؛ راجع إعدادات الوجه.")

                try:
                    from scripts import content_guard
                    state = content_guard.channel_status(project_path, "youtube")
                    if state.get("locked"):
                        lines.append("⛔ قاطع قناة YouTube مقفول بسبب حادثة سياسة محلية.")
                    else:
                        lines.append("✅ قاطع مخاطر القناة غير مقفول محلياً.")
                except Exception as exc:
                    lines.append("⚠️ تعذر قراءة قاطع القناة: {}".format(str(exc)[:160]))

                if str(platform or "youtube").lower() != "youtube":
                    lines.append("ℹ️ المنصة الحالية ليست YouTube؛ فحص OAuth غير مطلوب لهذا المسار.")
                    return "\n".join(lines)
                try:
                    preflight = prepare_youtube_preflight(
                        project_path,
                        oauth_file=oauth_file,
                        dry_run=bool(dry_run),
                        privacy_status=privacy_status or "private",
                        publish_at=publish_at or None,
                        schedule_interval_minutes=60,
                        expected_clips=1,
                        full_access=bool(full_access),
                    )
                    channel = preflight.get("channel") or {}
                    lines.append("✅ OAuth والقناة جاهزان: {} ({})".format(
                        channel.get("title") or "YouTube", channel.get("id") or "غير متاح"))
                    lines.append("الوضع: {}".format(
                        "Dry Run — لا رفع فعلي" if dry_run else "رفع فعلي محدد؛ راجع التقرير قبل التأكيد"))
                    if publish_at:
                        lines.append("الجدولة: {}".format(preflight.get("schedule_start", publish_at)))
                    lines.extend("⚠️ " + str(warning) for warning in preflight.get("warnings", []))
                except Exception as exc:
                    lines.append("❌ فشل preflight: {}".format(str(exc)[:500]))
                return "\n".join(lines)

            def youtube_oauth_status_text(full_access=False, prefix=""):
                from webui.youtube_credentials import scopes_for_access, status
                current = status(full_access)
                scope = scopes_for_access(full_access)[0]
                lines = [prefix] if prefix else []
                lines.append("✅ ملف client_secrets محفوظ" if current["client_secrets_present"] else "⚠️ لم يتم اختيار client_secrets.json")
                if current["token_present"]:
                    try:
                        from scripts.upload_gate import YouTubeUploader
                        YouTubeUploader(WORKING_DIR, dry_run=True, oauth_full_access=full_access).ensure_authenticated()
                        lines.append("✅ قناة YouTube متصلة — التوكن صالح")
                    except Exception as exc:
                        lines.append("⚠️ التوكن موجود لكنه غير صالح: {}".format(str(exc)[:220]))
                else:
                    lines.append("ℹ️ لم يتم تسجيل الدخول بعد")
                try:
                    from scripts import content_guard
                    channel_state = content_guard.channel_status(VIRALS_DIR, "youtube")
                    if channel_state.get("locked"):
                        lines.append("⛔ الرفع الآلي مقفول: سُجلت حادثة سياسة/إنذار في قاعدة المشروع.")
                        latest = (channel_state.get("incidents") or [{}])[0]
                        if latest.get("detail"):
                            lines.append("آخر حادثة: {}".format(str(latest["detail"])[:220]))
                    else:
                        lines.append("✅ قاطع مخاطر القناة: لا توجد حادثة سياسة مسجلة محلياً")
                except Exception as exc:
                    lines.append("⚠️ تعذر قراءة قاطع مخاطر القناة: {}".format(str(exc)[:160]))
                lines.append("النطاق: {}".format(scope))
                return "\n".join(lines)

            def validate_youtube_oauth(oauth_file, full_access=False):
                try:
                    from webui.youtube_credentials import (
                        replace_client_secrets,
                        store_client_secrets,
                    )
                    if oauth_file:
                        stored = store_client_secrets(oauth_file)
                        if stored.get("changed"):
                            replace_client_secrets(oauth_file, invalidate_token=True)
                            return youtube_oauth_status_text(
                                full_access,
                                "🔁 تم تغيير ملف OAuth وإبطال التوكن القديم. اضغط «تسجيل الدخول إلى YouTube» لربط القناة من جديد.",
                            )
                        return youtube_oauth_status_text(
                            full_access,
                            "✅ الملف صالح ومحفوظ — نوع العميل: {}".format(stored.get("client_type", "?")),
                        )
                    return youtube_oauth_status_text(full_access)
                except Exception as exc:
                    return "❌ تم رفض ملف OAuth: {}".format(str(exc)[:500])

            def replace_youtube_oauth(oauth_file, full_access=False):
                if not oauth_file:
                    return "❌ اختر ملف client_secrets.json الجديد أولاً."
                try:
                    from webui.youtube_credentials import replace_client_secrets
                    stored = replace_client_secrets(oauth_file, invalidate_token=True)
                    return youtube_oauth_status_text(
                        full_access,
                        "🔁 تم استبدال الملف وإبطال التوكن القديم. اضغط تسجيل الدخول الآن. نوع العميل: {}".format(
                            stored.get("client_type", "?")),
                    )
                except Exception as exc:
                    return "❌ فشل استبدال ملف OAuth: {}".format(str(exc)[:500])

            def start_youtube_oauth(oauth_file, full_access=False):
                try:
                    from webui.youtube_credentials import (
                        replace_client_secrets,
                        status,
                        store_client_secrets,
                    )
                    if oauth_file:
                        stored = store_client_secrets(oauth_file)
                        if stored.get("changed"):
                            replace_client_secrets(oauth_file, invalidate_token=True)
                        secrets_path = stored["path"]
                    else:
                        current = status(full_access)
                        if not current["client_secrets_present"]:
                            return "❌ اختر client_secrets.json أولاً ثم اضغط حفظ/التحقق."
                        secrets_path = current["client_secrets_path"]
                    from scripts.upload_gate import YouTubeUploader
                    uploader = YouTubeUploader(
                        WORKING_DIR, dry_run=True,
                        client_secrets_path=secrets_path,
                        oauth_full_access=full_access,
                    )
                    token_path = uploader.auth()
                    channel = uploader.verify_channel()
                    channel_label = channel.get("title") or "قناة YouTube"
                    channel_id = channel.get("id") or "غير متاح"
                    return youtube_oauth_status_text(
                        full_access,
                        "✅ تم ربط قناة YouTube: {}\nمعرّف القناة: {}\nتم حفظ التوكن في {}".format(
                            channel_label, channel_id, token_path),
                    )
                except ImportError as exc:
                    return (
                        "❌ مكتبات تسجيل الدخول إلى YouTube غير مثبتة: {}\n"
                        "ثبّت متطلبات الرفع من مجلد المشروع ثم أعد تشغيل WebUI:\n"
                        ".\\.venv\\Scripts\\python.exe -m pip install -r requirements-upload.txt"
                    ).format(str(exc)[:300])
                except Exception as exc:
                    return "❌ فشل تسجيل الدخول: {}".format(str(exc)[:600])

            def upload_publish_clip(project_name, platform, clip_path, title,
                                    caption, hashtags, oauth_file, privacy_status,
                                    publish_at, dry, music_gate, full_access=False,
                                    public_confirm=False):
                if platform == "youtube" and str(privacy_status or "private").lower() == "public" and not dry and not public_confirm:
                    yield "❌ تم إيقاف الرفع العام: فعّل تأكيد النشر العام أولاً."
                    return
                if not project_name:
                    yield i18n("Error: No project selected.")
                    return
                project_path = _project_path_for_name(project_name)
                tags = [h.strip() for h in (hashtags or "").split(",") if h.strip()]
                yield from publish_panel.stream_upload(
                    project_path, platform, clip_path, title, caption,
                    tags, dry, music_gate, oauth_file, privacy_status, publish_at,
                    full_access, public_confirm=bool(public_confirm))

            pub_refresh.click(library.refresh_projects, outputs=pub_project)
            pub_project.change(load_publish_clips,
                               inputs=[pub_project, pub_source, pub_specific_file],
                               outputs=[pub_clip, pub_preview, pub_title, pub_caption])
            pub_source.change(load_publish_clips,
                              inputs=[pub_project, pub_source, pub_specific_file],
                              outputs=[pub_clip, pub_preview, pub_title, pub_caption])
            pub_specific_file.change(load_publish_clips,
                                    inputs=[pub_project, pub_source, pub_specific_file],
                                    outputs=[pub_clip, pub_preview, pub_title, pub_caption])
            pub_clip.change(select_publish_clip,
                            inputs=[pub_project, pub_clip],
                            outputs=[pub_preview, pub_title, pub_caption, pub_sub_preview])
            pub_translate_btn.click(translate_publish_clip,
                                    inputs=[pub_project, pub_clip, pub_lang],
                                    outputs=pub_translate_out)
            pub_music_db_file.change(
                lambda value: file_inputs.first_path(value),
                inputs=pub_music_db_file, outputs=pub_music_db)
            pub_music_btn.click(music_check_publish,
                                inputs=[pub_project, pub_music_db],
                                outputs=pub_music_out)
            pub_audit_btn.click(
                publish_readiness,
                inputs=[pub_project, pub_source, pub_specific_file, pub_platform,
                        pub_privacy, pub_publish_at, pub_dry, pub_youtube_oauth, pub_full_oauth],
                outputs=pub_readiness_out,
            )
            def _oauth_status_pair(text):
                return text, text

            def connect_youtube_from_processing(enabled, oauth_file, full_access=False):
                if not enabled:
                    return _oauth_status_pair("ℹ️ تم تعطيل شرط ربط القناة قبل البدء. يمكنك تفعيله لاحقاً لبدء OAuth.")
                result = start_youtube_oauth(oauth_file, full_access)
                return _oauth_status_pair(result)

            demo.load(lambda: _oauth_status_pair(youtube_oauth_status_text(False)),
                      outputs=[pub_oauth_status, auto_upload_channel_status])
            pub_check_oauth_btn.click(
                lambda f, full: _oauth_status_pair(validate_youtube_oauth(f, full)),
                inputs=[pub_youtube_oauth, pub_full_oauth],
                outputs=[pub_oauth_status, auto_upload_channel_status])
            pub_replace_oauth_btn.click(
                lambda f, full: _oauth_status_pair(replace_youtube_oauth(f, full)),
                inputs=[pub_youtube_oauth, pub_full_oauth],
                outputs=[pub_oauth_status, auto_upload_channel_status])
            pub_login_btn.click(
                lambda f, full: _oauth_status_pair(start_youtube_oauth(f, full)),
                inputs=[pub_youtube_oauth, pub_full_oauth],
                outputs=[pub_oauth_status, auto_upload_channel_status])
            pub_youtube_oauth.change(
                lambda f, full: _oauth_status_pair(validate_youtube_oauth(f, full)),
                inputs=[pub_youtube_oauth, pub_full_oauth],
                outputs=[pub_oauth_status, auto_upload_channel_status])
            connect_youtube_before_start_btn.click(
                connect_youtube_from_processing,
                inputs=[require_youtube_connection_input, auto_upload_oauth_file_input, pub_full_oauth],
                outputs=[pub_oauth_status, auto_upload_channel_status])
            require_youtube_connection_input.change(
                connect_youtube_from_processing,
                inputs=[require_youtube_connection_input, auto_upload_oauth_file_input, pub_full_oauth],
                outputs=[pub_oauth_status, auto_upload_channel_status])
            auto_upload_oauth_file_input.change(
                lambda f, full: _oauth_status_pair(validate_youtube_oauth(f, full)),
                inputs=[auto_upload_oauth_file_input, pub_full_oauth],
                outputs=[pub_oauth_status, auto_upload_channel_status])
            pub_upload_btn.click(upload_publish_clip,
                                 inputs=[pub_project, pub_platform, pub_clip,
                                         pub_title, pub_caption, pub_hashtags,
                                         pub_youtube_oauth, pub_privacy,
                                         pub_publish_at, pub_dry, pub_music_gate,
                                         pub_full_oauth, pub_public_confirm],
                                 outputs=pub_log)

            with gr.Accordion(i18n("🧠 SEO Tools (v7.22): اقتراح عناوين وأوقات النشر"), open=False):
                gr.Markdown("**عناوين SEO ذكية:** أدخل موضوع المقطع — تحصل على عناوين مقترحة مرتبة بدرجات، مع جلب اقتراحات البحث من يوتيوب (بدون مفتاح API). **أوقات النشر:** احسب أفضل الأوقات تلقائياً أو حدد ساعاتك المفضلة.", elem_classes=["vc-help-card"])
                with gr.Row():
                    seo_topic_input = gr.Textbox(label=i18n("Topic (موضوع المقطع)"), placeholder="مثال: كسب المال من الانترنت", scale=3)
                    seo_keywords_input = gr.Textbox(label=i18n("Keywords (كلمات مفتاحية، مفصولة بفاصلة)"), placeholder="كسب المال، الربح، العمل من المنزل", scale=2)
                    seo_platform_input = gr.Dropdown(choices=["youtube", "tiktok", "reels"], value="youtube", label=i18n("Platform"), scale=1)
                with gr.Row():
                    seo_gen_btn = gr.Button(i18n("✨ Generate SEO Titles"), size="sm")
                    seo_suggest_btn = gr.Button(i18n("🔍 YouTube Suggestions"), size="sm")
                    seo_slots_btn = gr.Button(i18n("🕐 Best Publish Times"), size="sm")
                seo_out = gr.Textbox(label=i18n("Result"), lines=10, interactive=False)

                def run_seo_titles(topic, keywords, platform):
                    try:
                        from scripts import seo_titles
                        kws = [k.strip() for k in (keywords or "").split(",") if k.strip()]
                        titles = seo_titles.generate_titles(topic or "مقاطع قصيرة", kws, count=6)
                        lines = ["## عناوين مقترحة (مرتبة بالدرجة):"]
                        for t in titles:
                            lines.append("• [{}] {}".format(t["score"], t["title"]))
                        if kws:
                            lines.append("")
                            lines.append("## اقتراحات بحث يوتيوب:")
                            for s in seo_titles.fetch_suggestions(topic or kws[0]):
                                lines.append("  - " + s)
                        lines.append("")
                        lines.append("## أفضل أوقات النشر ({}):".format(platform))
                        for slot in seo_titles.suggest_next_slots(platform, count=4):
                            lines.append("  - " + slot)
                        return "\n".join(lines)
                    except Exception as exc:
                        return "❌ " + str(exc)

                def run_seo_suggestions(topic, keywords, platform):
                    try:
                        from scripts import seo_titles
                        kws = [k.strip() for k in (keywords or "").split(",") if k.strip()]
                        query = topic or (kws[0] if kws else "")
                        if not query:
                            return "❌ أدخل موضوعاً أو كلمة مفتاحية أولاً."
                        results = seo_titles.fetch_suggestions(query)
                        if not results:
                            return "لا توجد اقتراحات (قد يكون الطلب بدون نتائج أو الشبكة غير متاحة)."
                        return "\n".join("• " + s for s in results)
                    except Exception as exc:
                        return "❌ " + str(exc)

                def run_seo_slots(topic, keywords, platform):
                    try:
                        from scripts import seo_titles
                        slots = seo_titles.suggest_next_slots(platform, count=6)
                        return "أفضل أوقات النشر القادمة ({}):\n{}".format(
                            platform, "\n".join("  - " + s for s in slots))
                    except Exception as exc:
                        return "❌ " + str(exc)

                seo_gen_btn.click(run_seo_titles,
                                  inputs=[seo_topic_input, seo_keywords_input, seo_platform_input],
                                  outputs=seo_out)
                seo_suggest_btn.click(run_seo_suggestions,
                                      inputs=[seo_topic_input, seo_keywords_input, seo_platform_input],
                                      outputs=seo_out)
                seo_slots_btn.click(run_seo_slots,
                                    inputs=[seo_topic_input, seo_keywords_input, seo_platform_input],
                                    outputs=seo_out)


        with gr.Tab("🗂️ " + i18n("Library")):
            gr.Markdown(f"### {i18n('Existing Projects')}")
            with gr.Row():
                lib_query_input = gr.Textbox(label=i18n("Search by name"), placeholder=i18n("Type part of a project name"))
                lib_date_from_input = gr.Textbox(label=i18n("From date"), placeholder="YYYY-MM-DD")
                lib_date_to_input = gr.Textbox(label=i18n("To date"), placeholder="YYYY-MM-DD")
                lib_filter_btn = gr.Button(i18n("Filter"))
            with gr.Row():
                project_dropdown = gr.Dropdown(choices=library.get_existing_projects(force_refresh=True), label=i18n("Choose a Project"), value=None)
                refresh_btn = gr.Button(i18n("Refresh List"))
            project_gallery_html = gr.HTML()
            refresh_btn.click(library.refresh_projects, outputs=project_dropdown)
            lib_filter_btn.click(library.filter_projects, inputs=[lib_query_input, lib_date_from_input, lib_date_to_input], outputs=project_dropdown)
            def on_select_project(proj_name): return library.generate_project_gallery(proj_name)
            project_dropdown.change(on_select_project, project_dropdown, project_gallery_html)

            gr.Markdown("### النسخ الاحتياطي والاستعادة الآمنة")
            gr.Markdown("يستبعد النسخ الاحتياطي تلقائياً توكن OAuth وclient_secrets وملفات cache. فعّل تضمين الوسائط فقط إذا أردت نسخة كبيرة تشمل الفيديو والصوت.")
            with gr.Row():
                lib_backup_dir = gr.Textbox(
                    label="مجلد النسخ الاحتياطية",
                    value=os.path.join(VIRALS_DIR, ".backups"),
                )
                lib_backup_include_media = gr.Checkbox(
                    label="تضمين الفيديو والصوت", value=False,
                )
                lib_backup_btn = gr.Button("إنشاء نسخة احتياطية", variant="primary")
            lib_backup_out = gr.Textbox(label="نتيجة النسخ الاحتياطي", lines=3, interactive=False)
            with gr.Row():
                lib_restore_file = gr.File(label="ملف النسخة الاحتياطية ZIP", file_count="single", type="filepath")
                lib_restore_root = gr.Textbox(label="مجلد الاستعادة", value=VIRALS_DIR)
                lib_restore_btn = gr.Button("استعادة إلى مشروع جديد", variant="secondary")
            lib_restore_out = gr.Textbox(label="نتيجة الاستعادة", lines=3, interactive=False)

            def _create_project_backup(project_name, destination, include_media):
                project_path = _project_path_for_name(project_name)
                if not project_path or not os.path.isdir(project_path):
                    return "❌ اختر مشروعاً صالحاً من المكتبة أولاً."
                try:
                    result = backup.create_backup(project_path, destination, bool(include_media))
                    return "✅ تم إنشاء النسخة: {}\nالملفات: {} · الوسائط: {}".format(
                        result["path"], result["files"], "نعم" if result["include_media"] else "لا")
                except Exception as exc:
                    return "❌ فشل إنشاء النسخة: {}".format(exc)

            def _restore_project_backup(file_value, destination):
                path = file_inputs.first_path(file_value)
                if not path:
                    return "❌ اختر ملف ZIP صالحاً أولاً."
                try:
                    restored = backup.restore_backup(path, destination or VIRALS_DIR)
                    return "✅ تمت الاستعادة إلى مشروع جديد: {}".format(restored)
                except Exception as exc:
                    return "❌ فشل الاستعادة: {}".format(exc)

            lib_backup_btn.click(
                _create_project_backup,
                inputs=[project_dropdown, lib_backup_dir, lib_backup_include_media],
                outputs=lib_backup_out,
            )
            lib_restore_btn.click(
                _restore_project_backup,
                inputs=[lib_restore_file, lib_restore_root],
                outputs=lib_restore_out,
            )
    



        with gr.Tab("📋 " + i18n("Batch Queue")):
            gr.Markdown(f"### {i18n('Batch Queue')}")
            gr.Markdown(i18n("One YouTube URL per line. The queue processes them one by one with the current settings."))
            batch_urls_input = gr.Textbox(
                label=i18n("YouTube URLs"), lines=6,
                placeholder="https://youtube.com/watch?v=...\nhttps://youtube.com/watch?v=...",
            )
            batch_df = gr.Dataframe(
                headers=batch_queue.HEADERS,
                datatype=["number", "str", "str"],
                interactive=False,
                label=i18n("Queue Status"),
            )
            with gr.Row():
                batch_priority_input = gr.Number(
                    label="أولوية المهام (-5 إلى 5)", value=0, precision=0,
                    minimum=-5, maximum=5, step=1,
                )
                batch_run_btn = gr.Button(i18n("Run Queue"), variant="primary")
                batch_pause_btn = gr.Button("⏸ إيقاف مؤقت", variant="secondary")
                batch_resume_btn = gr.Button("▶ استئناف", variant="secondary")
                batch_cancel_btn = gr.Button("⛔ إلغاء", variant="stop")
                batch_retry_btn = gr.Button("↻ إعادة الفاشل", variant="secondary")
                batch_refresh_btn = gr.Button("🔄 تحديث", variant="secondary")
            batch_summary = gr.Markdown()

            def _batch_view(job_ids=None):
                ids = list(job_ids if job_ids is not None else current_batch_job_ids)
                if not ids:
                    snapshots = list(batch_render_queue.snapshot().values())
                else:
                    snapshots = [batch_render_queue.snapshot(job_id) for job_id in ids]
                    snapshots = [snapshot for snapshot in snapshots if snapshot]
                items = []
                counts = {"queued": 0, "running": 0, "retrying": 0, "succeeded": 0, "failed": 0, "cancelled": 0}
                for snapshot in snapshots:
                    status = snapshot.get("status", "queued")
                    counts[status] = counts.get(status, 0) + 1
                    mapped = {"succeeded": "done", "failed": "failed", "cancelled": "failed",
                              "cancelling": "running", "retrying": "running"}.get(status, status)
                    items.append({"url": snapshot.get("metadata", {}).get("url", ""), "status": mapped})
                if not snapshots:
                    return [], "لا توجد مهام محفوظة في العرض الحالي."
                state = "متوقف مؤقتاً" if batch_render_queue.paused else "قيد التشغيل"
                summary = (
                    "**حالة الطابور:** {}  \n"
                    "المعلقة: {} · الجارية: {} · المعاد تشغيلها: {} · "
                    "الناجحة: {} · الفاشلة: {} · الملغاة: {}"
                ).format(state, counts.get("queued", 0), counts.get("running", 0),
                          counts.get("retrying", 0), counts.get("succeeded", 0),
                          counts.get("failed", 0), counts.get("cancelled", 0))
                if batch_render_queue.state_warning:
                    summary += "  \n⚠️ " + batch_render_queue.state_warning
                return batch_queue.rows_from_items(items), summary

            def _batch_pause():
                batch_render_queue.pause_all()
                return _batch_view()

            def _batch_resume():
                batch_render_queue.resume_all()
                return _batch_view()

            def _batch_cancel():
                for job_id in list(current_batch_job_ids):
                    try:
                        job = batch_render_queue.get(job_id)
                        if job and job.status not in render_queue.TERMINAL_STATES:
                            batch_render_queue.cancel(job_id)
                    except Exception:
                        continue
                return _batch_view()

            def _batch_retry_failed():
                batch_render_queue.retry_failed(list(current_batch_job_ids))
                return _batch_view()

            def _batch_refresh():
                return _batch_view()

            batch_pause_btn.click(_batch_pause, outputs=[batch_df, batch_summary])
            batch_resume_btn.click(_batch_resume, outputs=[batch_df, batch_summary])
            batch_cancel_btn.click(_batch_cancel, outputs=[batch_df, batch_summary])
            batch_retry_btn.click(_batch_retry_failed, outputs=[batch_df, batch_summary])
            batch_refresh_btn.click(_batch_refresh, outputs=[batch_df, batch_summary])
            demo.load(_batch_view, outputs=[batch_df, batch_summary])

            def run_batch(urls_text, *rest):
                global current_batch_job_ids
                urls = batch_queue.parse_queue_text(urls_text)
                if not urls:
                    yield ([], i18n("Queue is empty."), "", gr.update(), gr.update(),
                           None, gr.update(), gr.update(), gr.update())
                    return
                invalid = batch_queue.invalid_urls(urls)
                if invalid:
                    detail = "\n".join("- " + str(url) for url in invalid[:20])
                    yield ([], i18n("Invalid YouTube URL(s):") + "\n" + detail, "",
                           gr.update(), gr.update(), None, gr.update(), gr.update(), gr.update())
                    return

                # Store the plan without the password-like API field. The key
                # is resolved from settings_store/environment by the child
                # process, so restarting the WebUI never writes it to disk.
                plan_rest = list(rest)
                priority = 0
                if plan_rest:
                    try:
                        priority = max(-5, min(5, int(float(plan_rest.pop(0) or 0))))
                    except (TypeError, ValueError):
                        priority = 0
                if len(plan_rest) > 9:
                    plan_rest[9] = None
                batch_id = uuid.uuid4().hex
                job_ids = []
                for url in urls:
                    job_ids.append(batch_render_queue.add(
                        {"url": url, "rest": plan_rest, "batch_id": batch_id},
                        metadata={"url": url, "batch_id": batch_id},
                        priority=priority,
                    ))
                current_batch_job_ids = list(job_ids)

                def queue_runner(job, cancel_event, progress):
                    plan = job.plan or {}
                    url = plan.get("url", "")
                    saved_rest = list(plan.get("rest") or [])
                    if cancel_event.is_set():
                        raise RuntimeError("cancelled")
                    progress(5, "Starting " + url)
                    final_update = None
                    for update in run_viral_cutter("YouTube URL", None, url, *saved_rest):
                        if cancel_event.is_set():
                            kill_process()
                            raise RuntimeError("cancelled")
                        final_update = update
                        batch_render_queue.update_metadata(
                            job.id,
                            logs=update[0] or "",
                            result_html=update[3] or "",
                            progress_html=update[4] or "",
                            tasks_html=update[5] or "",
                            errors_html=update[6] or "",
                        )
                        progress(50, "Processing " + url)
                    final_logs = (final_update or ("",))[0]
                    if not batch_queue.looks_completed(final_logs):
                        raise RuntimeError("pipeline did not report completion")
                    progress(100, "Completed " + url)
                    return (final_update or ("", "", "", None, "", "", ""))[3]

                batch_render_queue.start(queue_runner)
                progress_state = empty_progress_state(i18n("Starting"))
                initial_items = [{"url": url, "status": "pending"} for url in urls]
                yield (batch_queue.rows_from_items(initial_items), "",
                       "", gr.update(value=i18n("Running..."), interactive=False),
                       gr.update(visible=True, interactive=True), None,
                       render_progress_html(progress_state),
                       render_tasks_html(progress_state),
                       render_error_html([]))

                while True:
                    snapshots = [batch_render_queue.snapshot(job_id) for job_id in job_ids]
                    items = []
                    latest = None
                    for snapshot in snapshots:
                        if not snapshot:
                            continue
                        status = snapshot["status"]
                        mapped = {"succeeded": "done", "failed": "failed",
                                  "cancelled": "failed", "cancelling": "running"}.get(status, status)
                        items.append({"url": snapshot["metadata"].get("url", ""), "status": mapped})
                        latest = snapshot
                    if latest:
                        yield (
                            batch_queue.rows_from_items(items), "",
                            latest.get("metadata", {}).get("logs", ""),
                            gr.update(value=i18n("Running..."), interactive=False),
                            gr.update(visible=True, interactive=True),
                            latest.get("metadata", {}).get("result_html"),
                            latest.get("metadata", {}).get("progress_html", ""),
                            latest.get("metadata", {}).get("tasks_html", ""),
                            latest.get("metadata", {}).get("errors_html", ""),
                        )
                    if snapshots and all(s and s["status"] in render_queue.TERMINAL_STATES for s in snapshots):
                        break
                    time.sleep(0.25)

                ok = sum(1 for job_id in job_ids if batch_render_queue.snapshot(job_id)["status"] == "succeeded")
                failed = len(job_ids) - ok
                yield (batch_queue.rows_from_items(items),
                       i18n("Finished: {} succeeded, {} failed.").format(ok, failed),
                       "", gr.update(value=i18n("Start Processing"), interactive=True),
                       gr.update(visible=True, interactive=False), None,
                       gr.update(), gr.update(), gr.update())


        with gr.Tab("🧠 " + i18n("Teach the Tool")):
            gr.Markdown(f"### {i18n('Teach the Tool')}")
            gr.Markdown(i18n("The tool learns from your channel: add words a struck/rejected clip contained, allow words the blocklist wrongly flags, or extract patterns from a blocked project."))
            with gr.Row():
                learn_term = gr.Textbox(label=i18n("Word / phrase"), placeholder=i18n("e.g. a word from the struck clip"))
                learn_severity = gr.Dropdown(
                    choices=[(i18n("High"), "high"), (i18n("Medium"), "medium"), (i18n("Low"), "low")],
                    label=i18n("Severity"), value="high")
            learn_reason = gr.Textbox(label=i18n("Reason (optional)"), placeholder=i18n("e.g. strike on video X"))
            with gr.Row():
                learn_add_btn = gr.Button(i18n("🚫 Block this word"), variant="primary")
                learn_allow_btn = gr.Button(i18n("✅ Allow this word (false positive)"))
                learn_remove_btn = gr.Button(i18n("🗑 Remove"))
            learn_feedback = gr.Textbox(label=i18n("Result"), lines=3, interactive=False)
            gr.Markdown("### " + i18n("Learn from a blocked project"))
            with gr.Row():
                learn_project = gr.Dropdown(choices=library.get_existing_projects(),
                                            label=i18n("Blocked project"), value=None)
                learn_apply = gr.Checkbox(label=i18n("Apply (teach the extracted patterns)"), value=False)
                learn_extract_btn = gr.Button(i18n("🔍 Extract patterns"), variant="secondary")
            learn_extract_out = gr.Textbox(label=i18n("Extracted patterns"), lines=8, interactive=False)
            with gr.Row():
                learn_terms_btn = gr.Button(i18n("📋 Show my custom terms"))
                learn_stats_btn = gr.Button(i18n("📓 Learning journal"))
            learn_terms_out = gr.Textbox(label=i18n("Custom terms / journal"), lines=10, interactive=False)

            def _learn_add(term, severity, reason):
                return learn_panel.add_term(term, severity, reason)

            def _learn_allow(term, reason):
                return learn_panel.allow_term(term, reason)

            def _learn_remove(term):
                return learn_panel.remove_term(term)

            def _learn_extract(project_name, apply):
                return learn_panel.extract_from_project(project_name, apply)

            learn_add_btn.click(_learn_add, inputs=[learn_term, learn_severity, learn_reason],
                                outputs=learn_feedback)
            learn_allow_btn.click(_learn_allow, inputs=[learn_term, learn_reason],
                                  outputs=learn_feedback)
            learn_remove_btn.click(_learn_remove, inputs=[learn_term], outputs=learn_feedback)
            learn_extract_btn.click(_learn_extract, inputs=[learn_project, learn_apply],
                                    outputs=learn_extract_out)
            learn_terms_btn.click(lambda: learn_panel.list_terms(), outputs=learn_terms_out)
            learn_stats_btn.click(lambda: learn_panel.show_stats(), outputs=learn_terms_out)

        with gr.Tab("📈 " + i18n("Performance")):
            gr.Markdown(f"### {i18n('Performance (YouTube Analytics)')}")
            gr.Markdown(i18n("See which clips actually performed so future selections learn from outcomes. First run opens a browser to authorize (read-only)."))
            with gr.Row():
                perf_days = gr.Number(label=i18n("Days"), value=28, precision=0)
                perf_summary_btn = gr.Button(i18n("📈 Channel summary"), variant="primary")
                perf_top_btn = gr.Button(i18n("🏆 Top clips"))
                perf_trends_btn = gr.Button(i18n("📅 Daily views"))
                perf_local_btn = gr.Button("📁 تحليل سجل الرفع المحلي")
            perf_out = gr.Textbox(label=i18n("Analytics report"), lines=12, interactive=False)

            def _perf(kind, days):
                try:
                    days = int(float(days or 28))
                except Exception:
                    days = 28
                return learn_panel.run_analytics(kind, days=days)

            perf_summary_btn.click(lambda d: _perf("summary", d), inputs=[perf_days], outputs=perf_out)
            perf_top_btn.click(lambda d: _perf("top", d), inputs=[perf_days], outputs=perf_out)
            perf_trends_btn.click(lambda d: _perf("trends", d), inputs=[perf_days], outputs=perf_out)
            perf_local_btn.click(lambda d: _perf("local", d), inputs=[perf_days], outputs=perf_out)
    with gr.Accordion("📜 سجل التشغيل", open=False, elem_id="vc-log-monitor"):
        with gr.Row():
            logs_output = gr.Textbox(label="تحديثات التشغيل", lines=12, autoscroll=True, elem_id="logs_output", scale=9)
            with gr.Column(scale=1, min_width=110):
                gr.Markdown("&nbsp;")
                clear_log_btn = gr.Button("🗑️ مسح السجل", size="sm")
    logs_output.change(fn=None, inputs=[], outputs=[], js="function() { var ta = document.querySelector('#logs_output textarea'); if (ta) { if (!ta._scrollerSetup) { ta._isSticky = true; ta.addEventListener('scroll', function() { var diff = ta.scrollHeight - ta.scrollTop - ta.clientHeight; ta._isSticky = diff <= 50; }); ta._scrollerSetup = true; } if (ta._isSticky === undefined || ta._isSticky === true) { ta.scrollTop = ta.scrollHeight; } } }")
    clear_log_btn.click(lambda: "", outputs=logs_output)

    # --- v6.9.2: remember EVERY form field (set once, stays forever) ---
    PREF_FIELDS.extend([
        (video_quality_input, "video_quality"),
        (translate_input, "translate_target"),
        (use_youtube_subs_input, "use_youtube_subs"),
        (safety_mode_input, "safety_mode"),
        (safety_ai_input, "safety_ai"),
        (platform_input, "platform"),
        (metadata_gate_input, "metadata_gate"),
        (title_language_input, "title_language"),
        (polish_input, "polish"),
        (music_input, "music"),
        (logo_input, "logo"),
        (broll_input, "broll"),
        (broll_query_input, "broll_query"),
        (broll_opacity_input, "broll_opacity"),
        (sfx_dir_input, "sfx_dir"),
        (sfx_volume_input, "sfx_volume"),
        (cookies_input, "cookies"),
        (model_input, "whisper_model"),
        (transcription_device_input, "transcription_device"),
        (workflow_input, "workflow"),
        (aspect_input, "output_aspect"),
        (reframe_mode_input, "reframe_mode"),
        (force_new_segments_input, "force_new_segments"),
        (visual_check_input, "visual_check"),
        (visual_gate_input, "visual_gate"),
        (visual_frames_input, "visual_frames"),
        (visual_model_input, "visual_model"),
        (auto_download_visual_input, "auto_download_visual"),
        (watermark_position_input, "watermark_position"),
        (watermark_size_input, "watermark_size"),
        (watermark_opacity_input, "watermark_opacity"),
        (require_youtube_connection_input, "require_youtube_connection"),
        (auto_upload_after_processing_input, "auto_upload_after_processing"),
        (auto_upload_dry_run_input, "auto_upload_dry_run"),
        (auto_upload_source_input, "auto_upload_source"),
        (auto_upload_privacy_input, "auto_upload_privacy"),
        (auto_upload_publish_at_input, "auto_upload_publish_at"),
        (auto_upload_public_confirm_input, "auto_upload_public_confirm"),
        (auto_upload_interval_minutes_input, "auto_upload_interval_minutes"),
        (batch_priority_input, "batch_priority"),
    ])
    for comp, _key in PREF_FIELDS:
        comp.change(autosave_webui_prefs, outputs=[])
    demo.load(restore_webui_prefs, outputs=[comp for comp, _ in PREF_FIELDS])

    processing_validation_inputs = [
        input_source, project_selector, url_input, video_upload, segments_input,
        min_dur_input, max_dur_input, workflow_input, ai_backend_input,
        transcription_device_input, safety_mode_input, visual_check_input,
        visual_model_input, logo_input, music_input,
        auto_upload_after_processing_input, auto_upload_dry_run_input,
        auto_upload_source_input, auto_upload_specific_file_input,
        auto_upload_privacy_input, auto_upload_publish_at_input,
        auto_upload_public_confirm_input, auto_upload_interval_minutes_input,
    ]
    validate_config_btn.click(
        validate_processing_ui,
        inputs=processing_validation_inputs,
        outputs=create_config_status,
    )

    # kill_process returns 6 values (logs, start, stop, progress, tasks, errors)
    stop_btn.click(kill_process, outputs=[logs_output, start_btn, stop_btn, progress_panel, tasks_panel, errors_panel])


    start_btn.click(run_viral_cutter, inputs=[
    input_source, project_selector, url_input, video_upload, segments_input, viral_input, themes_input, min_dur_input, max_dur_input,
    model_input, transcription_device_input, ai_backend_input, api_key_input, ai_model_input, chunk_size_input,
    workflow_input, face_model_input, face_mode_input, face_detect_interval_input, no_face_mode_input,
    face_filter_thresh_input, face_two_thresh_input, face_conf_thresh_input, face_dead_zone_input, focus_active_speaker_input,
    active_speaker_mar_input, active_speaker_score_diff_input, include_motion_input, active_speaker_motion_threshold_input, active_speaker_motion_sensitivity_input, active_speaker_decay_input,
    face_smoothing_input, face_headroom_input,
    use_custom_subs,
    font_name_input, font_size_input, font_color_input, highlight_color_input,
    outline_color_input, outline_thickness_input, shadow_color_input, shadow_size_input,
    bold_input, italic_input, uppercase_input, vertical_pos_input, alignment_input,
    highlight_size_input, words_per_block_input, gap_limit_input, mode_input,
    underline_input, strikeout_input, border_style_input, remove_punc_input, caption_animation_input, auto_emoji_input,
    video_quality_input, use_youtube_subs_input, translate_input, safety_mode_input, safety_ai_input,
    platform_input, metadata_gate_input, title_language_input, polish_input, music_input, logo_input,
    broll_input, broll_query_input, broll_opacity_input, sfx_dir_input, sfx_volume_input, cookies_input, sponsorblock_input, live_wait_minutes_input,
    aspect_input, reframe_mode_input, force_new_segments_input,
    visual_check_input, visual_gate_input, visual_frames_input, visual_model_input, auto_download_visual_input,
    watermark_position_input, watermark_size_input, watermark_opacity_input,
    require_youtube_connection_input, auto_upload_after_processing_input, auto_upload_dry_run_input,
    auto_upload_source_input, auto_upload_specific_file_input, auto_upload_privacy_input, auto_upload_publish_at_input,
    auto_upload_public_confirm_input, auto_upload_interval_minutes_input,
    auto_upload_oauth_file_input
    ], outputs=[logs_output, start_btn, stop_btn, results_html, progress_panel, tasks_panel, errors_panel])

    review_render_btn.click(run_review_render, inputs=[
    review_project_dropdown, review_df, url_input, video_upload, segments_input, viral_input, themes_input, min_dur_input, max_dur_input,
    model_input, transcription_device_input, ai_backend_input, api_key_input, ai_model_input, chunk_size_input,
    workflow_input, face_model_input, face_mode_input, face_detect_interval_input, no_face_mode_input,
    face_filter_thresh_input, face_two_thresh_input, face_conf_thresh_input, face_dead_zone_input, focus_active_speaker_input,
    active_speaker_mar_input, active_speaker_score_diff_input, include_motion_input, active_speaker_motion_threshold_input, active_speaker_motion_sensitivity_input, active_speaker_decay_input,
    face_smoothing_input, face_headroom_input,
    use_custom_subs,
    font_name_input, font_size_input, font_color_input, highlight_color_input,
    outline_color_input, outline_thickness_input, shadow_color_input, shadow_size_input,
    bold_input, italic_input, uppercase_input, vertical_pos_input, alignment_input,
    highlight_size_input, words_per_block_input, gap_limit_input, mode_input,
    underline_input, strikeout_input, border_style_input, remove_punc_input, caption_animation_input, auto_emoji_input,
    video_quality_input, use_youtube_subs_input, translate_input, safety_mode_input, safety_ai_input,
    platform_input, metadata_gate_input, title_language_input, polish_input, music_input, logo_input,
    broll_input, broll_query_input, broll_opacity_input, sfx_dir_input, sfx_volume_input, cookies_input, sponsorblock_input, live_wait_minutes_input,
    aspect_input, reframe_mode_input, force_new_segments_input,
    visual_check_input, visual_gate_input, visual_frames_input, visual_model_input, auto_download_visual_input,
    watermark_position_input, watermark_size_input, watermark_opacity_input,
    require_youtube_connection_input, auto_upload_after_processing_input, auto_upload_dry_run_input,
    auto_upload_source_input, auto_upload_specific_file_input, auto_upload_privacy_input, auto_upload_publish_at_input,
    auto_upload_public_confirm_input, auto_upload_interval_minutes_input,
    auto_upload_oauth_file_input
    ], outputs=[logs_output, start_btn, stop_btn, results_html, progress_panel, tasks_panel, errors_panel])

    batch_run_btn.click(run_batch, inputs=[
    batch_urls_input, batch_priority_input, video_upload, segments_input, viral_input, themes_input, min_dur_input, max_dur_input,

    model_input, transcription_device_input, ai_backend_input, api_key_input, ai_model_input, chunk_size_input,
    workflow_input, face_model_input, face_mode_input, face_detect_interval_input, no_face_mode_input,
    face_filter_thresh_input, face_two_thresh_input, face_conf_thresh_input, face_dead_zone_input, focus_active_speaker_input,
    active_speaker_mar_input, active_speaker_score_diff_input, include_motion_input, active_speaker_motion_threshold_input, active_speaker_motion_sensitivity_input, active_speaker_decay_input,
    face_smoothing_input, face_headroom_input,
    use_custom_subs,
    font_name_input, font_size_input, font_color_input, highlight_color_input,
    outline_color_input, outline_thickness_input, shadow_color_input, shadow_size_input,
    bold_input, italic_input, uppercase_input, vertical_pos_input, alignment_input,
    highlight_size_input, words_per_block_input, gap_limit_input, mode_input,
    underline_input, strikeout_input, border_style_input, remove_punc_input, caption_animation_input, auto_emoji_input,
    video_quality_input, use_youtube_subs_input, translate_input, safety_mode_input, safety_ai_input,
    platform_input, metadata_gate_input, title_language_input, polish_input, music_input, logo_input,
    broll_input, broll_query_input, broll_opacity_input, sfx_dir_input, sfx_volume_input, cookies_input, sponsorblock_input, live_wait_minutes_input,
    aspect_input, reframe_mode_input, force_new_segments_input,
    visual_check_input, visual_gate_input, visual_frames_input, visual_model_input, auto_download_visual_input,
    watermark_position_input, watermark_size_input, watermark_opacity_input,
    require_youtube_connection_input, auto_upload_after_processing_input, auto_upload_dry_run_input,
    auto_upload_source_input, auto_upload_specific_file_input, auto_upload_privacy_input, auto_upload_publish_at_input,
    auto_upload_public_confirm_input, auto_upload_interval_minutes_input
    ], outputs=[batch_df, batch_summary, logs_output, start_btn, stop_btn, results_html, progress_panel, tasks_panel, errors_panel])

def _resolve_webui_host():
    """Host to bind. Defaults to loopback; VIRALCUTTER_HOST overrides it.

    Binding to 0.0.0.0 exposes the WebUI — and any file it can serve — to the
    whole network. Only do that on a network you trust.
    """
    host = os.environ.get("VIRALCUTTER_HOST", "").strip() or "127.0.0.1"
    loopback = host in ("127.0.0.1", "::1", "localhost")
    if not loopback:
        print("[webui] WARNING: binding to {} — the WebUI is reachable from the "
              "network. Set VIRALCUTTER_HOST=127.0.0.1 to bind loopback only."
              .format(host))
    return host


def _allowed_dirs():
    """Static dirs Gradio may serve — VIRALS only by default.

    The repo root holds api_config.json, crash logs and OAuth tokens; it must
    NOT be served implicitly. Power users can add extra dirs with
    VIRALCUTTER_EXTRA_STATIC_DIRS (os.pathsep-separated, e.g. "D:/media;C:/clips").
    """
    dirs = [os.path.abspath(VIRALS_DIR)]
    extra = os.environ.get("VIRALCUTTER_EXTRA_STATIC_DIRS", "").strip()
    if extra:
        for d in extra.split(os.pathsep):
            d = os.path.abspath(d.strip())
            if d and d not in dirs:
                if os.path.isdir(d):
                    dirs.append(d)
                else:
                    print("[webui] WARNING: VIRALCUTTER_EXTRA_STATIC_DIRS entry "
                          "does not exist, skipped: {}".format(d))
    return dirs


def _webui_auth():
    """Optional HTTP basic auth from VIRALCUTTER_WEBUI_USER / VIRALCUTTER_WEBUI_PASSWORD.

    Returns a (user, password) tuple or None when not configured.
    """
    user = os.environ.get("VIRALCUTTER_WEBUI_USER", "").strip()
    password = os.environ.get("VIRALCUTTER_WEBUI_PASSWORD", "")
    if user and password:
        return (user, password)
    return None


def _launch(argv=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--colab", action="store_true", help="Run in Google Colab mode")
    parser.add_argument("--preflight", choices=["auto", "check", "off"], default="auto",
                        help="Environment check before boot: 'auto' (default) checks everything "
                             "and auto-installs missing core dependencies, 'off' skips it.")
    args = parser.parse_args(argv)

    # v7.18: continuous safety-data refresh. A daemon thread keeps the
    # hate-speech blocklist and the YouTube policy-watch feed fresh for as
    # long as the WebUI runs (first check at boot, then every 6h).
    def _start_safety_watcher():
        try:
            import threading

            from scripts import safety_updater  # needed both by _cycle and watch thread
            from scripts.youtube_policy_watch import check_policy_pages

            def _cycle():
                try:
                    result = safety_updater.check_and_update(force=True)
                    print("[safety-watcher] blocklist: {} — {}".format(
                        result.get("status"), result.get("message", "")))
                    watch = check_policy_pages()
                    if watch.get("status") == "changed":
                        print("[safety-watcher] YouTube policy change detected: {}".format(
                            ", ".join(watch.get("changes", []))))
                except Exception as exc:
                    print("[safety-watcher] background refresh skipped: {}".format(exc))

            thread = threading.Thread(
                target=safety_updater.watch,
                kwargs={"interval_hours": 6, "max_cycles": None},
                daemon=True,
                name="safety-watcher",
            )
            _cycle()  # immediate refresh at boot
            thread.start()
        except Exception as exc:
            print("[safety-watcher] could not start background refresh: {}".format(exc))

    if os.environ.get("VIRALCUTTER_DISABLE_SAFETY_WATCHER", "").strip().lower() not in ("1", "true", "yes", "on"):
        _start_safety_watcher()

    # Pre-flight guarantee: verify + auto-repair before the server starts.
    if args.preflight != "off" and os.environ.get("VIRALCUTTER_SKIP_PREFLIGHT", "").strip().lower() not in ("1", "true", "yes", "on"):
        try:
            from scripts import preflight
            code = preflight.run_preflight(
                mode="auto-fix" if args.preflight == "auto" else "check",
                quiet=True,
                ensure_upload=args.preflight == "auto",
            )
            if code == 1:
                print("[preflight] Critical problems remain — fix them and start again (or use --preflight off).")
                return 1
        except Exception as e:
            print("[preflight] check skipped ({}).".format(e))

    _start_telegram_control()

    if args.colab:
        print("Running in Colab mode. Generating public link with Static Mounts...")
        library.set_url_mode("fastapi")
        allowed_dirs = _allowed_dirs()
        try:
            gr.set_static_paths(paths=allowed_dirs)
        except AttributeError:
            pass
        app, local_url, share_url = demo.queue().launch(
            share=True,
            allowed_paths=allowed_dirs,
            prevent_thread_lock=True,
            **_launch_theme_kwargs
        )
        app.mount("/virals", StaticFiles(directory=VIRALS_DIR), name="virals")
        demo.block_thread()
    else:
        is_windows = (os.name == 'nt')
        library.set_url_mode("fastapi")
        allowed_dirs = _allowed_dirs()
        try:
            gr.set_static_paths(paths=allowed_dirs)
        except AttributeError:
            pass
        from fastapi import BackgroundTasks
        from fastapi.responses import FileResponse

        def attach_extra_routes(fastapi_app):
            fastapi_app.mount("/virals", StaticFiles(directory=VIRALS_DIR), name="virals")
            @fastapi_app.get("/export_xml_api")
            def export_xml_api(project: str, segment: int, background_tasks: BackgroundTasks, format: str = "premiere"):
                try:
                    # SECURITY: only serve projects inside VIRALS_DIR. Reject
                    # anything that escapes it via "../" or absolute paths.
                    project = os.path.basename((project or "").strip()) or ""
                    virals_root = os.path.abspath(VIRALS_DIR)
                    project_path = os.path.abspath(os.path.join(virals_root, project))
                    if (not project
                            or os.path.commonpath([project_path, virals_root]) != virals_root
                            or not os.path.isdir(project_path)):
                        return {"error": "Project not found."}
                    # Run the exporter IN-PROCESS — the packaged exe has no
                    # scripts/export_xml.py on disk, so a subprocess is
                    # impossible there; in-process works in both modes.
                    try:
                        from scripts.export_xml_lib.exporter import export_pack
                    except Exception as e:
                        return {"error": "Export module unavailable in this build: {}".format(e)}
                    try:
                        export_pack(project_path, segment, format)
                    except Exception as e:
                        return {"error": "Export failed: {}".format(e)}
                    proj_name = os.path.basename(project_path)
                    zip_filename = f"export_{proj_name}_seg{segment}.zip"
                    file_path = os.path.join(project_path, zip_filename)
                    if os.path.exists(file_path):
                        return FileResponse(file_path, filename=zip_filename, media_type='application/zip')
                    return {"error": f"File generation failed. Expected: {file_path}"}
                except Exception as e:
                    return {"error": str(e)}
            print(f"Mounted /virals to {VIRALS_DIR}")

        if is_windows:
            print("Running in Windows environment (using Gradio launch for convenience).")
            app, local_url, share_url = demo.queue().launch(
                share=False,
                allowed_paths=allowed_dirs,
                inbrowser=True,
                server_name=_resolve_webui_host(),
                server_port=7860,
                auth=_webui_auth(),
                prevent_thread_lock=True,
                **_launch_theme_kwargs
            )
            attach_extra_routes(app)
            demo.block_thread()
        else:
            print("Running in Linux/Container environment (using Uvicorn for stability).")
            app = FastAPI()
            _auth = _webui_auth()
            if _auth:
                import base64 as _b64

                from fastapi import Request
                from fastapi.responses import JSONResponse
                _auth_user, _auth_pass = _auth

                @app.middleware("http")
                async def _basic_auth_middleware(request: Request, call_next):
                    auth_header = request.headers.get("authorization", "")
                    ok = False
                    if auth_header.lower().startswith("basic "):
                        try:
                            decoded = _b64.b64decode(auth_header[6:]).decode("utf-8", "replace")
                            u, _, pw = decoded.partition(":")
                            ok = (u == _auth_user and pw == _auth_pass)
                        except Exception:
                            ok = False
                    if not ok:
                        return JSONResponse({"error": "unauthorized"}, status_code=401,
                                            headers={"WWW-Authenticate": 'Basic realm="OUSSAMA Cutter"'})
                    return await call_next(request)

                print("[webui] HTTP basic auth enabled (VIRALCUTTER_WEBUI_USER).")
            attach_extra_routes(app)
            if _GRADIO_MAJOR >= 6:
                # mount_gradio_app resets theme/css unless passed explicitly, and
                # builds the page config BEFORE applying them — so pass them in
                # and refresh the config afterwards.
                app = gr.mount_gradio_app(app, demo.queue(), path="/", allowed_paths=allowed_dirs,
                                          ssr_mode=False, theme=vc_theme, css=css, css_paths=[])
                demo.config = demo.get_config_file()
                demo.config["is_custom_theme"] = True
            else:
                app = gr.mount_gradio_app(app, demo.queue(), path="/", allowed_paths=allowed_dirs, ssr_mode=False)
            uvicorn.run(app,
                host=_resolve_webui_host(),
                port=7860,
                log_level="info",
            )
if __name__ == "__main__":
    _launch()
