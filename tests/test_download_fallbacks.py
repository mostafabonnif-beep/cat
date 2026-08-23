from scripts import download_video


def test_format_attempts_contains_quality_and_safe_fallbacks():
    attempts = download_video._format_attempts("bestvideo+bestaudio/best")
    formats = [item[0] for item in attempts]
    clients = [item[1] for item in attempts]
    assert formats[0] == "bestvideo+bestaudio/best"
    assert "best" in formats
    assert any(client == ["android", "web_safari"] for client in clients)


def test_runtime_options_uses_explicit_deno_path(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_DENO_PATH", r"D:\\Tools\\deno.exe")
    options = download_video._runtime_options()
    assert options == {"js_runtimes": {"deno": {"path": r"D:\\Tools\\deno.exe"}}}


def test_http_block_detection_is_narrow_enough_for_fallbacks():
    assert download_video._is_http_block("HTTP Error 429: Too Many Requests")
    assert download_video._is_http_block("403 Forbidden")
    assert not download_video._is_http_block("Private video. Sign in")
