"""Rights and transformation evidence for responsible short-form publishing."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

REPORT_FILENAME = "provenance_report.json"
RIGHTS_FILENAME = "rights_manifest.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _manifest(project_folder: str) -> dict[str, Any]:
    return _load_json(os.path.join(project_folder, "project_manifest.json"))


def _rights(project_folder: str) -> dict[str, Any]:
    explicit = _load_json(os.path.join(project_folder, RIGHTS_FILENAME))
    if explicit:
        return explicit
    source = _manifest(project_folder).get("source") or {}
    declared = source.get("rights")
    if isinstance(declared, dict):
        return declared
    return {}


def _rights_evidence(project_folder: str) -> dict[str, Any]:
    data = _rights(project_folder)
    basis = str(data.get("basis", data.get("status", "")) or "").strip().lower()
    allowed_basis = {"owned", "licensed", "public_domain", "cc0", "permission"}
    proof_url = str(data.get("proof_url", data.get("license_url", "")) or "").strip()
    attestation = bool(data.get("attestation") or data.get("owner_attestation"))
    allow_publish = bool(data.get("allow_publish"))
    if not data:
        return {
            "status": "missing",
            "action": "review",
            "basis": None,
            "reasons": ["No rights manifest or ownership declaration was found."],
        }
    reasons = []
    if basis not in allowed_basis:
        reasons.append("Rights basis is missing or not recognized.")
    if not (proof_url or attestation or allow_publish):
        reasons.append("Add a license/permission URL or an owner attestation.")
    if allow_publish and basis in allowed_basis and not reasons:
        action = "allow"
        status = "declared"
    else:
        action = "review"
        status = "incomplete"
    return {
        "status": status,
        "action": action,
        "basis": basis or None,
        "proof_url": proof_url or None,
        "attestation": attestation,
        "allow_publish": allow_publish,
        "reasons": reasons,
    }


def _clip_files(project_folder: str, index: int | None) -> list[str]:
    if index is None:
        return []
    token = f"{int(index):03d}"
    found = []
    for root, _dirs, files in os.walk(project_folder):
        if root.endswith(".git") or "Trash" in root:
            continue
        for name in files:
            lower = name.lower()
            if token in name and lower.endswith((".json", ".mp4", ".mov", ".mkv", ".ass", ".srt", ".vtt")):
                found.append(os.path.join(root, name))
    return found


def _declared_transformation(project_folder: str, index: int | None) -> dict[str, Any]:
    manifest = _manifest(project_folder)
    transformation = manifest.get("transformation")
    if isinstance(transformation, dict):
        per_clip = transformation.get("clips")
        if isinstance(per_clip, dict):
            transformation = per_clip.get(str(index), per_clip.get(index, transformation))
        if isinstance(transformation, dict):
            return transformation
    return {}


def _transformation_evidence(project_folder: str, index: int | None) -> dict[str, Any]:
    declared = _declared_transformation(project_folder, index)
    files = [os.path.basename(path).lower() for path in _clip_files(project_folder, index)]
    evidence: list[dict[str, Any]] = []

    def add(name: str, weight: int, value: Any, source: str) -> None:
        if bool(value):
            evidence.append({"name": name, "weight": weight, "source": source})

    add("commentary", 35, declared.get("commentary") or declared.get("original_analysis"), "manifest")
    add("voiceover", 35, declared.get("voiceover") or declared.get("voiceover_path"), "manifest")
    add("broll", 20, declared.get("broll") or any("broll" in name for name in files), "manifest_or_artifact")
    add("substantive_edit", 15, declared.get("substantive_edit") or declared.get("editorial_edit"), "manifest")
    add("face_reframe", 8, declared.get("face_reframe") or any("tracking" in name for name in files), "manifest_or_artifact")
    add("captions", 5, declared.get("captions") or any(name.endswith((".ass", ".srt", ".vtt")) for name in files), "manifest_or_artifact")
    add("branding", 3, declared.get("branding") or any("polish" in name or "logo" in name for name in files), "manifest_or_artifact")
    add("intro_outro", 5, declared.get("intro_outro"), "manifest")

    score = min(100, sum(item["weight"] for item in evidence))
    major = {"commentary", "voiceover", "broll"}
    has_major = any(item["name"] in major for item in evidence)
    if score >= 35 and has_major:
        action = "allow"
        status = "meaningful"
        reasons = []
    else:
        action = "review"
        status = "insufficient"
        reasons = ["Captions, cropping, or re-encoding alone are not treated as meaningful transformation."]
        if not has_major:
            reasons.append("Document commentary, voiceover, B-roll, or original analysis in the manifest.")
    return {
        "status": status,
        "action": action,
        "score": score,
        "evidence": evidence,
        "reasons": reasons,
    }


def assess_clip(project_folder: str, index: int | None, policy: str = "warn") -> dict[str, Any]:
    policy = str(policy or "warn").lower()
    if policy not in {"warn", "block"}:
        policy = "warn"
    rights = _rights_evidence(project_folder)
    transformation = _transformation_evidence(project_folder, index)
    reasons = list(rights.get("reasons", [])) + list(transformation.get("reasons", []))
    review = rights.get("action") != "allow" or transformation.get("action") != "allow"
    action = "block" if policy == "block" and review else ("review" if review else "allow")
    return {
        "policy": policy,
        "action": action,
        "rights": rights,
        "transformation": transformation,
        "reasons": reasons,
    }


def analyze_project(project_folder: str, policy: str = "warn") -> dict[str, Any]:
    scorecard = _load_json(os.path.join(project_folder, "risk_scorecard.json"))
    entries = scorecard.get("segments") or []
    if not entries:
        entries = [{"index": 0}]
    clips = []
    for entry in entries:
        index = entry.get("index") if isinstance(entry, dict) else None
        result = assess_clip(project_folder, index, policy=policy)
        result["index"] = index
        clips.append(result)
    blocked = [item for item in clips if item["action"] == "block"]
    review = [item for item in clips if item["action"] == "review"]
    report = {
        "generated_at": _now(),
        "policy": policy,
        "summary": {
            "total": len(clips),
            "allow": len(clips) - len(blocked) - len(review),
            "review": len(review),
            "blocked": len(blocked),
        },
        "clips": clips,
    }
    temp = os.path.join(project_folder, REPORT_FILENAME + ".tmp")
    os.makedirs(project_folder, exist_ok=True)
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    os.replace(temp, os.path.join(project_folder, REPORT_FILENAME))
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Audit rights and transformation evidence")
    parser.add_argument("--project", required=True)
    parser.add_argument("--policy", choices=["warn", "block"], default="warn")
    parser.add_argument("--init", action="store_true")
    args = parser.parse_args(argv)
    if args.init:
        path = os.path.join(args.project, RIGHTS_FILENAME)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"basis": "licensed", "license": "", "proof_url": "", "attestation": False, "allow_publish": False}, handle, ensure_ascii=False, indent=2)
    report = analyze_project(args.project, policy=args.policy)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 1 if report["summary"]["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
