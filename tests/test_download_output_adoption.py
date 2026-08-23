from pathlib import Path

from scripts.download_video import _adopt_downloaded_video


def test_adopts_extensionless_yt_dlp_output(tmp_path):
    project = Path(tmp_path)
    base = project / "input"
    final = project / "input.mp4"
    base.write_bytes(b"0" * 2048)

    adopted = _adopt_downloaded_video(str(project), str(base), str(final))

    assert adopted == str(final)
    assert final.exists()
    assert not base.exists()
    assert final.stat().st_size == 2048


def test_keeps_existing_valid_mp4(tmp_path):
    project = Path(tmp_path)
    base = project / "input"
    final = project / "input.mp4"
    final.write_bytes(b"1" * 2048)

    adopted = _adopt_downloaded_video(str(project), str(base), str(final))

    assert adopted == str(final)
    assert final.read_bytes() == b"1" * 2048
