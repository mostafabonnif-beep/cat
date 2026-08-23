# -*- coding: utf-8 -*-
"""Music fingerprinting (Roadmap 2.3) — Chromaprint/AcoustID.

Pure logic is tested with synthetic fingerprints (no chromaprint binary
required on CI); the fpcalc/AcoustID network paths are exercised with mocks.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import base64  # noqa: E402

from scripts import music_fingerprint as mf  # noqa: E402


def _raw_fp(n=200, seed=7):
    """Deterministic fake raw fingerprint."""
    import random
    rng = random.Random(seed)
    return [rng.getrandbits(32) for _ in range(n)]


class TestDecodeAndGrams:
    def test_raw_passthrough(self):
        ints = _raw_fp(50)
        assert mf.decode_fingerprint(ints, "raw") == ints
        assert mf.decode_fingerprint(list(ints), "compressed") == ints

    def test_empty_compressed_without_pyacoustid(self):
        # No pyacoustid on CI → compressed decode returns [] (no crash).
        assert mf.decode_fingerprint("AAAA", "compressed") == []

    def test_grams_overlap(self):
        a = mf._grams([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        b = mf._grams([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        assert a == b
        c = mf._grams([99] * 20)
        assert not (a & c)


class TestFingerprintFile:
    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            mf.fingerprint_file(str(tmp_path / "nope.mp4"))

    def test_fpcalc_cli_flow(self, tmp_path, monkeypatch):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        monkeypatch.setattr(mf, "_import_acoustid", lambda: None)

        class FakeProc:
            returncode = 0
            stdout = "DURATION=32.5\nFINGERPRINT=1,2,3,4,5\n"
            stderr = ""

        monkeypatch.setattr(mf.shutil, "which", lambda _n: "/usr/bin/fpcalc")
        monkeypatch.setattr(mf.subprocess, "run", lambda *a, **k: FakeProc())
        result = mf.fingerprint_file(str(video))
        assert result["engine"] == "fpcalc"
        assert result["format"] == "raw"
        assert result["duration"] == 32.5
        assert result["fingerprint"] == [1, 2, 3, 4, 5]

    def test_no_backend_raises_clear(self, tmp_path, monkeypatch):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        monkeypatch.setattr(mf, "_import_acoustid", lambda: None)
        monkeypatch.setattr(mf.shutil, "which", lambda _n: None)
        with pytest.raises(mf.FpcalcUnavailable, match="Chromaprint not found"):
            mf.fingerprint_file(str(video))

    def test_fpcalc_error_raises(self, tmp_path, monkeypatch):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        monkeypatch.setattr(mf, "_import_acoustid", lambda: None)
        monkeypatch.setattr(mf.shutil, "which", lambda _n: "/usr/bin/fpcalc")

        class FakeProc:
            returncode = 1
            stdout = ""
            stderr = "no audio stream"

        monkeypatch.setattr(mf.subprocess, "run", lambda *a, **k: FakeProc())
        with pytest.raises(RuntimeError, match="no audio stream"):
            mf.fingerprint_file(str(video))


class TestLocalMatch:
    def test_exact_match(self):
        clip = _raw_fp(100, seed=1)
        db = {"songs": [{"title": "My Song", "fingerprint": list(clip),
                         "format": "raw", "duration": 100.0}]}
        result = mf.match_local(db, clip, "raw", threshold=0.1)
        assert result["matched"] is True
        assert result["song"] == "My Song"
        assert result["score"] >= 0.9

    def test_related_clip_matches(self):
        # a 40-sub-fingerprint excerpt of a longer song
        song = _raw_fp(400, seed=3)
        clip = song[100:140]
        db = {"songs": [{"title": "Long Song", "fingerprint": song,
                         "format": "raw", "duration": 300.0}]}
        result = mf.match_local(db, clip, "raw", threshold=0.1)
        assert result["matched"] is True
        assert result["score"] > 0.5

    def test_unrelated_does_not_match(self):
        db = {"songs": [{"title": "A", "fingerprint": _raw_fp(300, seed=11),
                         "format": "raw", "duration": 100.0}]}
        result = mf.match_local(db, _raw_fp(100, seed=99), "raw", threshold=0.1)
        assert result["matched"] is False

    def test_empty_db_no_match(self):
        result = mf.match_local({"songs": []}, _raw_fp(30), "raw")
        assert result["matched"] is False


class TestIdentifyAcoustid:
    def test_raw_fingerprint_is_encoded_and_queried(self, monkeypatch):
        """fpcalc raw ints now reach AcoustID (no pyacoustid needed)."""
        import urllib.request
        captured = {}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"status": "ok", "results": []}).encode()

        def fake_urlopen(url, timeout=60):
            captured["url"] = url
            return FakeResp()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert mf.identify_acoustid([1, 2, 3], 30) == []
        assert "fingerprint=" in captured["url"]
        assert "duration=30" in captured["url"]
        assert "client=8XaBELgH" in captured["url"]

    def test_unencodable_raw_returns_empty(self, monkeypatch):
        def boom(url, timeout=60):
            raise AssertionError("must not hit the network")

        monkeypatch.setattr(mf.urllib.request, "urlopen", boom)
        # A fingerprint that cannot be packed (e.g. non-int entries) →
        # no request is made, [] returned.
        assert mf.identify_acoustid(["x", "y"], 30) == []

    def test_lookup_parses_results(self, monkeypatch):
        payload = {
            "status": "ok",
            "results": [{
                "score": 0.9,
                "recordings": [{
                    "id": "R1",
                    "title": "Song Title",
                    "artists": [{"name": "Artist Name"}],
                    "sources": 3,
                }],
            }],
        }

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(payload).encode()

        def fake_urlopen(url, timeout=60):
            assert "client=8XaBELgH" in url
            assert "fingerprint=" in url
            return FakeResp()

        monkeypatch.setattr(mf.urllib.request, "urlopen", fake_urlopen)
        results = mf.identify_acoustid("COMPRESSED_FP", 30)
        assert results[0]["title"] == "Song Title"
        assert results[0]["artist"] == "Artist Name"
        assert results[0]["score"] == 0.9

    def test_lookup_failure_returns_empty(self, monkeypatch):
        def boom(url, timeout=60):
            raise OSError("offline")

        monkeypatch.setattr(mf.urllib.request, "urlopen", boom)
        assert mf.identify_acoustid("COMPRESSED_FP", 30) == []


class TestAnalyzeProject:
    def _project(self, tmp_path, n=2):
        final = tmp_path / "final"
        final.mkdir()
        for i in range(n):
            (final / "{:03d}_clip.mp4".format(i)).write_bytes(b"x")
        return str(tmp_path)

    def test_report_written_with_no_fpcalc(self, tmp_path, monkeypatch):
        project = self._project(tmp_path)
        monkeypatch.setattr(mf, "_import_acoustid", lambda: None)
        monkeypatch.setattr(mf.shutil, "which", lambda _n: None)
        report = mf.analyze_project(project)
        assert report["summary"]["no_fpcalc"] == 2
        assert os.path.exists(os.path.join(project, mf.REPORT_NAME))
        saved = json.load(open(os.path.join(project, mf.REPORT_NAME)))
        assert saved["clips"][0]["verdict"] == "no_fpcalc"

    def test_report_matches_local_db(self, tmp_path, monkeypatch):
        project = self._project(tmp_path)
        clip_fp = _raw_fp(100, seed=5)
        monkeypatch.setattr(mf, "fingerprint_file",
                            lambda p, timeout=600: {"fingerprint": clip_fp,
                                                    "duration": 30.0,
                                                    "engine": "fpcalc",
                                                    "format": "raw"})
        # No real network: AcoustID lookup is a no-op in this test.
        monkeypatch.setattr(mf, "identify_acoustid", lambda *a, **k: [])
        db = {"songs": [{"title": "Licensed Track",
                         "fingerprint": list(clip_fp),
                         "format": "raw", "duration": 30.0}]}
        report = mf.analyze_project(project, local_db=db)
        assert report["summary"]["checked"] == 2
        assert report["summary"]["matched"] == 2
        assert report["clips"][0]["verdict"] == "local_match"
        assert report["clips"][0]["local_match"]["song"] == "Licensed Track"

    def test_report_marks_backend_and_coverage_note(self, tmp_path, monkeypatch):
        project = self._project(tmp_path, n=1)
        monkeypatch.setattr(mf, "_import_acoustid", lambda: None)
        monkeypatch.setattr(mf, "_fpcalc_candidates", lambda: ["/fake/fpcalc"])
        monkeypatch.setattr(mf, "fingerprint_file",
                            lambda p, timeout=600: {"fingerprint": [1, 2, 3],
                                                    "duration": 1.0,
                                                    "engine": "fpcalc",
                                                    "format": "raw"})
        monkeypatch.setattr(mf, "identify_acoustid", lambda *a, **k: [])
        report = mf.analyze_project(project)
        assert report["backend"] == "fpcalc"
        assert "Arabic" in report["coverage_note"]
        assert "local reference DB" in report["coverage_note"]


class TestCompressFingerprint:
    def test_known_vector(self):
        # 1,2,3 → 3× little-endian uint32 → base64 (12 bytes → 16 chars).
        assert mf.compress_fingerprint([1, 2, 3]) == "AQAAAAIAAAADAAAA"

    def test_negative_ints_mask_to_32bit(self):
        # fpcalc -raw prints signed values; -1 == 0xFFFFFFFF in 32-bit.
        assert mf.compress_fingerprint([-1]) == mf.compress_fingerprint([2 ** 32 - 1])

    def test_roundtrip(self):
        import struct
        ints = [123456, -7, 2 ** 31, 0]
        enc = mf.compress_fingerprint(ints)
        raw = base64.b64decode(enc)
        vals = list(struct.unpack("<{}I".format(len(ints)), raw))
        assert vals == [i & 0xFFFFFFFF for i in ints]

    def test_empty_returns_none(self):
        assert mf.compress_fingerprint([]) is None

    def test_garbage_returns_none(self):
        assert mf.compress_fingerprint(["a", None]) is None


class TestFpcalcDiscovery:
    def test_finds_bundled_fpcalc_next_to_exe(self, tmp_path, monkeypatch):
        exe = tmp_path / "ViralCutter.exe"
        exe.write_bytes(b"")
        fpcalc = tmp_path / "fpcalc.exe"
        fpcalc.write_bytes(b"")
        monkeypatch.setattr(mf.sys, "executable", str(exe))
        assert mf._fpcalc_candidates()[0] == str(fpcalc)

    def test_finds_fpcalc_in_user_bin(self, tmp_path, monkeypatch):
        fpcalc = tmp_path / "fpcalc.exe"
        fpcalc.write_bytes(b"")
        monkeypatch.setattr(mf, "USER_BIN_DIR", str(tmp_path))
        assert str(fpcalc) in mf._fpcalc_candidates()

    def test_available_with_fpcalc_only(self, monkeypatch):
        monkeypatch.setattr(mf, "_import_acoustid", lambda: None)
        monkeypatch.setattr(mf, "_fpcalc_candidates", lambda: ["/x/fpcalc"])
        assert mf.fpcalc_available() is True


class TestInstallFpcalc:
    def test_all_downloads_fail_raises_clear(self, tmp_path, monkeypatch):
        import urllib.request

        def boom(req, *a, **k):
            raise OSError("offline")

        monkeypatch.setattr(urllib.request, "urlopen", boom)
        monkeypatch.setattr(mf.sys, "frozen", False, raising=False)
        with pytest.raises(RuntimeError, match="fpcalc download failed"):
            mf.install_fpcalc(target_dir=str(tmp_path), timeout=10)


class TestMusicGate:
    def _report(self, tmp_path, verdict="local_match"):
        data = {
            "gate": "warn",
            "clips": [
                {"index": 0, "video": "x.mp4", "verdict": "clean"},
                {"index": 1, "video": "y.mp4", "verdict": verdict,
                 "suggestion": "Audio overlaps local reference 'T' (100%)"},
            ],
        }
        with open(os.path.join(tmp_path, mf.REPORT_NAME), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def test_no_report_no_reasons(self, tmp_path):
        assert mf.music_gate_reasons(str(tmp_path)) == []

    def test_warn_medium(self, tmp_path):
        self._report(tmp_path)
        reasons = mf.music_gate_reasons(str(tmp_path), index=1)
        assert len(reasons) == 1
        assert reasons[0]["severity"] == "medium"
        assert reasons[0]["source"] == "music_fingerprint"
        # other clip is clean
        assert mf.music_gate_reasons(str(tmp_path), index=0) == []

    def test_block_high(self, tmp_path):
        self._report(tmp_path)
        reasons = mf.music_gate_reasons(str(tmp_path), index=1, gate="block")
        assert reasons[0]["severity"] == "high"

    def test_off_no_reasons(self, tmp_path):
        self._report(tmp_path)
        assert mf.music_gate_reasons(str(tmp_path), index=1, gate="off") == []


class TestGateIntegration:
    def test_blocked_by_music_report(self, tmp_path):
        from scripts import upload_gate as ug
        report = {
            "gate": "block",
            "clips": [{"index": 0, "video": "x.mp4",
                       "verdict": "acoustid_match",
                       "suggestion": "AcoustID: 'Hit' by Star (90%)"}],
        }
        with open(os.path.join(tmp_path, mf.REPORT_NAME), "w", encoding="utf-8") as f:
            json.dump(report, f)
        verdict = ug.check_clip(str(tmp_path), 0, "Title", "Caption", [],
                                music_gate="block")
        assert verdict["allowed"] is False
        assert any(r["source"] == "music_fingerprint" for r in verdict["reasons"])

    def test_warn_allows_upload(self, tmp_path):
        from scripts import upload_gate as ug
        report = {
            "gate": "warn",
            "clips": [{"index": 0, "video": "x.mp4",
                       "verdict": "local_match",
                       "suggestion": "Audio overlaps 'T' (80%)"}],
        }
        with open(os.path.join(tmp_path, mf.REPORT_NAME), "w", encoding="utf-8") as f:
            json.dump(report, f)
        verdict = ug.check_clip(str(tmp_path), 0, "Title", "Caption", [],
                                music_gate="warn")
        # warn → flagged (medium) but still allowed
        assert verdict["allowed"] is True
        assert any(r["severity"] == "medium" for r in verdict["reasons"])
