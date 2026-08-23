"""Tests for webui/settings_store.py — persistent AI settings (v6.9).

The core promise: a Gemini key saved once is remembered — the user never
retypes it. These tests pin that behavior plus masking, atomic writes,
and connection-test guards.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webui import settings_store


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Tests must not be polluted by a real GEMINI_API_KEY in the env."""
    for var in ("GEMINI_API_KEY", "VIRALCUTTER_GEMINI_KEY",
                "VIRALCUTTER_CONFIG_PASSPHRASE"):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# mask_key / looks_like_gemini_key
# ---------------------------------------------------------------------------

def test_mask_key_long():
    key = "AIzaSyD4iE8fG0hI1jK2lM3nO4pQ5rS6tU7vW8x"
    masked = settings_store.mask_key(key)
    assert masked.startswith("AIza")
    assert masked.endswith(key[-4:])
    assert key not in masked  # never expose the full key


def test_mask_key_short():
    masked = settings_store.mask_key("AIza123")
    assert masked.startswith("AIza")
    assert "****" in masked


def test_mask_key_empty():
    assert settings_store.mask_key("") == ""
    assert settings_store.mask_key(None) == ""


@pytest.mark.parametrize("key,expected", [
    ("AIzaSyD4iE8fG0hI1jK2lM3nO4pQ5rS6tU7vW8x", True),
    ("AIza", True),
    ("sk-proj-abc123", False),
    ("", False),
    (None, False),
])
def test_looks_like_gemini_key(key, expected):
    assert settings_store.looks_like_gemini_key(key) is expected


# ---------------------------------------------------------------------------
# save + load round-trip
# ---------------------------------------------------------------------------

def test_save_then_load_roundtrip(tmp_path):
    ok, err = settings_store.save_ui_settings(
        ai_backend="gemini",
        api_key="AIzaSyD4iE8fG0hI1jK2lM3nO4pQ5rS6tU7vW8x",
        ai_model="gemini-2.5-flash",
        chunk_size=50000,
        base_dir=str(tmp_path),
    )
    assert ok, err
    assert (tmp_path / "api_config.json").exists()

    loaded = settings_store.load_ui_settings(base_dir=str(tmp_path))
    assert loaded["ai_backend"] == "gemini"
    assert loaded["api_key"] == "AIzaSyD4iE8fG0hI1jK2lM3nO4pQ5rS6tU7vW8x"
    assert loaded["key_source"] == settings_store.KEY_SOURCE_FILE
    assert loaded["ai_model"] == "gemini-2.5-flash"
    assert loaded["chunk_size"] == 50000


def test_save_preserves_unrelated_fields(tmp_path):
    config = {
        "selected_api": "gemini",
        "gemini": {"api_key": "", "model": "m-old", "chunk_size": 20000},
        "g4f": {"model": "gpt-4o-mini", "chunk_size": 2000},
        "custom_future_field": {"keep": "me"},
    }
    with open(tmp_path / "api_config.json", "w", encoding="utf-8") as f:
        json.dump(config, f)

    ok, _ = settings_store.save_ui_settings(
        ai_backend="gemini", api_key="AIza_new_key_here_123",
        ai_model="m-new", chunk_size=99999, base_dir=str(tmp_path))
    assert ok

    with open(tmp_path / "api_config.json", encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["custom_future_field"] == {"keep": "me"}
    assert saved["g4f"]["model"] == "gpt-4o-mini"
    assert saved["gemini"]["model"] == "m-new"
    assert saved["gemini"]["api_key"] == "AIza_new_key_here_123"


def test_save_empty_key_keeps_existing(tmp_path):
    """Clearing the box mid-edit must NOT erase the stored key."""
    settings_store.save_ui_settings(
        ai_backend="gemini", api_key="AIza_keep_me_please_12345",
        ai_model="m1", chunk_size=1, base_dir=str(tmp_path))
    settings_store.save_ui_settings(
        ai_backend="gemini", api_key="", ai_model="m2",
        chunk_size=2, base_dir=str(tmp_path))
    loaded = settings_store.load_ui_settings(base_dir=str(tmp_path))
    assert loaded["api_key"] == "AIza_keep_me_please_12345"
    assert loaded["ai_model"] == "m2"


def test_save_does_not_persist_env_key(tmp_path, monkeypatch):
    """The env var stays the source of truth; never copy it into the file."""
    monkeypatch.setenv("GEMINI_API_KEY", "AIza_env_key_0123456789")
    ok, _ = settings_store.save_ui_settings(
        ai_backend="gemini", api_key="AIza_env_key_0123456789",
        ai_model="m", chunk_size=1, base_dir=str(tmp_path))
    assert ok
    with open(tmp_path / "api_config.json", encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["gemini"].get("api_key", "") != "AIza_env_key_0123456789"


def test_env_key_wins_over_file(tmp_path, monkeypatch):
    settings_store.save_ui_settings(
        ai_backend="gemini", api_key="AIza_file_key_0000000001",
        ai_model="m", chunk_size=1, base_dir=str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "AIza_env_key_0000000002")
    loaded = settings_store.load_ui_settings(base_dir=str(tmp_path))
    assert loaded["api_key"] == "AIza_env_key_0000000002"
    assert loaded["key_source"] == settings_store.KEY_SOURCE_ENV


def test_load_defaults_when_no_config(tmp_path):
    loaded = settings_store.load_ui_settings(base_dir=str(tmp_path))
    assert loaded["ai_backend"] == "gemini"
    assert loaded["api_key"] == ""
    assert loaded["key_source"] == settings_store.KEY_SOURCE_NONE
    assert isinstance(loaded["chunk_size"], int)


def test_save_atomic_no_tmp_left(tmp_path):
    settings_store.save_ui_settings(
        ai_backend="gemini", api_key="AIza_atomic_000000000000",
        ai_model="m", chunk_size=1, base_dir=str(tmp_path))
    assert not (tmp_path / "api_config.json.tmp").exists()


def test_backend_switch_remembered(tmp_path):
    settings_store.save_ui_settings(
        ai_backend="g4f", api_key="AIza_still_there_0000000",
        ai_model="gpt-4o", chunk_size=3000, base_dir=str(tmp_path))
    loaded = settings_store.load_ui_settings(base_dir=str(tmp_path))
    assert loaded["ai_backend"] == "g4f"
    # gemini key preserved for when the user switches back
    with open(tmp_path / "api_config.json", encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["gemini"]["api_key"] == "AIza_still_there_0000000"
    assert saved["g4f"]["model"] == "gpt-4o"


# ---------------------------------------------------------------------------
# test_gemini_connection guards (no network in unit tests)
# ---------------------------------------------------------------------------

def test_connection_empty_key_fails_fast():
    ok, msg = settings_store.test_gemini_connection("")
    assert ok is False
    assert "empty" in msg.lower()


def test_connection_rest_invalid_key(monkeypatch):
    """REST fallback maps HTTP 400 'API key not valid' to (False, detail)."""
    import io
    import urllib.error

    def fake_urlopen(req, timeout=10):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {},
            io.BytesIO(b'{"error": {"message": "API key not valid"}}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    ok, msg = settings_store._rest_test_gemini("AIza_bad_key")
    assert ok is False
    assert "400" in msg


def test_connection_rest_ok(monkeypatch):

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"models": [{"name": "m1"}, {"name": "m2"}]}'

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=10: FakeResp())
    ok, msg = settings_store._rest_test_gemini("AIza_good_key")
    assert ok is True
    assert "2" in msg


# ---------------------------------------------------------------------------
# create_viral_segments.call_gemini — loud failure on bad keys (v6.9)
# ---------------------------------------------------------------------------

def test_call_gemini_raises_on_invalid_key(monkeypatch):
    import importlib
    import types

    class FakeGenAI:
        @staticmethod
        def configure(**k):
            pass

        class GenerativeModel:
            def __init__(self, name):
                pass

            def generate_content(self, prompt):
                raise Exception("400 API key not valid. Please pass a valid API key.")

    fake = types.ModuleType("google.generativeai")
    fake.configure = FakeGenAI.configure
    fake.GenerativeModel = FakeGenAI.GenerativeModel
    monkeypatch.setitem(sys.modules, "google.generativeai", fake)
    monkeypatch.setitem(sys.modules, "google.genai", None)

    import scripts.create_viral_segments as cvs
    importlib.reload(cvs)
    try:
        assert cvs.HAS_GEMINI is True
        with pytest.raises(RuntimeError, match="API key not valid"):
            cvs.call_gemini("prompt", "bad-key", model_name="m1")
    finally:
        monkeypatch.undo()
        importlib.reload(cvs)


def test_call_gemini_non_key_error_returns_empty(monkeypatch):
    """Transient errors keep the old contract: return "{}" after logging."""
    import importlib
    import types

    class FakeGenAI:
        @staticmethod
        def configure(**k):
            pass

        class GenerativeModel:
            def __init__(self, name):
                pass

            def generate_content(self, prompt):
                raise Exception("some random transient failure")

    fake = types.ModuleType("google.generativeai")
    fake.configure = FakeGenAI.configure
    fake.GenerativeModel = FakeGenAI.GenerativeModel
    monkeypatch.setitem(sys.modules, "google.generativeai", fake)
    monkeypatch.setitem(sys.modules, "google.genai", None)

    import scripts.create_viral_segments as cvs
    importlib.reload(cvs)
    try:
        assert cvs.call_gemini("prompt", "key", model_name="m1") == "{}"
    finally:
        monkeypatch.undo()
        importlib.reload(cvs)


# ---------------------------------------------------------------------------
# utils.summarize_error — new Gemini hints (v6.9)
# ---------------------------------------------------------------------------

def test_summarize_error_invalid_key_hint():
    from webui.utils import summarize_error
    title, detail, hint = summarize_error(
        "RuntimeError: Gemini API key error (API key not valid): check the key")
    assert "aistudio.google.com" in hint


def test_summarize_error_quota_hint():
    from webui.utils import summarize_error
    _, _, hint = summarize_error("google.api_core.exceptions.ResourceExhausted: 429 Quota exceeded")
    assert "Gemini" in hint


def test_summarize_error_permission_hint():
    from webui.utils import summarize_error
    _, _, hint = summarize_error("403 PERMISSION_DENIED: Generative Language API has not been used")
    assert "Gemini" in hint


# ---------------------------------------------------------------------------
# v6.9.2 — full WebUI preferences persistence
# ---------------------------------------------------------------------------

def test_webui_prefs_roundtrip(tmp_path):
    ok, err = settings_store.save_webui_prefs(
        {"platform": "yt_shorts", "polish": True, "safety_mode": "block",
         "video_quality": "1080p", "translate_target": "None"},
        base_dir=str(tmp_path))
    assert ok, err
    loaded = settings_store.load_webui_prefs(base_dir=str(tmp_path))
    assert loaded["platform"] == "yt_shorts"
    assert loaded["polish"] is True
    assert loaded["safety_mode"] == "block"


def test_webui_prefs_skips_none(tmp_path):
    ok, _ = settings_store.save_webui_prefs(
        {"platform": None, "music": None, "logo": ""}, base_dir=str(tmp_path))
    assert ok
    loaded = settings_store.load_webui_prefs(base_dir=str(tmp_path))
    assert "platform" not in loaded
    assert "music" not in loaded
    assert loaded.get("logo") == ""


def test_webui_prefs_corrupt_file_returns_empty(tmp_path):
    (tmp_path / settings_store.WEBUI_PREFS_FILE).write_text("{not json", encoding="utf-8")
    assert settings_store.load_webui_prefs(base_dir=str(tmp_path)) == {}


def test_webui_prefs_missing_file_returns_empty(tmp_path):
    assert settings_store.load_webui_prefs(base_dir=str(tmp_path)) == {}


def test_load_corrupt_shape_and_invalid_backend_are_safe(tmp_path):
    (tmp_path / "api_config.json").write_text(
        json.dumps({"selected_api": "unknown", "gemini": [], "g4f": {"chunk_size": "bad"}}),
        encoding="utf-8",
    )
    loaded = settings_store.load_ui_settings(base_dir=str(tmp_path))
    assert loaded["ai_backend"] == "gemini"
    assert loaded["chunk_size"] == 20000


def test_save_clamps_chunk_size(tmp_path):
    ok, error = settings_store.save_ui_settings(
        ai_backend="gemini", chunk_size=999999999, base_dir=str(tmp_path)
    )
    assert ok, error
    loaded = settings_store.load_ui_settings(base_dir=str(tmp_path))
    assert loaded["chunk_size"] == settings_store.MAX_CHUNK_SIZE
