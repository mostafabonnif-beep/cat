"""Tests for the CLI command builder (webui/pipeline.py)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webui.pipeline import WORKFLOW_MAP, build_command

MAIN = "/repo/main_improved.py"


def _flags(cmd):
    """Return dict of flag -> value for --flag value pairs, and set of bare flags."""
    pairs, bare = {}, set()
    i = 0
    while i < len(cmd):
        if cmd[i].startswith("--"):
            if i + 1 < len(cmd) and not cmd[i + 1].startswith("--"):
                pairs[cmd[i]] = cmd[i + 1]
                i += 2
            else:
                bare.add(cmd[i])
                i += 1
        else:
            i += 1
    return pairs, bare


def test_basic_command_url_source():
    cmd = build_command(MAIN, ["--url", "https://youtu.be/x"], face_model="insightface")
    assert cmd[1] == MAIN
    assert "--url" in cmd
    pairs, bare = _flags(cmd)
    assert pairs["--segments"] == "3"          # default
    assert pairs["--min-duration"] == "15"     # default
    assert pairs["--max-duration"] == "90"     # default
    assert pairs["--model"] == "large-v3-turbo"
    assert pairs["--ai-backend"] == "manual"   # default
    assert pairs["--workflow"] == "1"          # Full default
    assert pairs["--face-mode"] == "auto"
    assert pairs["--no-face-mode"] == "padding"
    assert "--skip-prompts" in bare


def test_workflow_mapping():
    assert WORKFLOW_MAP == {"Full": "1", "Cut Only": "2", "Subtitles Only": "3"}
    cmd = build_command(MAIN, [], workflow="Cut Only", face_model="mediapipe")
    pairs, _ = _flags(cmd)
    assert pairs["--workflow"] == "2"
    cmd = build_command(MAIN, [], workflow="unknown-thing", face_model="m")
    pairs, _ = _flags(cmd)
    assert pairs["--workflow"] == "1"  # falls back to Full


def test_optional_args_included_only_when_set():
    cmd = build_command(
        MAIN, ["--project-path", "/p"],
        api_key="secret", themes="funny,news", viral=True,
        translate_target="English", chunk_size="5000",
        face_model="insightface",
    )
    pairs, bare = _flags(cmd)
    # SECURITY: the API key must never be placed in argv/process listings.
    # It is passed to the child process via the VIRALCUTTER_GEMINI_KEY env
    # variable instead (see webui/app.py run()).
    assert "--api-key" not in pairs and "--api-key" not in bare
    assert pairs["--themes"] == "funny,news"
    assert pairs["--translate-target"] == "English"
    assert pairs["--chunk-size"] == "5000"
    assert "--viral" in bare

    cmd2 = build_command(MAIN, [], face_model="m")
    assert "--api-key" not in cmd2
    assert "--themes" not in cmd2
    assert "--translate-target" not in cmd2
    assert "--chunk-size" not in cmd2
    assert "--viral" not in cmd2


def test_translate_target_none_is_skipped():
    cmd = build_command(MAIN, [], translate_target="None", face_model="m")
    assert "--translate-target" not in cmd


def test_watermark_flags_follow_polish():
    cmd = build_command(
        MAIN, [], face_model="m", polish=True, logo="/tmp/logo.png",
        watermark_position="top-left", watermark_size=0.20, watermark_opacity=0.65,
    )
    pairs, _ = _flags(cmd)
    assert pairs["--watermark-position"] == "top-left"
    assert pairs["--watermark-size"] == "0.2"
    assert pairs["--watermark-opacity"] == "0.65"
    cmd2 = build_command(MAIN, [], face_model="m", polish=False,
                         watermark_position="top-left", watermark_size=0.20,
                         watermark_opacity=0.65)
    assert "--watermark-position" not in cmd2


def test_active_speaker_group():
    cmd = build_command(
        MAIN, [], face_model="m",
        focus_active_speaker=True, active_speaker_mar=0.03,
        active_speaker_score_diff=1.5, include_motion=True,
        active_speaker_motion_threshold=3.0,
        active_speaker_motion_sensitivity=0.05, active_speaker_decay=2.0,
    )
    pairs, bare = _flags(cmd)
    assert "--focus-active-speaker" in bare
    assert "--include-motion" in bare
    assert pairs["--active-speaker-mar"] == "0.03"
    assert pairs["--active-speaker-decay"] == "2.0"

    # without focus, none of the group appears
    cmd2 = build_command(MAIN, [], face_model="m", include_motion=True,
                         active_speaker_mar=0.03)
    assert "--focus-active-speaker" not in cmd2
    assert "--include-motion" not in cmd2
    assert "--active-speaker-mar" not in cmd2


def test_subtitle_config_path():
    cmd = build_command(MAIN, [], face_model="m", subtitle_config_path="/tmp/sc.json")
    pairs, _ = _flags(cmd)
    assert pairs["--subtitle-config"] == "/tmp/sc.json"
    cmd2 = build_command(MAIN, [], face_model="m")
    assert "--subtitle-config" not in cmd2


def test_face_thresholds_zero_values_kept():
    """0.0 is a valid threshold and must not be dropped."""
    cmd = build_command(
        MAIN, [], face_model="m",
        face_filter_thresh=0.0, face_two_thresh=0.0,
        face_conf_thresh=0.0, face_dead_zone=0,
    )
    pairs, _ = _flags(cmd)
    assert pairs["--face-filter-threshold"] == "0.0"
    assert pairs["--face-two-threshold"] == "0.0"
    assert pairs["--face-confidence-threshold"] == "0.0"
    assert pairs["--face-dead-zone"] == "0"


def test_bad_numeric_inputs_fall_back_to_defaults():
    cmd = build_command(MAIN, [], segments="abc", min_duration=None,
                        max_duration="", chunk_size="x", face_model="m")
    pairs, _ = _flags(cmd)
    assert pairs["--segments"] == "3"
    assert pairs["--min-duration"] == "15"
    assert pairs["--max-duration"] == "90"
    assert pairs["--chunk-size"] == "70000"


class TestDownloadFriendlyErrors:
    """Friendly handling of private/age-restricted/unavailable videos (v6.2)."""

    def _import_dl(self):
        from scripts import download_video as dl
        return dl

    def test_private_video_message(self):
        dl = self._import_dl()
        msg = dl._friendly_download_error(
            "ERROR: [youtube] abc123: Private video. Sign in if you've been "
            "granted access to this video. Use --cookies-from-browser or "
            "--cookies for the authentication.")
        assert "PRIVATE" in msg
        assert "--cookies-from-browser" in msg

    def test_age_restricted_message(self):
        dl = self._import_dl()
        msg = dl._friendly_download_error(
            "ERROR: [youtube] xyz: Sign in to confirm your age")
        assert "age-restricted" in msg

    def test_unavailable_message(self):
        dl = self._import_dl()
        msg = dl._friendly_download_error(
            "ERROR: [youtube] qwe: Video unavailable. This video is not available")
        assert "unavailable" in msg

    def test_subtitle_429_returns_none(self):
        dl = self._import_dl()
        assert dl._friendly_download_error(
            "ERROR: Unable to download video subtitles (429)") is None


class TestDownloadNeverReturnsNone:
    """Regression: download() must RAISE on private videos, never return None.

    This guards against the v6.3 corruption where the main download block was
    accidentally nested inside a helper (download() returned None → crash).
    """

    def _run_private_video_sim(self, monkeypatch, tmp_path, sys_modules):
        import importlib
        import sys as _sys
        import types

        class FakeDownloadError(Exception):
            def __str__(self):
                return ("ERROR: [youtube] 2ExOHMwEDD4: Private video. Sign in if "
                        "you've been granted access to this video.")

        class FakeYDL:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, *a, **k):
                raise FakeDownloadError()

            def download(self, *a, **k):
                raise FakeDownloadError()

        fake = types.ModuleType("yt_dlp")
        fake.YoutubeDL = FakeYDL
        class _U:
            DownloadError = FakeDownloadError
        fake.utils = _U()
        _sys.modules["yt_dlp"] = fake

        from scripts import download_video as dl
        importlib.reload(dl)
        return dl

    def test_private_video_raises_auth_error(self, monkeypatch, tmp_path):
        dl = self._run_private_video_sim(monkeypatch, tmp_path, sys.modules)
        with pytest.raises(dl.AuthNeededError):
            dl.download("https://youtube.com/watch?v=2ExOHMwEDD4",
                        base_root=str(tmp_path), download_subs=False)

    def test_invalid_url_raises(self, monkeypatch, tmp_path):
        self._run_private_video_sim(monkeypatch, tmp_path, sys.modules)
        # different message → SystemExit instead of AuthNeededError
        class FakeBadURL(Exception):
            def __str__(self):
                return "is not a valid URL"

        class BadYDL:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, *a, **k):
                raise FakeBadURL()

            def download(self, *a, **k):
                raise FakeBadURL()

        import sys as _sys
        import types
        fake = types.ModuleType("yt_dlp")
        fake.YoutubeDL = BadYDL
        class _U:
            DownloadError = FakeBadURL
        fake.utils = _U()
        _sys.modules["yt_dlp"] = fake
        import importlib

        from scripts import download_video as dl2
        importlib.reload(dl2)
        with pytest.raises(SystemExit):
            dl2.download("not-a-url", base_root=str(tmp_path), download_subs=False)


class TestGeminiDualSDK:
    """create_viral_segments must work with EITHER Gemini SDK (v6.4)."""

    @staticmethod
    def _purge_sdk_modules():
        """Remove ONLY the Gemini SDK module trees from sys.modules.

        Hermetic helper: makes the no-SDK / fake-SDK simulations below work
        even when the real SDKs are already imported (full installs). Only
        google.generativeai.* and google.genai.* are dropped — NEVER
        google.protobuf / google.api_core / google._upb, whose live C
        extensions would segfault the interpreter if unloaded from sys.modules.
        """
        import sys as _sys
        for mod in [m for m in list(_sys.modules)
                    if m == "google.generativeai" or m.startswith("google.generativeai.")
                    or m == "google.genai" or m.startswith("google.genai.")]:
            del _sys.modules[mod]

    def test_import_error_message_is_actionable(self):
        import builtins
        import importlib

        # Hermetic: drop any cached Gemini SDK modules first so the no-SDK
        # simulation below is effective even in full-install environments.
        self._purge_sdk_modules()

        real_import = builtins.__import__

        def blocked(name, *a, **k):
            if name == "google" or name.startswith("google."):
                raise ImportError("blocked for test")
            return real_import(name, *a, **k)

        builtins.__import__ = blocked
        try:
            import scripts.create_viral_segments as cvs
            importlib.reload(cvs)
            assert cvs.HAS_GEMINI is False
            try:
                cvs.call_gemini("prompt", "key")
                raise AssertionError("should raise")
            except ImportError as e:
                assert "google-generativeai" in str(e) or "google-genai" in str(e)
            # reload keeps the no-SDK state — the message must stay actionable
            importlib.reload(cvs)
            try:
                cvs.call_gemini("prompt", "key")
                raise AssertionError("should raise")
            except ImportError as e:
                assert "google-generativeai" in str(e) or "google-genai" in str(e)
        finally:
            builtins.__import__ = real_import
            self._purge_sdk_modules()
            importlib.reload(__import__("scripts.create_viral_segments"))

    def test_legacy_sdk_path_used(self):
        import sys as _sys
        import types

        # Hermetic: drop any cached real SDKs first (full-install envs).
        self._purge_sdk_modules()
        # Force the legacy branch even when google-genai is installed.
        _sys.modules["google.genai"] = None

        class FakeGenAI:
            @staticmethod
            def configure(**k):
                pass

            class GenerativeModel:
                def __init__(self, name):
                    self.name = name

                def generate_content(self, prompt):
                    return types.SimpleNamespace(text='{"segments": []}')

        fake = types.ModuleType("google.generativeai")
        fake.configure = FakeGenAI.configure
        fake.GenerativeModel = FakeGenAI.GenerativeModel
        _sys.modules["google.generativeai"] = fake
        import importlib

        import scripts.create_viral_segments as cvs
        importlib.reload(cvs)
        assert cvs.HAS_GEMINI is True
        assert cvs.GEMINI_SDK == "legacy"
        out = cvs.call_gemini("prompt", "key", model_name="m1")
        assert out == '{"segments": []}'
        del _sys.modules["google.generativeai"]
        _sys.modules.pop("google.genai", None)
        importlib.reload(cvs)

    def test_new_sdk_path_used(self):
        import importlib
        import sys as _sys
        import types

        self._purge_sdk_modules()
        class FakeModels:
            @staticmethod
            def generate_content(**kwargs):
                return types.SimpleNamespace(text='{"segments": []}')
        class FakeClient:
            def __init__(self, api_key):
                self.models = FakeModels()
        fake = types.ModuleType("google.genai")
        fake.Client = FakeClient
        _sys.modules["google.genai"] = fake
        import scripts.create_viral_segments as cvs
        importlib.reload(cvs)
        assert cvs.GEMINI_SDK == "new"
        assert cvs.call_gemini("prompt", "key", model_name="m1") == '{"segments": []}'
        _sys.modules.pop("google.genai", None)
        importlib.reload(cvs)


class TestSanitizeArabicTitle:
    """Arabic YouTube titles must produce real folder names (v6.5)."""

    def test_arabic_title_preserved(self):
        from scripts.download_video import sanitize_filename
        assert sanitize_filename("كيف تصنع فيديو فيروسي 😱") == "كيف تصنع فيديو فيروسي"

    def test_reserved_chars_stripped(self):
        from scripts.download_video import sanitize_filename
        assert ":" not in sanitize_filename("a:b")
        assert "/" not in sanitize_filename("a/b")

    def test_emoji_stripped_latin_kept(self):
        from scripts.download_video import sanitize_filename
        out = sanitize_filename("Viral Video! (Part 1) 😱🎬")
        assert "😱" not in out and "Viral Video" in out

    def test_empty_falls_back(self):
        from scripts.download_video import sanitize_filename
        assert sanitize_filename("") == "Unknown_Video"


class TestBrokenWhisperxResilience:
    """A broken optional stack (e.g. tokenizers/transformers conflict) must not
    kill the WebUI or the pipeline — whisperx import is guarded (v6.7)."""

    def test_transcribe_video_imports_with_broken_whisperx(self):
        import builtins
        import importlib
        real_import = builtins.__import__

        def blocked(name, *a, **k):
            if name == "whisperx":
                raise ImportError("tokenizers>=0.22.0,<=0.23.0 is required ...")
            return real_import(name, *a, **k)

        builtins.__import__ = blocked
        try:
            import scripts.transcribe_video as tv
            importlib.reload(tv)
            assert tv.whisperx is None
        finally:
            builtins.__import__ = real_import
            importlib.reload(__import__("scripts.transcribe_video"))

    def test_placeholder_path_still_guarded(self):
        # without the env flag it must raise a clear error, not crash
        import os

        import scripts.transcribe_video as tv
        os.environ.pop("VIRALCUTTER_ALLOW_PLACEHOLDER", None)
        try:
            tv.transcribe("/nope.mp4", "large-v3", project_folder="/tmp/x")
            raise AssertionError("should raise")
        except ImportError as e:
            assert "requirements-transcribe" in str(e) or "torch" in str(e) or "WhisperX" in str(e)


class TestMusicFlags:
    def test_music_flags_appended(self):
        cmd = build_command(MAIN, ["--url", "x"], music_check="on", music_gate="block")
        assert "--music-check" in cmd and "on" in cmd
        assert "--music-gate" in cmd and "block" in cmd

    def test_music_defaults_omitted(self):
        cmd = build_command(MAIN, ["--url", "x"], music_check="auto", music_gate="warn")
        assert "--music-check" not in cmd
        assert "--music-gate" not in cmd


class TestFrozenExeCommands:
    def test_frozen_skips_script_path(self, monkeypatch):
        """Packaged exe re-invokes itself: no main_improved.py path on disk."""
        from webui import runtime as rt
        monkeypatch.setattr(rt.sys, "frozen", True, raising=False)
        cmd = build_command(MAIN, ["--url", "https://youtu.be/x"])
        # [exe, --url, ...] — the script path must NOT appear
        assert cmd[0] == sys.executable
        assert cmd[1] == "--url"
        assert MAIN not in cmd

    def test_source_keeps_script_path(self, monkeypatch):
        from webui import runtime as rt
        monkeypatch.setattr(rt.sys, "frozen", False, raising=False)
        cmd = build_command(MAIN, ["--url", "https://youtu.be/x"])
        assert cmd[1] == MAIN


class TestOutputAspectFlags:
    """v6.13 reframe flags must be appended only when explicitly chosen."""

    def test_aspect_9_16_default_not_passed(self):
        from webui.pipeline import build_command
        cmd = build_command(MAIN, ["--url", "x"], output_aspect="9:16")
        assert "--output-aspect" not in cmd

    def test_aspect_4_5_passed(self):
        from webui.pipeline import build_command
        cmd = build_command(MAIN, ["--url", "x"], output_aspect="4:5")
        assert cmd[cmd.index("--output-aspect") + 1] == "4:5"
        assert "--reframe-mode" not in cmd  # only when chosen

    def test_aspect_with_mode(self):
        from webui.pipeline import build_command
        cmd = build_command(MAIN, ["--url", "x"], output_aspect="16:9", reframe_mode="pad")
        assert cmd[cmd.index("--output-aspect") + 1] == "16:9"
        assert cmd[cmd.index("--reframe-mode") + 1] == "pad"

    def test_none_passed(self):
        from webui.pipeline import build_command
        cmd = build_command(MAIN, ["--url", "x"])
        assert "--output-aspect" not in cmd


class TestForceNewSegmentsFlag:
    """v6.16: WebUI checkbox → --force-new-segments CLI flag."""

    def test_flag_not_passed_by_default(self):
        from webui.pipeline import build_command
        cmd = build_command(MAIN, ["--url", "x"])
        assert "--force-new-segments" not in cmd

    def test_flag_passed_when_enabled(self):
        from webui.pipeline import build_command
        cmd = build_command(MAIN, ["--url", "x"], force_new_segments=True)
        assert "--force-new-segments" in cmd


def test_clean_json_response_handles_code_fences_and_prefix():
    from scripts.create_viral_segments import clean_json_response

    response = "Here is the result:\n```JSON\n{\"segments\": [{\"start_time\": 1, \"end_time\": 5}]}\n```"
    parsed = clean_json_response(response)
    assert len(parsed["segments"]) == 1
    assert parsed["segments"][0]["start_time"] == 1
