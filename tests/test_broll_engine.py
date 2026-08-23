import json

from scripts import broll_engine


class _Response:
    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = headers or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_extract_keywords_supports_arabic_and_english():
    terms = broll_engine.extract_keywords("الذكاء الاصطناعي والذكاء الاصطناعي changes business", max_terms=5)
    assert terms[0] == "الذكاء"
    assert "business" in terms


def test_build_broll_plan_uses_timing_and_query():
    plan = broll_engine.build_broll_plan([
        {"start": 1, "end": 4, "text": "new technology for business"},
        {"start": "bad", "end": 5, "text": "ignored"},
    ])
    assert len(plan) == 1
    assert plan[0]["start"] == 1.0
    assert plan[0]["end"] == 4.0
    assert plan[0]["query"]
    assert plan[0]["status"] == "needs_asset"


def test_search_pexels_normalizes_video_files(monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _Response(
            {
                "page": 1,
                "total_results": 1,
                "videos": [{
                    "id": 42,
                    "url": "https://www.pexels.com/video/42/",
                    "duration": 8,
                    "video_files": [
                        {"width": 720, "height": 1280, "link": "https://cdn/a.mp4"},
                    ],
                    "user": {"name": "Creator", "url": "https://pexels.com/@creator"},
                }],
            },
            {"X-Ratelimit-Remaining": "199"},
        )

    monkeypatch.setattr(broll_engine.requests, "get", fake_get)
    result = broll_engine.search_pexels_videos("technology", "secret")
    assert result["ok"] is True
    assert result["items"][0]["download_url"] == "https://cdn/a.mp4"
    assert result["items"][0]["photographer"] == "Creator"
    assert captured["kwargs"]["headers"] == {"Authorization": "secret"}


def test_search_without_key_does_not_call_network(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("network must not be called")

    monkeypatch.setattr(broll_engine.requests, "get", fail)
    result = broll_engine.search_pexels_videos("anything", "")
    assert result == {"ok": False, "error": "missing_api_key", "items": []}


def test_overlay_broll_dry_run_builds_safe_command(tmp_path):
    video = tmp_path / "video.mp4"
    broll = tmp_path / "broll.mp4"
    video.write_bytes(b"video")
    broll.write_bytes(b"broll")
    result = broll_engine.overlay_broll(
        str(video), str(broll), str(tmp_path / "out.mp4"),
        start=1.5, end=3.0, opacity=0.3, dry_run=True,
    )
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert "between(t,1.5,3.0)" in " ".join(result["cmd"])


def test_save_plan_is_valid_json(tmp_path):
    path = tmp_path / "broll_plan.json"
    broll_engine.save_plan([{"query": "city"}], str(path))
    assert json.loads(path.read_text(encoding="utf-8"))[0]["query"] == "city"
