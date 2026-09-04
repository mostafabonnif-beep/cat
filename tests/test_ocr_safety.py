from scripts import ocr_safety


def test_unavailable_tesseract_is_safe_and_explicit(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr_safety, "availability", lambda *_: {
        "available": False,
        "binary": None,
        "reason": "tesseract_not_installed",
    })
    report = ocr_safety.analyze_video(str(tmp_path / "clip.mp4"))
    assert report["available"] is False
    assert report["reason"] == "tesseract_not_installed"
    assert report["action"] == "allow"


def test_ocr_text_is_sent_through_policy_engine(monkeypatch):
    monkeypatch.setattr(ocr_safety, "availability", lambda *_: {
        "available": True,
        "binary": "/usr/bin/tesseract",
        "reason": None,
    })
    monkeypatch.setattr(ocr_safety, "_duration", lambda *_: 2.0)
    monkeypatch.setattr(ocr_safety, "_frame_png", lambda *_: b"png")
    monkeypatch.setattr(ocr_safety, "_recognize", lambda *_: "kill them all")
    report = ocr_safety.analyze_video("clip.mp4", frames=1)
    assert report["status"] == "scanned"
    assert report["action"] == "block"
    assert report["score"] == 100
    assert report["matches"]
