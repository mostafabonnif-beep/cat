import json

from scripts import provenance, upload_gate


def test_provenance_requires_rights_and_meaningful_transformation(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "risk_scorecard.json").write_text(
        json.dumps({"segments": [{"index": 0}]}), encoding="utf-8"
    )
    report = provenance.analyze_project(str(project), policy="block")
    assert report["summary"]["blocked"] == 1
    assert report["clips"][0]["rights"]["status"] == "missing"
    assert report["clips"][0]["transformation"]["status"] == "insufficient"


def test_provenance_allows_declared_license_and_commentary(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "rights_manifest.json").write_text(json.dumps({
        "basis": "licensed",
        "license": "CC BY 4.0",
        "proof_url": "https://example.test/license",
        "attestation": True,
        "allow_publish": True,
    }), encoding="utf-8")
    (project / "project_manifest.json").write_text(json.dumps({
        "transformation": {"clips": {"0": {"commentary": True}}}
    }), encoding="utf-8")
    (project / "risk_scorecard.json").write_text(
        json.dumps({"segments": [{"index": 0}]}), encoding="utf-8"
    )
    report = provenance.analyze_project(str(project), policy="block")
    assert report["summary"]["allow"] == 1
    assert report["clips"][0]["transformation"]["status"] == "meaningful"


def test_upload_gate_blocks_when_provenance_policy_is_block(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "provenance_report.json").write_text(json.dumps({
        "policy": "block",
        "clips": [{"index": 0, "action": "block", "reasons": ["rights missing"]}],
    }), encoding="utf-8")
    verdict = upload_gate.check_clip(str(project), index=0, title="safe title")
    assert not verdict["allowed"]
    assert any(reason["source"] == "provenance" for reason in verdict["reasons"])
