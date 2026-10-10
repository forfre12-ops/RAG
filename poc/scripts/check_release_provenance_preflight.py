"""Read-only provenance preflight for a KL air-gap release candidate.

This tool deliberately does *not* build an image, create a bundle, tag a
commit, sign anything, or change the working tree.  It answers a narrower
question before those irreversible steps: do the source, bundle manifest,
model, image digests, SBOM, vulnerability-scan receipt, and signature
verification receipt describe one releasable candidate?

The existing ``build_offline_bundle.py`` validates the bundle it creates, but
it honestly records ``security.scanned=false`` and its manifest has no signed
link to a source tag or image digest.  A green checksum alone therefore cannot
establish release identity.  This preflight is intentionally fail-closed for a
final package and prints JSON so the installer/release operator can retain the
result without treating this script as a signing system.

Typical final-candidate invocation (after a clean tag and the real artefacts
exist) is::

  python scripts/check_release_provenance_preflight.py \
    --bundle-manifest dist/koipa-airgap-bundle/manifest.json \
    --model-dir dist/koipa-airgap-bundle/models/classifier-trained \
    --sbom dist/koipa-airgap-bundle/licenses/sbom.cyclonedx.json \
    --scan-attestation release/scan.json \
    --signature release/manifest.sig \
    --signature-verification release/signature-verification.json \
    --required-image api-customer --image api-customer=registry/koipa-api-customer@sha256:<digest> \
    --required-image worker-customer --image worker-customer=registry/koipa-worker-customer@sha256:<digest>

``--signature-verification`` is a receipt created *after* an external
cryptographic verifier (for example cosign) succeeds.  This script validates
that the receipt binds the exact manifest and signature bytes; it never claims
to perform cryptographic verification itself.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


POC_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = POC_ROOT.parent
SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
IMAGE_DIGEST_RE = re.compile(r"@sha256:([0-9a-f]{64})$", re.IGNORECASE)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(path: Path) -> str:
    """Match build_offline_bundle.hash_model_dir exactly.

    The bundle builder's recorded model hash is relative-path bytes, a NUL
    separator, then file bytes in sorted relative-path order.  A slightly
    different but still deterministic tree hash is not useful here: it would
    falsely reject every real bundle.
    """
    digest = hashlib.sha256()
    for child in sorted(
        (p for p in path.rglob("*") if p.is_file()),
        key=lambda p: p.relative_to(path).as_posix(),
    ):
        relative = child.relative_to(path).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        with child.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path, errors: list[str], label: str) -> dict[str, Any] | None:
    if not path.is_file():
        errors.append(f"{label} is missing: {path}")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label} is not valid JSON: {path} ({exc})")
        return None
    if not isinstance(value, dict):
        errors.append(f"{label} must be a JSON object: {path}")
        return None
    return value


def _parse_time(value: object) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _age_problem(value: object, *, field: str, max_age_days: int, errors: list[str]) -> None:
    when = _parse_time(value)
    if when is None:
        errors.append(f"{field} has no parseable ISO-8601 timestamp")
        return
    if max_age_days <= 0:
        return
    age = dt.datetime.now(dt.timezone.utc) - when.astimezone(dt.timezone.utc)
    if age.days > max_age_days:
        errors.append(f"{field} is {age.days}d old (limit {max_age_days}d)")


def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        run = subprocess.run(
            ["git", *args], cwd=repo, text=True, capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return run.returncode, run.stdout.strip()


def source_identity(repo: Path) -> dict[str, Any]:
    head_rc, head = _git(repo, "rev-parse", "HEAD")
    status_rc, status = _git(repo, "status", "--porcelain=v1")
    tag_rc, tag = _git(repo, "describe", "--tags", "--exact-match", "HEAD")
    return {
        "repo": str(repo),
        "git_available": head_rc == 0,
        "git_commit": head if head_rc == 0 else "",
        "worktree_clean": status_rc == 0 and not status,
        "changed_path_count": len(status.splitlines()) if status_rc == 0 else None,
        "exact_tag": tag if tag_rc == 0 else "",
    }


def _parse_images(values: list[str], errors: list[str]) -> dict[str, str]:
    images: dict[str, str] = {}
    for item in values:
        role, sep, reference = item.partition("=")
        role, reference = role.strip(), reference.strip()
        if not sep or not role or not reference:
            errors.append(f"invalid --image value (expected role=repository@sha256:<digest>): {item!r}")
            continue
        if role in images:
            errors.append(f"duplicate image role: {role}")
            continue
        if not IMAGE_DIGEST_RE.search(reference):
            errors.append(f"image {role} is not pinned to an immutable sha256 digest: {reference}")
            continue
        images[role] = reference
    return images


def _validate_deploy_manifest(path: Path, errors: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        errors.append(f"deployment manifest is missing: {path}")
        return result
    try:
        import tomllib

        with path.open("rb") as stream:
            payload = tomllib.load(stream)
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f"deployment manifest is invalid TOML: {path} ({exc})")
        return result
    if payload.get("schema") != 1:
        errors.append(f"deployment manifest schema must be 1: {path}")
    if not isinstance(payload.get("bundle"), dict) or not isinstance(payload.get("container"), dict):
        errors.append(f"deployment manifest lacks bundle/container declarations: {path}")
    result["sha256"] = sha256_file(path)
    return result


def _validate_bundle_manifest(
    path: Path | None, source: dict[str, Any], errors: list[str],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    result: dict[str, Any] = {"path": str(path) if path else "", "exists": False}
    if path is None:
        errors.append("bundle manifest is required (pass --bundle-manifest after the bundle is built)")
        return result, None
    result["exists"] = path.is_file()
    payload = _json(path, errors, "bundle manifest")
    if payload is None:
        return result, None
    result["sha256"] = sha256_file(path)
    bundle = payload.get("bundle")
    if not isinstance(bundle, dict):
        errors.append("bundle manifest has no bundle object")
        return result, payload
    source_sha = str(source.get("git_commit") or "")
    manifest_sha = str(bundle.get("git_commit") or "")
    if not manifest_sha:
        errors.append("bundle manifest has no bundle.git_commit")
    elif source_sha and manifest_sha != source_sha:
        errors.append(f"bundle.git_commit {manifest_sha[:12]} != source {source_sha[:12]}")
    if bundle.get("dry_run") is not False:
        errors.append("bundle manifest is dry-run or omits bundle.dry_run=false")
    components = payload.get("components")
    if not isinstance(components, dict) or not components:
        errors.append("bundle manifest has no component inventory")
    models = payload.get("models")
    trained = [item for item in models if isinstance(item, dict) and item.get("role") == "classifier_trained"] if isinstance(models, list) else []
    if not trained:
        errors.append("bundle manifest has no classifier_trained model entry")
    elif not SHA256_RE.fullmatch(str(trained[0].get("sha256") or "")):
        errors.append("classifier_trained model lacks a 64-hex content hash in bundle manifest")
    return result, payload


def _validate_model(path: Path | None, bundle: dict[str, Any] | None, errors: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path) if path else "", "exists": False}
    if path is None:
        errors.append("classifier model directory is required (pass --model-dir)")
        return result
    if not path.is_dir():
        errors.append(f"classifier model directory is missing: {path}")
        return result
    result["exists"] = True
    result["tree_sha256"] = sha256_tree(path)
    temp = path / "temperature.json"
    if not temp.is_file():
        errors.append(f"classifier model has no temperature.json: {temp}")
    else:
        try:
            parsed = json.loads(temp.read_text(encoding="utf-8"))
            candidate = parsed.get("temperature") if isinstance(parsed, dict) else None
            if not isinstance(candidate, (int, float)) or candidate <= 0:
                errors.append("classifier temperature.json has no positive numeric temperature")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"classifier temperature.json is invalid: {exc}")
    if bundle is not None:
        models = bundle.get("models")
        trained = next(
            (item for item in models if isinstance(item, dict) and item.get("role") == "classifier_trained"),
            None,
        ) if isinstance(models, list) else None
        expected = str((trained or {}).get("sha256") or "")
        if SHA256_RE.fullmatch(expected) and expected.lower() != result["tree_sha256"].lower():
            errors.append("classifier model tree hash does not match bundle manifest")
    return result


def _validate_sbom(path: Path | None, max_age_days: int, errors: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path) if path else "", "exists": False}
    if path is None:
        errors.append("CycloneDX SBOM is required (pass --sbom)")
        return result
    result["exists"] = path.is_file()
    payload = _json(path, errors, "SBOM")
    if payload is None:
        return result
    result["sha256"] = sha256_file(path)
    if payload.get("bomFormat") != "CycloneDX":
        errors.append("SBOM bomFormat must be CycloneDX")
    components = payload.get("components")
    if not isinstance(components, list) or not components:
        errors.append("SBOM has no component inventory")
    metadata = payload.get("metadata")
    timestamp = metadata.get("timestamp") if isinstance(metadata, dict) else None
    _age_problem(timestamp, field="SBOM metadata.timestamp", max_age_days=max_age_days, errors=errors)
    result["component_count"] = len(components) if isinstance(components, list) else 0
    return result


def _validate_scan(
    path: Path | None, images: dict[str, str], required_images: list[str], max_age_days: int, errors: list[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path) if path else "", "exists": False}
    if path is None:
        errors.append("vulnerability scan attestation is required (pass --scan-attestation)")
        return result
    result["exists"] = path.is_file()
    payload = _json(path, errors, "scan attestation")
    if payload is None:
        return result
    result["sha256"] = sha256_file(path)
    if not str(payload.get("scanner") or "").strip():
        errors.append("scan attestation has no scanner name")
    if str(payload.get("status") or "").lower() not in {"pass", "passed"}:
        errors.append("scan attestation status must be pass")
    _age_problem(payload.get("scanned_at"), field="scan attestation scanned_at", max_age_days=max_age_days, errors=errors)
    summary = payload.get("summary")
    policy = payload.get("policy")
    if not isinstance(summary, dict) or not isinstance(policy, dict):
        errors.append("scan attestation must include summary and policy objects")
    else:
        for severity in ("critical", "high"):
            observed = summary.get(severity)
            allowed = policy.get(f"max_{severity}")
            if not isinstance(observed, int) or observed < 0:
                errors.append(f"scan attestation summary.{severity} must be a non-negative integer")
            elif not isinstance(allowed, int) or allowed < 0:
                errors.append(f"scan attestation policy.max_{severity} must be a non-negative integer")
            elif observed > allowed:
                errors.append(
                    f"scan attestation {severity} findings {observed} exceed policy maximum {allowed}"
                )
    scanned_images = payload.get("image_digests")
    if not isinstance(scanned_images, dict):
        errors.append("scan attestation has no image_digests object")
        return result
    for role in required_images:
        expected = images.get(role, "")
        actual = str(scanned_images.get(role) or "")
        expected_digest = IMAGE_DIGEST_RE.search(expected)
        if expected_digest is None:
            continue
        if actual.lower().removeprefix("sha256:") != expected_digest.group(1).lower():
            errors.append(f"scan attestation digest for {role} does not match declared image digest")
    return result


def _validate_signature(
    signature: Path | None,
    verification: Path | None,
    bundle_manifest: Path | None,
    max_age_days: int,
    errors: list[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "signature_path": str(signature) if signature else "",
        "verification_path": str(verification) if verification else "",
        "signature_exists": bool(signature and signature.is_file()),
    }
    if signature is None or not signature.is_file() or signature.stat().st_size == 0:
        errors.append("non-empty detached signature is required (pass --signature)")
        return result
    if verification is None:
        errors.append("signature verification receipt is required (pass --signature-verification)")
        return result
    payload = _json(verification, errors, "signature verification receipt")
    if payload is None:
        return result
    if not str(payload.get("verifier") or "").strip() or not str(payload.get("signer") or "").strip():
        errors.append("signature verification receipt must identify verifier and signer")
    if str(payload.get("status") or "").lower() not in {"verified", "pass", "passed"}:
        errors.append("signature verification receipt status must be verified")
    _age_problem(payload.get("verified_at"), field="signature verification receipt verified_at", max_age_days=max_age_days, errors=errors)
    if bundle_manifest is not None and bundle_manifest.is_file():
        if str(payload.get("subject_sha256") or "").lower() != sha256_file(bundle_manifest).lower():
            errors.append("signature verification receipt subject_sha256 does not bind the bundle manifest")
    if str(payload.get("signature_sha256") or "").lower() != sha256_file(signature).lower():
        errors.append("signature verification receipt signature_sha256 does not bind the detached signature")
    result["signature_sha256"] = sha256_file(signature)
    return result


def evaluate(args: argparse.Namespace, *, source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a JSON-serializable report.  No filesystem writes occur here."""
    errors: list[str] = []
    source = source or source_identity(Path(args.repo).resolve())
    if not source.get("git_available"):
        errors.append("git HEAD is unavailable; source identity cannot be established")
    if not source.get("worktree_clean"):
        errors.append("source worktree is dirty; build from a clean, committed release worktree")
    if not source.get("exact_tag"):
        errors.append("HEAD has no exact release tag")

    deploy_manifest = _validate_deploy_manifest(Path(args.deploy_manifest), errors)
    bundle_manifest_path = Path(args.bundle_manifest) if args.bundle_manifest else None
    bundle_manifest, bundle_payload = _validate_bundle_manifest(bundle_manifest_path, source, errors)
    model = _validate_model(Path(args.model_dir) if args.model_dir else None, bundle_payload, errors)
    sbom = _validate_sbom(Path(args.sbom) if args.sbom else None, args.max_age_days, errors)

    images = _parse_images(args.image, errors)
    required_images = list(dict.fromkeys(args.required_image))
    if not required_images:
        errors.append("at least one --required-image is required")
    for role in required_images:
        if role not in images:
            errors.append(f"required image has no immutable digest declaration: {role}")
    scan = _validate_scan(
        Path(args.scan_attestation) if args.scan_attestation else None,
        images,
        required_images,
        args.max_age_days,
        errors,
    )
    signature = _validate_signature(
        Path(args.signature) if args.signature else None,
        Path(args.signature_verification) if args.signature_verification else None,
        bundle_manifest_path,
        args.max_age_days,
        errors,
    )
    return {
        "schema_version": 1,
        "kind": "koipa-release-provenance-preflight",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "READY_FOR_FINAL_PACKAGE" if not errors else "BLOCKED",
        "blockers": errors,
        "source": source,
        "deployment_manifest": deploy_manifest,
        "bundle_manifest": bundle_manifest,
        "model": model,
        "images": images,
        "sbom": sbom,
        "scan": scan,
        "signature": signature,
        "notes": [
            "This is read-only preflight; it does not create a package, tag, image, SBOM, scan, or signature.",
            "A signature verification receipt must be produced by an external cryptographic verifier; this tool only verifies its file/digest bindings.",
        ],
    }


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Read-only final-package provenance preflight")
    ap.add_argument("--repo", default=str(REPO_ROOT), help="Git repository root (default: repository containing poc)")
    ap.add_argument("--deploy-manifest", default=str(POC_ROOT / "deploy_manifest.toml"))
    ap.add_argument("--bundle-manifest", help="built air-gap bundle manifest.json")
    ap.add_argument("--model-dir", help="bundled classifier-trained directory")
    ap.add_argument("--sbom", help="bundled CycloneDX SBOM")
    ap.add_argument("--image", action="append", default=[], metavar="ROLE=REPOSITORY@sha256:DIGEST")
    ap.add_argument("--required-image", action="append", default=[], metavar="ROLE")
    ap.add_argument("--scan-attestation", help="JSON receipt from the real vulnerability scan")
    ap.add_argument("--signature", help="non-empty detached signature for bundle manifest")
    ap.add_argument("--signature-verification", help="JSON receipt emitted after external signature verification")
    ap.add_argument("--max-age-days", type=int, default=14, help="maximum age for SBOM/scan/signature evidence (0 disables age checks)")
    ap.add_argument("--out", help="optional path to write the JSON report; omitted means stdout only")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.max_age_days < 0:
        parser().error("--max-age-days must be >= 0")
    report = evaluate(args)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(rendered)
    return 0 if report["status"] == "READY_FOR_FINAL_PACKAGE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
