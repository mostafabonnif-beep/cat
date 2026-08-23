# -*- coding: utf-8 -*-
"""Tests for reliability modules: checkpoint, secure_config, oom_guard, auto_updater."""

import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import auto_updater, checkpoint, oom_guard, secure_config

# ---------------------------------------------------------------------------
# checkpoint
# ---------------------------------------------------------------------------

class TestCheckpoint:
    def test_run_marks_done(self, tmp_path):
        calls = []
        with checkpoint.StageTracker(str(tmp_path)) as st:
            st.run("cut", lambda: calls.append("x") or "result")
        assert calls == ["x"]
        assert checkpoint.is_done(str(tmp_path), "cut")

    def test_second_run_skips(self, tmp_path):
        calls = []
        with checkpoint.StageTracker(str(tmp_path)) as st:
            st.run("cut", lambda: calls.append(1))
            st.run("cut", lambda: calls.append(2))
        assert calls == [1]  # second call skipped

    def test_force_reruns(self, tmp_path):
        calls = []
        with checkpoint.StageTracker(str(tmp_path)) as st:
            st.run("cut", lambda: calls.append(1))
            st.run("cut", lambda: calls.append(2), force=True)
        assert calls == [1, 2]

    def test_failed_stage_not_marked(self, tmp_path):
        with pytest.raises(ValueError):
            with checkpoint.StageTracker(str(tmp_path)) as st:
                st.run("cut", lambda: (_ for _ in ()).throw(ValueError("boom")))
        assert not checkpoint.is_done(str(tmp_path), "cut")

    def test_clear_and_pending(self, tmp_path):
        checkpoint.mark_done(str(tmp_path), "cut")
        checkpoint.mark_done(str(tmp_path), "edit")
        assert "cut" not in checkpoint.list_pending(str(tmp_path))
        checkpoint.clear(str(tmp_path), "edit")
        assert "edit" in checkpoint.list_pending(str(tmp_path))

    def test_disabled_tracker_runs_everything(self, tmp_path):
        calls = []
        with checkpoint.StageTracker(str(tmp_path), enabled=False) as st:
            st.run("cut", lambda: calls.append(1))
            st.run("cut", lambda: calls.append(2))
        assert calls == [1, 2]

    def test_corrupt_checkpoint_is_safe(self, tmp_path):
        (tmp_path / checkpoint.CHECKPOINT_FILENAME).write_text("{not json", encoding="utf-8")
        assert checkpoint.is_done(str(tmp_path), "cut") is False


# ---------------------------------------------------------------------------
# secure_config
# ---------------------------------------------------------------------------

class TestSecureConfig:
    def test_set_get_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(secure_config, "_base_dir", lambda: str(tmp_path))
        path = secure_config.set_key("sk-test-123", passphrase="hunter2")
        assert os.path.exists(path)
        assert secure_config.get_key(passphrase="hunter2") == "sk-test-123"

    def test_wrong_passphrase_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(secure_config, "_base_dir", lambda: str(tmp_path))
        secure_config.set_key("sk-test-123", passphrase="right")
        assert secure_config.get_key(passphrase="wrong") is None

    def test_env_var_takes_priority(self, tmp_path, monkeypatch):
        monkeypatch.setattr(secure_config, "_base_dir", lambda: str(tmp_path))
        secure_config.set_key("sk-file", passphrase="p")
        monkeypatch.setenv("GEMINI_API_KEY", "sk-env")
        assert secure_config.resolve_api_key() == "sk-env"

    def test_legacy_plaintext_warns(self, tmp_path, monkeypatch):
        monkeypatch.setattr(secure_config, "_base_dir", lambda: str(tmp_path))
        with open(secure_config.legacy_config_path(), "w", encoding="utf-8") as f:
            json.dump({"gemini": {"api_key": "sk-legacy"}}, f)
        assert secure_config.resolve_api_key(warn=False) == "sk-legacy"

    def test_load_api_config_injects_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr(secure_config, "_base_dir", lambda: str(tmp_path))
        monkeypatch.setenv("VIRALCUTTER_CONFIG_PASSPHRASE", "p")
        secure_config.set_key("sk-injected", passphrase="p")
        config = secure_config.load_api_config()
        assert config["gemini"]["api_key"] == "sk-injected"

    def test_no_passphrase_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(secure_config, "_base_dir", lambda: str(tmp_path))
        with pytest.raises(ValueError):
            secure_config.set_key("sk", passphrase="")


# ---------------------------------------------------------------------------
# oom_guard
# ---------------------------------------------------------------------------

class _FakeOOM(Exception):
    pass


class TestOomGuard:
    def test_retries_smaller_on_oom(self, tmp_path):
        used = []

        def fake_transcribe(_in, model, project_folder=None):
            used.append(model)
            if model == "large-v3-turbo":
                raise _FakeOOM("CUDA out of memory")
            return ("x.srt", "x.tsv")

        result = oom_guard.transcribe_with_fallback(
            "in.mp4", "large-v3-turbo", str(tmp_path),
            transcribe_fn=fake_transcribe, verbose=False)
        assert result == ("x.srt", "x.tsv")
        assert used == ["large-v3-turbo", "medium"]

    def test_non_oom_error_propagates(self, tmp_path):
        def fake_transcribe(_in, model, project_folder=None):
            raise ValueError("transcript empty")

        with pytest.raises(ValueError):
            oom_guard.transcribe_with_fallback(
                "in.mp4", "large", str(tmp_path),
                transcribe_fn=fake_transcribe, verbose=False)

    def test_success_first_try(self, tmp_path):
        used = []

        def fake_transcribe(_in, model, project_folder=None):
            used.append(model)
            return ("a", "b")

        oom_guard.transcribe_with_fallback(
            "in.mp4", "small", str(tmp_path),
            transcribe_fn=fake_transcribe, verbose=False)
        assert used == ["small"]

    def test_chain_exhausted_raises(self, tmp_path):
        def fake_transcribe(_in, model, project_folder=None):
            raise _FakeOOM("CUDA out of memory")

        with pytest.raises(_FakeOOM):
            oom_guard.transcribe_with_fallback(
                "in.mp4", "tiny", str(tmp_path),
                transcribe_fn=fake_transcribe, verbose=False)


# ---------------------------------------------------------------------------
# auto_updater
# ---------------------------------------------------------------------------

class TestAutoUpdater:
    def test_no_update_when_versions_equal(self):
        info = auto_updater.check_for_update(
            current_version="0.9.0", timeout=1,
            urlopen=lambda t: json.dumps({
                "tag_name": "v0.9.0",
                "assets": [{"name": "ViralCutter.exe",
                            "browser_download_url": "https://x/ViralCutter.exe"}],
                "body": "release notes",
            }).encode())
        assert info["update_available"] is False
        assert info["latest_version"] == "v0.9.0"

    def test_update_available_when_remote_newer(self):
        # Pick an asset name that matches this platform, mirroring the
        # updater's platform matching (exe on Windows, bin/appimage on POSIX).
        asset_name = "ViralCutter.exe" if os.name == "nt" else "ViralCutter-linux"
        info = auto_updater.check_for_update(
            current_version="0.8.0", timeout=1,
            urlopen=lambda t: json.dumps({
                "tag_name": "v1.0.0",
                "assets": [{"name": asset_name,
                            "browser_download_url": "https://x/" + asset_name}],
            }).encode())
        assert info["update_available"] is True
        assert info["download_url"] == "https://x/" + asset_name

    def test_foreign_platform_asset_is_not_picked(self):
        # SECURITY: never fall back to an asset for another platform.
        # ".dmg" matches no platform pattern, so download_url must stay None
        # on every OS.
        info = auto_updater.check_for_update(
            current_version="0.8.0", timeout=1,
            urlopen=lambda t: json.dumps({
                "tag_name": "v1.0.0",
                "assets": [{"name": "ViralCutter.dmg",
                            "browser_download_url": "https://x/ViralCutter.dmg"}],
            }).encode())
        assert info["update_available"] is True
        assert info["download_url"] is None

    def test_offline_is_safe(self):
        info = auto_updater.check_for_update(timeout=1,
                                             urlopen=lambda t: (_ for _ in ()).throw(
                                                 RuntimeError("network down")))
        assert info["update_available"] is False
        assert info["error"] is not None

    def test_parse_version(self):
        assert auto_updater._parse_version("v0.9.0") == (0, 9, 0)
        assert auto_updater._parse_version("1.2") == (1, 2, 0)
        assert auto_updater._parse_version("garbage") == (0, 0, 0)

    def test_download_update(self, tmp_path, monkeypatch):
        chunk = b"BINARY"
        payload = chunk * 2  # the FakeResp below serves `chunk` twice

        class FakeResp:
            def __init__(self):
                self._left = 2

            def read(self, n):
                if self._left > 0:
                    self._left -= 1
                    return chunk
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(auto_updater.urllib.request, "urlopen",
                            lambda *a, **k: FakeResp())
        dest = tmp_path / "upd"
        expected = hashlib.sha256(payload).hexdigest()
        path = auto_updater.download_update("https://x/ViralCutter.exe",
                                            dest_dir=str(dest),
                                            expected_sha256=expected)
        assert os.path.exists(path)
        with open(path, "rb") as f:
            assert f.read() == payload
        info = auto_updater.update_info(dest_dir=str(dest))
        assert info[1] == "ViralCutter.exe"

    def test_download_update_refused_without_checksum(self, tmp_path, monkeypatch):
        # SECURITY: fail closed — no checksum manifest, no install.
        class FakeResp:
            def __init__(self):
                self._left = 1

            def read(self, n):
                if self._left > 0:
                    self._left -= 1
                    return b"BINARY"
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(auto_updater.urllib.request, "urlopen",
                            lambda *a, **k: FakeResp())
        dest = tmp_path / "upd"
        with pytest.raises(RuntimeError, match="no checksum manifest"):
            auto_updater.download_update("https://x/ViralCutter.exe",
                                         dest_dir=str(dest))
        # the partial download must not linger as a valid update
        assert not os.path.exists(os.path.join(str(dest), "ViralCutter.exe"))

    def test_download_update_checksum_mismatch(self, tmp_path, monkeypatch):
        class FakeResp:
            def __init__(self):
                self._left = 1

            def read(self, n):
                if self._left > 0:
                    self._left -= 1
                    return b"BINARY"
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(auto_updater.urllib.request, "urlopen",
                            lambda *a, **k: FakeResp())
        dest = tmp_path / "upd"
        with pytest.raises(RuntimeError, match="checksum mismatch"):
            auto_updater.download_update("https://x/ViralCutter.exe",
                                         dest_dir=str(dest),
                                         expected_sha256="0" * 64)
        assert not os.path.exists(os.path.join(str(dest), "ViralCutter.exe"))


class TestAutoUpdaterTagsFallback:
    def test_falls_back_to_tags_when_no_releases(self, monkeypatch):
        calls = {}

        def fake_github(path, timeout=8):
            calls["path"] = path
            if "releases" in path:
                raise RuntimeError("Not Found (no release)")
            return [{"name": "v0.9.1"}]

        monkeypatch.setattr(auto_updater, "_github_api", fake_github)
        info = auto_updater.check_for_update(current_version="0.9.0", timeout=1)
        assert info["update_available"] is True
        assert info["latest_version"] == "v0.9.1"
        assert "tags" in calls["path"]

    def test_tag_same_version_no_update(self, monkeypatch):
        def fake_github(path, timeout=8):
            if "releases" in path:
                raise RuntimeError("no release")
            return [{"name": "v0.9.0"}]

        monkeypatch.setattr(auto_updater, "_github_api", fake_github)
        info = auto_updater.check_for_update(current_version="0.9.0", timeout=1)
        assert info["update_available"] is False


class TestWhisperModelFallback:
    """large-v3-turbo not supported by older faster-whisper → graceful fallback."""

    def test_turbo_candidates(self):
        from scripts.transcribe_video import resolve_model_candidates
        assert resolve_model_candidates("large-v3-turbo")[:3] == ["large-v3-turbo", "large-v3", "medium"]

    def test_plain_model_gets_large_v3_fallback(self):
        from scripts.transcribe_video import resolve_model_candidates
        assert resolve_model_candidates("small")[-1] == "large-v3"
        assert resolve_model_candidates("large-v3") == ["large-v3"]

    def test_none_defaults(self):
        from scripts.transcribe_video import resolve_model_candidates
        assert resolve_model_candidates(None)[0] == "large-v3"


def test_checkpoint_rejects_unknown_stage(tmp_path):
    with pytest.raises(ValueError):
        checkpoint.mark_done(str(tmp_path), "not-a-real-stage")
    with pytest.raises(ValueError):
        checkpoint.clear(str(tmp_path), "not-a-real-stage")


# ---------------------------------------------------------------------------
# v7.5 multi-key Gemini and transcription device
# ---------------------------------------------------------------------------

def test_secure_config_multiple_keys_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(secure_config, "_base_dir", lambda: str(tmp_path))
    path = secure_config.set_keys(["k-one", "k-two", "k-three", "ignored"], passphrase="hunter2")
    assert os.path.exists(path)
    assert secure_config.get_keys(passphrase="hunter2") == ["k-one", "k-two", "k-three"]
    assert secure_config.get_key(passphrase="hunter2") == "k-one"


def test_settings_store_multiple_keys_roundtrip(tmp_path):
    from webui import settings_store
    ok, err = settings_store.save_ui_settings(
        ai_backend="gemini", api_keys=["k-one", "k-two", "k-three"],
        key_mode="2", ai_model="gemini-2.5-flash", chunk_size=20000,
        base_dir=str(tmp_path))
    assert ok, err
    loaded = settings_store.load_ui_settings(base_dir=str(tmp_path))
    assert loaded["api_keys"] == ["k-one", "k-two", "k-three"]
    assert loaded["api_key"] == "k-one"
    assert loaded["key_mode"] == "2"


def test_gemini_rotation_switches_after_invalid_key(monkeypatch):
    from scripts import create_viral_segments
    calls = []
    monkeypatch.setattr(create_viral_segments, "HAS_GEMINI", True)
    monkeypatch.setattr(create_viral_segments, "_GEMINI_KEY_CURSOR", 0)

    def fake_generate(model, prompt, api_key):
        calls.append(api_key)
        if api_key == "k-one":
            raise RuntimeError("API key not valid")
        return "ok"

    monkeypatch.setattr(create_viral_segments, "_gemini_generate", fake_generate)
    assert create_viral_segments.call_gemini("p", ["k-one", "k-two"], "model") == "ok"
    assert calls == ["k-one", "k-two"]

def test_rich_lifecycle_metadata_records_failure_and_resume(tmp_path):
    with pytest.raises(RuntimeError, match="broken"):
        with checkpoint.StageTracker(str(tmp_path)) as st:
            st.run("music_check", lambda: (_ for _ in ()).throw(RuntimeError("broken")))
    data = checkpoint.load_checkpoint(str(tmp_path))
    assert data["active_stage"] is None
    assert data["last_error"]["stage"] == "music_check"
    assert data["last_error"]["message"] == "broken"
    assert data["history"][-1]["status"] == "failed"
    assert checkpoint.list_pending(str(tmp_path))[0] == "download"


def test_rich_lifecycle_metadata_records_skip(tmp_path):
    checkpoint.mark_done(str(tmp_path), "cut")
    with checkpoint.StageTracker(str(tmp_path)) as st:
        assert st.run("cut", lambda: "must-not-run") is None
        info = st.resume_info()
    assert info["skipped"] == ["cut"]
    assert info["active_stage"] is None
