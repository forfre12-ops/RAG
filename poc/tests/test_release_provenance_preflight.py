"""Tests for the read-only final-package provenance preflight."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_provenance_preflight", ROOT / "scripts" / "check_release_provenance_preflight.py",
)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_candidate(tmp_path: Path) -> dict[str, Path]:
    deploy = tmp_path / "deploy_manifest.toml"
    deploy.write_text("schema = 1\n[bundle]\n[container]\n", encoding="utf-8")
    model = tmp_path / "models" / "classifier-trained"
    model.mkdir(parents=True)
    (model / "model.safetensors").write_bytes(b"weights")
    (model / "temperature.json").write_text('{"temperature": 2.03}', encoding="utf-8")
    tree = preflight.sha256_tree(model)
    bundle = tmp_path / "manifest.json"
    bundle.write_text(json.dumps({
        "bundle": {"git_commit": "a" * 40, "dry_run": False},
        "components": {"api-customer": {"image": "example/api", "sha256": "b" * 64}},
        "models": [{"role": "classifier_trained", "sha256": tree}],
    }), encoding="utf-8")
    sbom = tmp_path / "sbom.cyclonedx.json"
    now = datetime.now(timezone.utc).isoformat()
    sbom.write_text(json.dumps({
        "bomFormat": "CycloneDX", "specVersion": "1.5",
        "metadata": {"timestamp": now}, "components": [{"name": "fastapi"}],
    }), encoding="utf-8")
    scan = tmp_path / "scan.json"
    scan.write_text(json.dumps({
        "scanner": "trivy", "status": "pass", "scanned_at": now,
        "image_digests": {"api-customer": "c" * 64},
        "summary": {"critical": 0, "high": 0},
        "policy": {"max_critical": 0, "max_high": 0},
    }), encoding="utf-8")
    signature = tmp_path / "manifest.sig"
    signature.write_bytes(b"detached-signature")
    verification = tmp_path / "signature-verification.json"
    verification.write_text(json.dumps({
        "verifier": "cosign verify-blob", "signer": "release@example.invalid",
        "status": "verified", "verified_at": now,
        "subject_sha256": _sha(bundle), "signature_sha256": _sha(signature),
    }), encoding="utf-8")
    return {"deploy": deploy, "model": model, "bundle": bundle, "sbom": sbom, "scan": scan, "signature": signature, "verification": verification}


def _args(paths: dict[str, Path], **overrides) -> argparse.Namespace:
    values = {
        "repo": str(ROOT.parent), "deploy_manifest": str(paths["deploy"]),
        "bundle_manifest": str(paths["bundle"]), "model_dir": str(paths["model"]),
        "sbom": str(paths["sbom"]), "image": ["api-customer=registry.example/koipa@sha256:" + "c" * 64],
        "required_image": ["api-customer"], "scan_attestation": str(paths["scan"]),
        "signature": str(paths["signature"]), "signature_verification": str(paths["verification"]),
        "max_age_days": 14, "out": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _source() -> dict:
    return {
        "repo": "fixture", "git_available": True, "git_commit": "a" * 40,
        "worktree_clean": True, "changed_path_count": 0, "exact_tag": "v1.0.0",
    }


def test_complete_candidate_is_ready_without_writing(tmp_path: Path):
    paths = _write_candidate(tmp_path)
    report = preflight.evaluate(_args(paths), source=_source())
    assert report["status"] == "READY_FOR_FINAL_PACKAGE"
    assert report["blockers"] == []
    assert not (tmp_path / "release-proof.json").exists()


def test_tag_dirty_and_unpinned_image_block_candidate(tmp_path: Path):
    paths = _write_candidate(tmp_path)
    source = _source() | {"worktree_clean": False, "exact_tag": ""}
    report = preflight.evaluate(_args(paths, image=["api-customer=registry.example/koipa:latest"]), source=source)
    assert report["status"] == "BLOCKED"
    joined = "\n".join(report["blockers"])
    assert "worktree is dirty" in joined
    assert "no exact release tag" in joined
    assert "not pinned" in joined


def test_model_scan_and_signature_are_bound_to_the_candidate(tmp_path: Path):
    paths = _write_candidate(tmp_path)
    (paths["model"] / "model.safetensors").write_bytes(b"different-weights")
    receipt = json.loads(paths["verification"].read_text(encoding="utf-8"))
    receipt["subject_sha256"] = "0" * 64
    paths["verification"].write_text(json.dumps(receipt), encoding="utf-8")
    scan = json.loads(paths["scan"].read_text(encoding="utf-8"))
    scan["image_digests"]["api-customer"] = "0" * 64
    paths["scan"].write_text(json.dumps(scan), encoding="utf-8")
    report = preflight.evaluate(_args(paths), source=_source())
    joined = "\n".join(report["blockers"])
    assert report["status"] == "BLOCKED"
    assert "model tree hash" in joined
    assert "scan attestation digest" in joined
    assert "subject_sha256" in joined


def test_scan_receipt_requires_explicit_severity_policy(tmp_path: Path):
    paths = _write_candidate(tmp_path)
    scan = json.loads(paths["scan"].read_text(encoding="utf-8"))
    scan["summary"]["high"] = 2
    scan["policy"]["max_high"] = 1
    paths["scan"].write_text(json.dumps(scan), encoding="utf-8")
    report = preflight.evaluate(_args(paths), source=_source())
    assert report["status"] == "BLOCKED"
    assert "high findings 2 exceed policy maximum 1" in "\n".join(report["blockers"])


def test_cli_returns_blocked_without_creating_artifacts(tmp_path: Path, capsys):
    out = tmp_path / "should-not-exist.json"
    code = preflight.main(["--repo", str(tmp_path), "--deploy-manifest", str(tmp_path / "none.toml")])
    captured = capsys.readouterr().out
    assert code == 2
    assert '"status": "BLOCKED"' in captured
    assert not out.exists()
