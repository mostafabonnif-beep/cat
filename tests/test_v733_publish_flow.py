# -*- coding: utf-8 -*-
"""v7.33 — publish flow: hashtags reach the uploader, SEO advisory, variants."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webui import publish_panel as pp


def _project(tmp_path, with_tags=False):
    project = tmp_path / "proj"
    (project / "final").mkdir(parents=True)
    for i in range(2):
        (project / "final" / "{:03d}_clip.mp4".format(i)).write_bytes(b"x" * 40)
    segments = [
        {"title": "First Title", "caption": "cap 1", "start_time": 0,
         "end_time": 5, "hashtags": ["viral", "story"] if with_tags else []},
        {"title": "Second Title", "caption": "cap 2", "start_time": 5,
         "end_time": 10, "hashtags": "#tips #short" if with_tags else []},
    ]
    (project / "viral_segments.txt").write_text(
        json.dumps({"segments": segments}), encoding="utf-8")
    return str(project)


class TestClipMetadata:
    def test_metadata_returns_title_caption_hashtags(self, tmp_path):
        project = _project(tmp_path, with_tags=True)
        clip = os.path.join(project, "final", "001_clip.mp4")
        meta = pp.clip_metadata(project, clip)
        assert meta["title"] == "Second Title"
        assert meta["caption"] == "cap 2"
        assert meta["hashtags"] == ["tips", "short"]

    def test_metadata_string_hashtags_normalized(self, tmp_path):
        project = _project(tmp_path, with_tags=True)
        clip = os.path.join(project, "final", "000_clip.mp4")
        meta = pp.clip_metadata(project, clip)
        assert meta["hashtags"] == ["viral", "story"]

    def test_clip_suggestion_backwards_compatible(self, tmp_path):
        project = _project(tmp_path)
        clip = os.path.join(project, "final", "000_clip.mp4")
        title, caption = pp.clip_suggestion(project, clip)
        assert (title, caption) == ("First Title", "cap 1")


class TestHashtagsFlowToUploader:
    def test_batch_passes_segment_hashtags(self, tmp_path, monkeypatch):
        project = _project(tmp_path, with_tags=True)
        clips = [os.path.join(project, "final", "{:03d}_clip.mp4".format(i))
                 for i in range(2)]
        captured = []

        def fake_stream_upload(*args, **kwargs):
            captured.append({"tags": args[5], "title": args[3], "variant": args[-1]})
            yield "ok"
            return pp._publish_result("dry_run", args[2], args[3])

        monkeypatch.setattr(pp, "stream_upload", fake_stream_upload)
        updates = list(pp.stream_upload_batch(project, "youtube", clips, True, "warn"))
        assert captured[0]["tags"] == ["viral", "story"]
        assert captured[1]["tags"] == ["tips", "short"]
        assert any("هاشتاغات المقطع" in line for line in updates)
        # variant_policy defaults off and is forwarded
        assert captured[0]["variant"] == "off"


class TestSeoAdvisory:
    def test_seo_score_helper(self):
        check = pp.seo_title_score("كيف تكسب المال من يوتيوب؟ الخطوات الكاملة")
        assert check is not None
        assert 0.0 <= check["score"] <= 100.0

    def test_seo_line_emitted_for_youtube_upload(self, tmp_path, monkeypatch):
        project = _project(tmp_path)
        monkeypatch.setattr(pp, "_audio_qc_upload_allowed", lambda *a: (True, ""))

        class FakeUploader:
            def __init__(self, *a, **k):
                pass

            def upload(self, video_path, title, caption, hashtags, index=None):
                return {"status": "uploaded", "platform": "youtube",
                        "video_id": "ABC"}

        import scripts.upload_gate as ug
        monkeypatch.setattr(ug, "UPLOADERS",
                            {"youtube": lambda *a, **k: FakeUploader(*a, **k)})
        clip = os.path.join(project, "final", "000_clip.mp4")
        lines = list(pp.stream_upload(project, "youtube", clip, "My Title",
                                      "Cap", ["#shorts"], False, "warn"))
        out = "\n".join(lines)
        assert "[seo]" in out


class TestCrossPlatformVariantWiring:
    def test_dry_run_previews_variant_without_rendering(self, tmp_path, monkeypatch):
        project = _project(tmp_path)
        clip = os.path.join(project, "final", "000_clip.mp4")
        # A prior successful YouTube publish of this same clip exists.
        history = os.path.join(project, "publish_history.jsonl")
        with open(history, "w", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "platform": "youtube", "status": "uploaded",
                "video": "000_clip.mp4", "title": "T",
                "file_fingerprint": "sha256:x"}, ensure_ascii=False) + "\n")

        decision = {}
        import scripts.platform_variant as pv
        monkeypatch.setattr(pv, "maybe_variant",
                            lambda *a, **k: decision or {"action": "none"})
        monkeypatch.setattr(pv, "plan_variant",
                            lambda *a, **k: {"action": "variate", "seed": 7,
                                             "reason": "plan", "preset": {}})
        updates = list(pp.stream_upload(project, "tiktok", clip, "T", "C", [],
                                        True, "warn", variant_policy="auto"))
        out = "\n".join(updates)
        assert "نسخة مختلفة" in out
        # dry run must never create the variants folder / render anything
        assert not os.path.exists(os.path.join(project, "variants"))

    def test_real_upload_uses_variant_file(self, tmp_path, monkeypatch):
        project = _project(tmp_path)
        monkeypatch.setattr(pp, "_audio_qc_upload_allowed", lambda *a: (True, ""))
        clip = os.path.join(project, "final", "000_clip.mp4")
        history = os.path.join(project, "publish_history.jsonl")
        with open(history, "w", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "platform": "youtube", "status": "uploaded",
                "video": "000_clip.mp4", "title": "T",
                "file_fingerprint": "sha256:x"}, ensure_ascii=False) + "\n")

        variant_path = os.path.join(project, "variants", "000_clip__tiktok_7.mp4")
        os.makedirs(os.path.dirname(variant_path), exist_ok=True)
        with open(variant_path, "wb") as stream:
            stream.write(b"variant-bytes")

        import scripts.platform_variant as pv
        monkeypatch.setattr(pv, "maybe_variant",
                            lambda *a, **k: {"action": "variate", "path": variant_path,
                                             "seed": 7, "transforms": ["mirror"],
                                             "reason": "r"})
        captured = {}

        class FakeUploader:
            def __init__(self, project_folder, dry_run=False, music_gate=None):
                pass

            def upload(self, video_path, title, caption, hashtags, index=None):
                captured["video"] = video_path
                captured["tags"] = hashtags
                return {"status": "uploaded", "platform": "tiktok",
                        "video_id": "TIK"}

        import scripts.upload_gate as ug
        monkeypatch.setattr(ug, "UPLOADERS",
                            {"tiktok": lambda *a, **k: FakeUploader(*a, **k)})
        updates = list(pp.stream_upload(project, "tiktok", clip, "T", "C", [],
                                        False, "warn", variant_policy="auto"))
        out = "\n".join(updates)
        assert captured["video"] == variant_path
        assert "نسخة مختلفة لمنصة tiktok" in out
        assert "Upload finished." in out

    def test_default_off_uploads_original(self, tmp_path, monkeypatch):
        project = _project(tmp_path)
        monkeypatch.setattr(pp, "_audio_qc_upload_allowed", lambda *a: (True, ""))
        clip = os.path.join(project, "final", "000_clip.mp4")
        captured = {}

        class FakeUploader:
            def __init__(self, *a, **k):
                pass

            def upload(self, video_path, title, caption, hashtags, index=None):
                captured["video"] = video_path
                return {"status": "uploaded", "platform": "tiktok"}

        import scripts.upload_gate as ug
        monkeypatch.setattr(ug, "UPLOADERS",
                            {"tiktok": lambda *a, **k: FakeUploader(*a, **k)})
        list(pp.stream_upload(project, "tiktok", clip, "T", "C", [],
                              False, "warn"))
        assert captured["video"] == clip
        assert not os.path.exists(os.path.join(project, "variants"))
