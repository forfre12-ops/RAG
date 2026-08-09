"""Fail-closed comparison of two classifiers on frozen proxy gold only.

This module intentionally separates a useful proxy-regression comparison from
an accuracy claim about customer documents.  The input must be the exact
1,000-record artifact emitted by ``assemble_proxy_gold.py`` and attested by its
assembly report.  When a proxy-training manifest is supplied, its materialized
document splits are re-read and checked for ID, family, and normalized-text
overlap instead of trusting the manifest's reported zeroes.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
from typing import Any
import uuid

from lloydk.hygiene import text_hash
from lloydk.modules.m2_preprocess.chunker import split as _m5_char_split
from lloydk.proxy_corpus import (
    DEFAULT_TARGET_COUNTS,
    GRADE_CODES,
    PUBLIC_REAL,
    validate_proxy_record,
)


SCHEMA_VERSION = "proxy-model-comparison-v1"
EXPECTED_FROZEN_DOCUMENTS = 1_000
EXPECTED_FINAL_LOCKED_DOCUMENTS = 800
EXPECTED_PUBLIC_S3_CHALLENGE_DOCUMENTS = 300
LABELS = ("TS", "S1", "S2", "S3")
GRADE_SEVERITY = {label: index for index, label in enumerate(LABELS)}
CLAIM_SCOPE = "frozen_proxy_regression_only_not_customer_real_accuracy"
LEGACY_TRAINING_ATTESTATION_SCHEMA = "legacy-training-corpus-attestation-v1"
SERVING_MAX_LENGTH = 512
SERVING_CHUNK_OVERLAP = 64
SERVING_SEVERE_AGG_CODES = ("TS", "S1")
SERVING_CHAR_CHUNK_MULTIPLIER = 3
SERVING_INFERENCE_BATCH_SIZE = 8
COMPARISON_MODES = (
    "raw_model",
    "bundle_operating_point",
)
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ProxyComparisonError(ValueError):
    """An input or artifact violates the comparison contract."""


@dataclass(frozen=True)
class ModelPredictionBatch:
    """Document-level predictions plus an aggregation runtime attestation."""

    predictions: tuple[dict[str, object], ...]
    runtime_attestation: dict[str, object]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_json_loads(raw: str, *, location: str) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant {value}")

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(
            raw,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        detail = exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)
        raise ProxyComparisonError(f"malformed JSON at {location}: {detail}") from exc


def _read_json_object(path: Path, *, purpose: str) -> tuple[dict[str, object], bytes]:
    if path.is_symlink() or not path.is_file():
        raise ProxyComparisonError(f"{purpose} is not a regular file: {path}")
    try:
        payload = path.read_bytes()
        decoded = payload.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ProxyComparisonError(f"cannot read {purpose} {path}: {exc}") from exc
    value = _strict_json_loads(decoded, location=str(path))
    if not isinstance(value, dict):
        raise ProxyComparisonError(f"{purpose} must be a JSON object: {path}")
    return value, payload


def _read_jsonl(path: Path, *, purpose: str) -> tuple[list[dict[str, object]], bytes]:
    if path.suffix.lower() != ".jsonl":
        raise ProxyComparisonError(f"{purpose} must be a .jsonl file: {path}")
    if path.is_symlink() or not path.is_file():
        raise ProxyComparisonError(f"{purpose} is not a regular file: {path}")
    try:
        payload = path.read_bytes()
        decoded = payload.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ProxyComparisonError(f"cannot read {purpose} {path}: {exc}") from exc
    if not decoded.strip():
        raise ProxyComparisonError(f"{purpose} is empty: {path}")

    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(decoded.splitlines(), 1):
        if not line.strip():
            continue
        value = _strict_json_loads(line, location=f"{path}:{line_number}")
        if not isinstance(value, dict):
            raise ProxyComparisonError(
                f"{purpose} row must be a JSON object at {path}:{line_number}"
            )
        rows.append(value)
    if not rows:
        raise ProxyComparisonError(f"{purpose} contains no records: {path}")
    return rows, payload


def _required_record_fields(
    rows: Sequence[Mapping[str, object]], *, purpose: str
) -> dict[str, set[str]]:
    ids: set[str] = set()
    families: set[str] = set()
    text_hashes: set[str] = set()
    for index, row in enumerate(rows):
        fields: dict[str, str] = {}
        for key in ("doc_id", "document_family_id", "text", "label"):
            value = row.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ProxyComparisonError(
                    f"{purpose} row {index} has missing/invalid {key}"
                )
            fields[key] = value.strip() if key != "text" else value
        if fields["label"] not in GRADE_CODES:
            raise ProxyComparisonError(
                f"{purpose} row {index} has invalid label {fields['label']!r}"
            )
        digest = text_hash(fields["text"])
        if fields["doc_id"] in ids:
            raise ProxyComparisonError(
                f"{purpose} has duplicate doc_id {fields['doc_id']!r}"
            )
        if digest in text_hashes:
            raise ProxyComparisonError(
                f"{purpose} has duplicate normalized text hash at row {index}: {digest}"
            )
        ids.add(fields["doc_id"])
        families.add(fields["document_family_id"])
        text_hashes.add(digest)
    return {"doc_ids": ids, "family_ids": families, "text_hashes": text_hashes}


def _canonical_records_sha256(rows: Sequence[Mapping[str, object]]) -> str:
    ordered = sorted(rows, key=lambda row: str(row["doc_id"]))
    payload = b"".join(
        (
            json.dumps(
                dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        ).encode("utf-8")
        for row in ordered
    )
    return _sha256_bytes(payload)


def load_frozen_proxy_gold(
    corpus_path: Path, assembly_manifest_path: Path
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Load and attest the exact frozen 1,000-record proxy-gold artifact."""
    rows, corpus_bytes = _read_jsonl(corpus_path, purpose="frozen proxy corpus")
    manifest, manifest_bytes = _read_json_object(
        assembly_manifest_path, purpose="frozen assembly manifest"
    )
    if manifest.get("ready") is not True:
        raise ProxyComparisonError("frozen assembly manifest is not ready=true")
    artifact = manifest.get("artifact")
    if not isinstance(artifact, Mapping):
        raise ProxyComparisonError("frozen assembly manifest is missing artifact attestation")
    expected_hash = str(artifact.get("sha256") or "")
    actual_hash = _sha256_bytes(corpus_bytes)
    if not _SHA256.fullmatch(expected_hash) or expected_hash != actual_hash:
        raise ProxyComparisonError(
            "frozen corpus SHA-256 does not match its assembly manifest"
        )
    if artifact.get("records") != EXPECTED_FROZEN_DOCUMENTS:
        raise ProxyComparisonError(
            "frozen assembly artifact must attest exactly 1,000 records"
        )
    if len(rows) != EXPECTED_FROZEN_DOCUMENTS:
        raise ProxyComparisonError(
            f"frozen corpus must contain exactly 1,000 records; found {len(rows)}"
        )

    identities = _required_record_fields(rows, purpose="frozen proxy corpus")
    distribution = Counter(str(row["label"]) for row in rows)
    expected_distribution = dict(DEFAULT_TARGET_COUNTS)
    if dict(distribution) != expected_distribution:
        raise ProxyComparisonError(
            "frozen grade distribution mismatch: "
            f"expected {expected_distribution}, found {dict(distribution)}"
        )
    if len(identities["family_ids"]) < 2:
        raise ProxyComparisonError(
            "family-cluster bootstrap requires at least two frozen families"
        )

    stats = manifest.get("stats")
    if not isinstance(stats, Mapping):
        raise ProxyComparisonError("frozen assembly manifest is missing stats")
    if stats.get("selected") != EXPECTED_FROZEN_DOCUMENTS:
        raise ProxyComparisonError("assembly stats do not attest 1,000 selected records")
    if stats.get("selected_by_grade") != expected_distribution:
        raise ProxyComparisonError("assembly stats grade distribution is not the frozen target")
    shortcut_gate = stats.get("shortcut_gate")
    if not isinstance(shortcut_gate, Mapping) or shortcut_gate.get("passed") is not True:
        raise ProxyComparisonError("frozen assembly shortcut gate did not pass")

    audit: dict[str, object] = {
        "path": str(corpus_path.resolve()),
        "file_sha256": actual_hash,
        "records_sha256": _canonical_records_sha256(rows),
        "records": len(rows),
        "grade_distribution": expected_distribution,
        "unique_doc_ids": len(identities["doc_ids"]),
        "unique_family_ids": len(identities["family_ids"]),
        "unique_normalized_text_hashes": len(identities["text_hashes"]),
        "assembly_manifest": {
            "path": str(assembly_manifest_path.resolve()),
            "sha256": _sha256_bytes(manifest_bytes),
            "ready": True,
            "shortcut_gate_passed": True,
        },
    }
    return rows, audit


def load_final_locked_proxy_eval(
    corpus_path: Path, suite_manifest_path: Path
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Load the one-shot final 800 partition of a frozen Proxy suite.

    The companion 200-document development partition is intentionally not
    accepted here.  It is for iteration and model selection only; this loader
    makes accidental re-use of that partition in a final comparison fail.
    """
    rows, corpus_bytes = _read_jsonl(corpus_path, purpose="final locked proxy corpus")
    manifest, manifest_bytes = _read_json_object(
        suite_manifest_path, purpose="final proxy suite manifest"
    )
    if manifest.get("schema") != "frozen-proxy-eval-split-audit-v1":
        raise ProxyComparisonError("final proxy suite manifest schema is invalid")
    if manifest.get("final_documents") != EXPECTED_FINAL_LOCKED_DOCUMENTS:
        raise ProxyComparisonError("final proxy suite must attest exactly 800 records")
    expected_hash = str(manifest.get("final_sha256") or "")
    actual_hash = _sha256_bytes(corpus_bytes)
    if not _SHA256.fullmatch(expected_hash) or expected_hash != actual_hash:
        raise ProxyComparisonError("final proxy corpus SHA-256 does not match suite manifest")
    expected_distribution = {"TS": 160, "S1": 200, "S2": 200, "S3": 240}
    if len(rows) != EXPECTED_FINAL_LOCKED_DOCUMENTS:
        raise ProxyComparisonError(f"final proxy corpus must contain 800 records; found {len(rows)}")
    identities = _required_record_fields(rows, purpose="final locked proxy corpus")
    distribution = Counter(str(row["label"]) for row in rows)
    if dict(distribution) != expected_distribution:
        raise ProxyComparisonError(
            "final proxy grade distribution mismatch: "
            f"expected {expected_distribution}, found {dict(distribution)}"
        )
    for index, row in enumerate(rows):
        if row.get("evaluation_partition") != "final_locked":
            raise ProxyComparisonError(f"final proxy row {index} is not final_locked")
        if row.get("catalog_split_role") != "frozen_proxy_eval_only":
            raise ProxyComparisonError(f"final proxy row {index} has invalid split role")
        if row.get("training_use_permitted") is not False:
            raise ProxyComparisonError(f"final proxy row {index} permits training")
        if row.get("evaluation_use_permitted") is not True:
            raise ProxyComparisonError(f"final proxy row {index} lacks evaluation permission")
    return rows, {
        "path": str(corpus_path.resolve()),
        "file_sha256": actual_hash,
        "records_sha256": _canonical_records_sha256(rows),
        "records": len(rows),
        "grade_distribution": expected_distribution,
        "partition": "final_locked",
        "unique_doc_ids": len(identities["doc_ids"]),
        "unique_family_ids": len(identities["family_ids"]),
        "unique_normalized_text_hashes": len(identities["text_hashes"]),
        "suite_manifest": {
            "path": str(suite_manifest_path.resolve()),
            "sha256": _sha256_bytes(manifest_bytes),
            "development_documents": manifest.get("development_documents"),
            "final_documents": EXPECTED_FINAL_LOCKED_DOCUMENTS,
        },
    }


def load_public_s3_challenge(
    corpus_path: Path,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Load a separate 300-document, evaluation-permitted public S3 challenge."""
    rows, corpus_bytes = _read_jsonl(
        corpus_path, purpose="public S3 overclassification challenge"
    )
    if len(rows) != EXPECTED_PUBLIC_S3_CHALLENGE_DOCUMENTS:
        raise ProxyComparisonError(
            "public S3 challenge must contain exactly "
            f"{EXPECTED_PUBLIC_S3_CHALLENGE_DOCUMENTS} records; found {len(rows)}"
        )
    identities = _required_record_fields(rows, purpose="public S3 challenge")
    failures: list[dict[str, object]] = []
    warning_counts: Counter[str] = Counter()
    licences: Counter[str] = Counter()
    sources: set[str] = set()
    source_hashes: set[str] = set()
    for index, row in enumerate(rows):
        check = validate_proxy_record(
            row, stage="eligible", intended_use="evaluation"
        )
        warning_counts.update(check.warnings)
        row_errors = list(check.errors)
        if row.get("document_origin") != PUBLIC_REAL:
            row_errors.append("challenge_requires_public_real")
        if row.get("label") != "S3":
            row_errors.append("challenge_requires_S3")
        if row.get("evaluation_use_permitted") is not True:
            row_errors.append("challenge_requires_evaluation_permission")
        source_reference = str(row.get("source_reference") or "")
        if not source_reference.startswith("https://"):
            row_errors.append("challenge_requires_https_source_reference")
        source_license = str(row.get("source_license") or "")
        if source_license not in {"KOGL-0", "KOGL-1", "KOGL-AI"}:
            row_errors.append(f"challenge_invalid_KOGL_license:{source_license}")
        for key in ("source_sha256", "license_evidence_sha256"):
            value = str(row.get(key) or "")
            if not _SHA256.fullmatch(value):
                row_errors.append(f"challenge_invalid_sha256:{key}")
        if row_errors and len(failures) < 20:
            failures.append(
                {
                    "index": index,
                    "doc_id": str(row.get("doc_id") or ""),
                    "errors": sorted(set(row_errors)),
                }
            )
        licences[source_license] += 1
        sources.add(source_reference)
        source_hashes.add(str(row.get("source_sha256") or ""))
    if failures:
        raise ProxyComparisonError(
            "public S3 challenge contains ineligible/unverifiable records: "
            + json.dumps(failures, ensure_ascii=False, sort_keys=True)
        )
    audit: dict[str, object] = {
        "path": str(corpus_path.resolve()),
        "file_sha256": _sha256_bytes(corpus_bytes),
        "records_sha256": _canonical_records_sha256(rows),
        "records": len(rows),
        "label": "S3",
        "document_origin": PUBLIC_REAL,
        "intended_use": "evaluation",
        "training_use_inferred": False,
        "unique_doc_ids": len(identities["doc_ids"]),
        "unique_family_ids": len(identities["family_ids"]),
        "unique_normalized_text_hashes": len(identities["text_hashes"]),
        "unique_source_references": len(sources),
        "unique_source_sha256": len(source_hashes),
        "source_licenses": dict(sorted(licences.items())),
        "validation_warning_counts": dict(sorted(warning_counts.items())),
    }
    return rows, audit


def assert_separate_evaluation_corpora(
    primary_rows: Sequence[Mapping[str, object]],
    challenge_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Keep the public challenge outside the matched primary metric population."""
    primary = _required_record_fields(primary_rows, purpose="primary frozen corpus")
    challenge = _required_record_fields(challenge_rows, purpose="public S3 challenge")
    overlaps = {
        "doc_ids": sorted(primary["doc_ids"] & challenge["doc_ids"]),
        "family_ids": sorted(primary["family_ids"] & challenge["family_ids"]),
        "normalized_text_hashes": sorted(
            primary["text_hashes"] & challenge["text_hashes"]
        ),
    }
    if any(overlaps.values()):
        raise ProxyComparisonError(
            "public S3 challenge overlaps the primary frozen corpus: "
            + json.dumps(
                {key: values[:10] for key, values in overlaps.items() if values},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return {
        "checked": True,
        "doc_id_overlap": 0,
        "family_id_overlap": 0,
        "normalized_text_hash_overlap": 0,
        "metrics_combined": False,
    }


def _resolve_attested_artifact(
    manifest_dir: Path, artifact: Mapping[str, object], *, name: str
) -> Path:
    relative = Path(str(artifact.get("path") or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ProxyComparisonError(f"unsafe training artifact path for {name}: {relative}")
    candidate = manifest_dir / relative
    if candidate.is_symlink() or not candidate.is_file():
        raise ProxyComparisonError(f"training artifact is not a regular file: {candidate}")
    root = manifest_dir.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ProxyComparisonError(
            f"training artifact escapes manifest directory: {candidate}"
        ) from exc
    expected_hash = str(artifact.get("sha256") or "")
    if not _SHA256.fullmatch(expected_hash):
        raise ProxyComparisonError(f"training artifact {name} has invalid SHA-256")
    actual_hash = _sha256_file(resolved)
    if expected_hash != actual_hash:
        raise ProxyComparisonError(f"training artifact SHA-256 mismatch: {name}")
    return resolved


def load_training_manifest(
    manifest_path: Path,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Re-read all document splits from one committed proxy-training run."""
    manifest, manifest_bytes = _read_json_object(
        manifest_path, purpose="proxy training manifest"
    )
    if manifest.get("schema_version") != "proxy-training-run-v1":
        raise ProxyComparisonError("unsupported proxy training manifest schema")
    if manifest.get("status") != "complete":
        raise ProxyComparisonError("proxy training manifest status is not complete")

    complete_path = manifest_path.parent / "COMPLETE"
    complete, complete_bytes = _read_json_object(
        complete_path, purpose="proxy training completion marker"
    )
    manifest_sha256 = _sha256_bytes(manifest_bytes)
    if complete.get("manifest_sha256") != manifest_sha256:
        raise ProxyComparisonError("training COMPLETE does not attest manifest SHA-256")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ProxyComparisonError("training manifest is missing artifacts")
    document_keys = (
        "train_documents",
        "validation_documents",
        "calibration_documents",
    )
    rows: list[dict[str, object]] = []
    artifact_audit: dict[str, object] = {}
    for key in document_keys:
        descriptor = artifacts.get(key)
        if not isinstance(descriptor, Mapping):
            raise ProxyComparisonError(f"training manifest is missing artifact {key}")
        artifact_path = _resolve_attested_artifact(
            manifest_path.parent, descriptor, name=key
        )
        artifact_rows, artifact_bytes = _read_jsonl(
            artifact_path, purpose=f"training {key}"
        )
        if descriptor.get("records") != len(artifact_rows):
            raise ProxyComparisonError(f"training artifact record count mismatch: {key}")
        rows.extend(artifact_rows)
        artifact_audit[key] = {
            "path": str(artifact_path),
            "sha256": _sha256_bytes(artifact_bytes),
            "records": len(artifact_rows),
        }
    identities = _required_record_fields(rows, purpose="proxy training documents")
    training_input = manifest.get("inputs")
    if not isinstance(training_input, Mapping):
        raise ProxyComparisonError("training manifest is missing inputs")
    training_stats = training_input.get("training")
    if not isinstance(training_stats, Mapping) or training_stats.get("row_count") != len(rows):
        raise ProxyComparisonError(
            "training document artifacts do not match manifest input row count"
        )
    audit: dict[str, object] = {
        "path": str(manifest_path.resolve()),
        "sha256": manifest_sha256,
        "complete_path": str(complete_path.resolve()),
        "complete_sha256": _sha256_bytes(complete_bytes),
        "records": len(rows),
        "unique_doc_ids": len(identities["doc_ids"]),
        "unique_family_ids": len(identities["family_ids"]),
        "unique_normalized_text_hashes": len(identities["text_hashes"]),
        "artifacts": artifact_audit,
    }
    return rows, audit


def _legacy_training_rows(
    raw_rows: Sequence[Mapping[str, object]], *, split: str, purpose: str
) -> tuple[list[dict[str, object]], dict[str, int], str]:
    """Normalize a historical train/val/test split without inventing provenance."""
    rows: list[dict[str, object]] = []
    derived_doc_ids = 0
    derived_family_ids = 0
    for index, raw in enumerate(raw_rows):
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ProxyComparisonError(f"{purpose} row {index} has invalid text")
        digest = text_hash(text)
        doc_id = raw.get("doc_id")
        if not isinstance(doc_id, str) or not doc_id.strip():
            doc_id = f"legacy:{split}:{index:08d}:{digest}"
            derived_doc_ids += 1
        else:
            doc_id = doc_id.strip()
        family_id = raw.get("document_family_id")
        if not isinstance(family_id, str) or not family_id.strip():
            family_id = f"legacy-family:{digest}"
            derived_family_ids += 1
        else:
            family_id = family_id.strip()
        label = raw.get("label")
        rows.append(
            {
                "doc_id": doc_id,
                "document_family_id": family_id,
                "text": text,
                "label": label if isinstance(label, str) and label.strip() else "legacy",
            }
        )
    canonical = _jsonl_bytes(
        [
            {
                "doc_id": row["doc_id"],
                "document_family_id": row["document_family_id"],
                "normalized_text_hash": text_hash(str(row["text"])),
                "label": row["label"],
            }
            for row in rows
        ]
    )
    return (
        rows,
        {"doc_id": derived_doc_ids, "document_family_id": derived_family_ids},
        _sha256_bytes(canonical),
    )


def _historical_build_manifest_audit(path: Path) -> dict[str, object]:
    """Hash a preserved historical build record and parse JSON when present."""
    if path.is_symlink() or not path.is_file():
        raise ProxyComparisonError(
            f"historical build manifest is not a regular file: {path}"
        )
    try:
        raw = path.read_bytes()
        decoded = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ProxyComparisonError(
            f"cannot read historical build manifest {path}: {exc}"
        ) from exc
    parsed_kind = "opaque_bytes"
    schema_version: str | None = None
    try:
        parsed = _strict_json_loads(decoded, location=str(path))
    except ProxyComparisonError:
        if path.suffix.lower() == ".json":
            raise
    else:
        parsed_kind = type(parsed).__name__
        if isinstance(parsed, Mapping):
            raw_schema = parsed.get("schema_version")
            schema_version = raw_schema if isinstance(raw_schema, str) else None
    return {
        "path": str(path.resolve()),
        "sha256": _sha256_bytes(raw),
        "bytes": len(raw),
        "parsed_kind": parsed_kind,
        "schema_version": schema_version,
    }


def create_legacy_training_corpus_attestation(
    *,
    train_path: Path,
    validation_path: Path,
    test_path: Path,
    model_dir: Path,
    historical_build_manifest_path: Path,
    output_path: Path,
) -> dict[str, object]:
    """Create an immutable-text provenance record for raw-model comparison only."""
    sources = {
        "train": train_path,
        "validation": validation_path,
        "test": test_path,
    }
    split_entries: dict[str, object] = {}
    for split, path in sources.items():
        raw_rows, raw_bytes = _read_jsonl(path, purpose=f"legacy {split} corpus")
        normalized, derived, normalized_hash = _legacy_training_rows(
            raw_rows, split=split, purpose=f"legacy {split} corpus"
        )
        split_entries[split] = {
            "path": str(path.resolve()),
            "file_sha256": _sha256_bytes(raw_bytes),
            "normalized_records_sha256": normalized_hash,
            "records": len(normalized),
            "derived_identity_fields": derived,
        }
    historical_build = _historical_build_manifest_audit(historical_build_manifest_path)
    payload: dict[str, object] = {
        "schema_version": LEGACY_TRAINING_ATTESTATION_SCHEMA,
        "status": "complete",
        "claim_scope": "raw_model_proxy_regression_only_legacy_training_provenance",
        "bundle_operating_point_allowed": False,
        "model": hash_model_directory(model_dir),
        "historical_build_manifest": historical_build,
        "provenance_limitations": [
            "This is historical operator provenance, not cryptographic proof that "
            "the attested corpus executed the recorded training job."
        ],
        "identity_policy": {
            "doc_id": "preserve_when_present_else_derived_from_split_index_and_normalized_text_hash",
            "document_family_id": "preserve_when_present_else_derived_from_normalized_text_hash",
            "normalized_text_hash": "lloydk.hygiene.text_hash",
        },
        "splits": split_entries,
    }
    encoded = _json_bytes(payload, indent=2)
    complete_path = output_path.with_name(f"{output_path.name}.COMPLETE")
    if output_path.exists() or complete_path.exists():
        raise ProxyComparisonError(f"legacy training attestation already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_new(output_path, encoded)
    _atomic_write_new(
        complete_path,
        _json_bytes(
            {
                "schema_version": LEGACY_TRAINING_ATTESTATION_SCHEMA,
                "status": "complete",
                "attestation_sha256": _sha256_bytes(encoded),
            },
            indent=2,
        ),
    )
    return payload


def load_legacy_training_corpus_attestation(
    path: Path,
    *,
    model_dir: Path,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Re-read every declared legacy split and verify immutable text provenance."""
    payload, raw = _read_json_object(path, purpose="legacy training corpus attestation")
    complete, _ = _read_json_object(
        path.with_name(f"{path.name}.COMPLETE"),
        purpose="legacy training corpus completion marker",
    )
    if (
        payload.get("schema_version") != LEGACY_TRAINING_ATTESTATION_SCHEMA
        or payload.get("status") != "complete"
        or payload.get("bundle_operating_point_allowed") is not False
        or complete.get("schema_version") != LEGACY_TRAINING_ATTESTATION_SCHEMA
        or complete.get("status") != "complete"
        or complete.get("attestation_sha256") != _sha256_bytes(raw)
    ):
        raise ProxyComparisonError("legacy training corpus attestation contract is invalid")
    model = payload.get("model")
    if not isinstance(model, Mapping) or not isinstance(model.get("tree_sha256"), str):
        raise ProxyComparisonError("legacy training attestation model binding is invalid")
    actual_model = hash_model_directory(model_dir)
    if actual_model["tree_sha256"] != model.get("tree_sha256"):
        raise ProxyComparisonError(
            "legacy training attestation does not bind the supplied model directory"
        )
    historical_build = payload.get("historical_build_manifest")
    if not isinstance(historical_build, Mapping):
        raise ProxyComparisonError("legacy training historical build manifest is missing")
    historical_path = Path(str(historical_build.get("path") or ""))
    if _historical_build_manifest_audit(historical_path) != dict(historical_build):
        raise ProxyComparisonError(
            "historical build manifest does not match its legacy training attestation"
        )
    limitations = payload.get("provenance_limitations")
    if (
        not isinstance(limitations, list)
        or not limitations
        or not all(isinstance(value, str) and value for value in limitations)
    ):
        raise ProxyComparisonError("legacy training provenance limitations are missing")
    splits = payload.get("splits")
    if not isinstance(splits, Mapping) or set(splits) != {"train", "validation", "test"}:
        raise ProxyComparisonError("legacy training corpus attestation split inventory is invalid")
    rows: list[dict[str, object]] = []
    audit_splits: dict[str, object] = {}
    for split in ("train", "validation", "test"):
        entry = splits[split]
        if not isinstance(entry, Mapping):
            raise ProxyComparisonError(f"legacy training split attestation is invalid: {split}")
        source_path = Path(str(entry.get("path") or ""))
        if source_path.is_symlink() or not source_path.is_file():
            raise ProxyComparisonError(f"legacy training source is not a regular file: {split}")
        raw_rows, source_bytes = _read_jsonl(source_path, purpose=f"legacy {split} corpus")
        normalized, derived, normalized_hash = _legacy_training_rows(
            raw_rows, split=split, purpose=f"legacy {split} corpus"
        )
        if (
            entry.get("file_sha256") != _sha256_bytes(source_bytes)
            or entry.get("normalized_records_sha256") != normalized_hash
            or entry.get("records") != len(normalized)
            or entry.get("derived_identity_fields") != derived
        ):
            raise ProxyComparisonError(
                f"legacy training source does not match its immutable attestation: {split}"
            )
        rows.extend(normalized)
        audit_splits[split] = {
            "path": str(source_path.resolve()),
            "file_sha256": _sha256_bytes(source_bytes),
            "normalized_records_sha256": normalized_hash,
            "records": len(normalized),
            "derived_identity_fields": derived,
        }
    _required_record_fields(rows, purpose="legacy training documents")
    return rows, {
        "path": str(path.resolve()),
        "sha256": _sha256_bytes(raw),
        "schema_version": LEGACY_TRAINING_ATTESTATION_SCHEMA,
        "claim_scope": payload["claim_scope"],
        "splits": audit_splits,
        "legacy_provenance": True,
        "model_tree_sha256": actual_model["tree_sha256"],
        "historical_build_manifest": historical_build,
        "provenance_limitations": limitations,
    }


def assert_no_training_overlap(
    frozen_rows: Sequence[Mapping[str, object]],
    training_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Fail on direct ID, family, or normalized-text overlap."""
    frozen = _required_record_fields(frozen_rows, purpose="frozen proxy corpus")
    training = _required_record_fields(training_rows, purpose="proxy training documents")
    overlap = {
        "doc_ids": sorted(frozen["doc_ids"] & training["doc_ids"]),
        "family_ids": sorted(frozen["family_ids"] & training["family_ids"]),
        "normalized_text_hashes": sorted(
            frozen["text_hashes"] & training["text_hashes"]
        ),
    }
    if any(overlap.values()):
        summary = {key: len(values) for key, values in overlap.items()}
        samples = {key: values[:10] for key, values in overlap.items() if values}
        raise ProxyComparisonError(
            "frozen/train leakage detected: "
            + json.dumps(
                {"counts": summary, "samples": samples},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return {
        "checked": True,
        "unit": ["doc_id", "document_family_id", "normalized_text_hash"],
        "doc_id_overlap": 0,
        "family_id_overlap": 0,
        "normalized_text_hash_overlap": 0,
    }


def hash_model_directory(model_dir: Path) -> dict[str, object]:
    """Return a portable SHA-256 tree manifest and reject symlinks/empty dirs."""
    if model_dir.is_symlink() or not model_dir.is_dir():
        raise ProxyComparisonError(f"model path is not a regular directory: {model_dir}")
    files: list[dict[str, object]] = []
    for path in sorted(model_dir.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise ProxyComparisonError(f"model directory contains a symlink: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(model_dir).as_posix()
        files.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    if not files:
        raise ProxyComparisonError(f"model directory contains no files: {model_dir}")
    canonical = json.dumps(
        files, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "path": str(model_dir.resolve()),
        "tree_sha256": _sha256_bytes(canonical),
        "file_count": len(files),
        "total_bytes": sum(int(item["size"]) for item in files),
        "files": files,
    }


def serving_aggregation_contract(
    *,
    max_length: int = SERVING_MAX_LENGTH,
    chunk_overlap: int = SERVING_CHUNK_OVERLAP,
    severe_codes: Sequence[str] = SERVING_SEVERE_AGG_CODES,
    forward_batch_size: int = SERVING_INFERENCE_BATCH_SIZE,
    apply_bundle_operating_point: bool = False,
    raw_model: bool = False,
    require_fast_overflow: bool = False,
) -> dict[str, object]:
    """Describe the M5 model-component aggregation contract without importing M5."""
    if max_length < 8:
        raise ProxyComparisonError("max_length must be at least 8")
    if not 0 <= chunk_overlap < max_length * SERVING_CHAR_CHUNK_MULTIPLIER:
        raise ProxyComparisonError("chunk_overlap is outside the serving split contract")
    if forward_batch_size < 1:
        raise ProxyComparisonError("forward_batch_size must be positive")
    if apply_bundle_operating_point and raw_model:
        raise ProxyComparisonError("raw model and bundle operating point modes conflict")
    normalized_severe = tuple(str(code) for code in severe_codes)
    if not normalized_severe or not set(normalized_severe) <= set(LABELS):
        raise ProxyComparisonError("severe aggregation codes must be TS/S1/S2/S3")
    splitter_source = Path(_m5_char_split.__code__.co_filename).resolve()
    m5_source = _PROJECT_ROOT / "src" / "lloydk" / "modules" / "m5_inference" / "pipeline.py"
    if not splitter_source.is_file() or not m5_source.is_file():
        raise ProxyComparisonError("serving aggregation reference source is missing")
    try:
        splitter_reference = splitter_source.relative_to(_PROJECT_ROOT).as_posix()
        m5_reference = m5_source.resolve().relative_to(_PROJECT_ROOT).as_posix()
    except ValueError as exc:
        raise ProxyComparisonError(
            "serving aggregation sources are outside the project root"
        ) from exc
    token_stride = min(chunk_overlap, max_length // 4)
    contract: dict[str, object] = {
        "schema_version": "m5-model-aggregation-mirror-v1",
        "evaluation_unit": "document",
        "chunk_rows_are_not_evaluation_samples": True,
        "char_split": {
            "implementation": "lloydk.modules.m2_preprocess.chunker.split",
            "source_path": splitter_reference,
            "source_sha256": _sha256_file(splitter_source),
            "size_formula": "max_length*3",
            "size_chars": max_length * SERVING_CHAR_CHUNK_MULTIPLIER,
            "overlap_chars": chunk_overlap,
        },
        "tokenizer_windows": {
            "fast_tokenizer": {
                "truncation": True,
                "max_length_tokens": max_length,
                "return_overflowing_tokens": True,
                "stride_formula": "min(chunk_overlap,max_length//4)",
                "stride_tokens": token_stride,
            },
            "slow_or_overflow_error_fallback": (
                "one truncated token window per character chunk"
            ),
            "overflow_to_sample_mapping": (
                "each token window inherits its parent character-chunk weight"
            ),
        },
        "probability_aggregation": {
            "window_probability": "softmax(logits/bundle_temperature)",
            "probability_column_labels": "model config.id2label",
            "base": "parent-character-length-weighted mean across token windows",
            "window_weight": "max(len(parent_char_chunk.strip()),1)",
            "overflow_weighting": "parent weight repeated for every overflow window",
            "severe_codes": list(normalized_severe),
            "severe_override": "elementwise max window probability over weighted mean",
            "renormalization": "divide all grade values by their sum",
            "selection": "argmax",
        },
        "temperature_policy": (
            "model-dir temperature.json when present, otherwise identity 1.0; "
            "runtime environment override is intentionally excluded and attested"
        ),
        "excluded_post_model_serving_rules": [
            "rule-engine FNR override",
            "source-prior cap",
            "metadata floor",
            "escalation tau",
        ],
        "forward_batch_size": forward_batch_size,
        "m5_reference": {
            "path": m5_reference,
            "sha256": _sha256_file(m5_source),
            "methods": ["chunk_text", "_encode_windows", "_aggregate_chunk_probs"],
        },
    }
    if apply_bundle_operating_point:
        contract["comparison_mode"] = "bundle_operating_point"
        contract["probability_aggregation"]["selection"] = (  # type: ignore[index]
            "bundle_calibration_tau_or_argmax"
        )
        contract["operating_point_policy"] = (
            "model-dir operating_point.json calibrated on independent "
            "calibration_documents; runtime environment override excluded"
        )
        contract["excluded_post_model_serving_rules"] = [
            value
            for value in contract["excluded_post_model_serving_rules"]  # type: ignore[union-attr]
            if value != "escalation tau"
        ]
    if raw_model:
        contract["temperature_policy"] = (
            "forced identity T=1.0; bundle and runtime environment temperature excluded"
        )
        contract["comparison_mode"] = "raw_model"
    if require_fast_overflow:
        contract["tokenizer_windows"]["slow_or_overflow_error_fallback"] = (  # type: ignore[index]
            "forbidden_fail_closed"
        )
        contract["tokenizer_windows"]["production_fast_overflow_required"] = True  # type: ignore[index]
    canonical = json.dumps(
        contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    contract["contract_sha256"] = _sha256_bytes(canonical)
    return contract


def serving_char_chunks(
    text: str,
    *,
    max_length: int = SERVING_MAX_LENGTH,
    chunk_overlap: int = SERVING_CHUNK_OVERLAP,
) -> list[str]:
    """Use the same character splitter and size formula as M5 ``chunk_text``."""
    chunks = _m5_char_split(
        text,
        size=max_length * SERVING_CHAR_CHUNK_MULTIPLIER,
        overlap=chunk_overlap,
    )
    return [chunk.text for chunk in chunks] or [text]


def aggregate_serving_probabilities(
    window_probabilities: Sequence[Sequence[float]],
    window_weights: Sequence[int | float],
    *,
    severe_codes: Sequence[str] = SERVING_SEVERE_AGG_CODES,
    label_order: Sequence[str] = LABELS,
) -> dict[str, float]:
    """Mirror M5 weighted-mean + severe max-pool + renormalization in pure Python."""
    if not window_probabilities or len(window_probabilities) != len(window_weights):
        raise ProxyComparisonError("window probabilities and weights must be non-empty/matched")
    ordered_labels = tuple(str(label) for label in label_order)
    if len(ordered_labels) != len(LABELS) or set(ordered_labels) != set(LABELS):
        raise ProxyComparisonError("label_order must contain each TS/S1/S2/S3 grade once")
    parsed_rows: list[list[float]] = []
    for row_index, values in enumerate(window_probabilities):
        if len(values) != len(ordered_labels):
            raise ProxyComparisonError(
                f"window probability row {row_index} must have {len(ordered_labels)} values"
            )
        parsed = [float(value) for value in values]
        if any(not math.isfinite(value) or value < 0 for value in parsed):
            raise ProxyComparisonError(f"invalid window probabilities at row {row_index}")
        if not math.isclose(sum(parsed), 1.0, rel_tol=1e-5, abs_tol=1e-5):
            raise ProxyComparisonError(
                f"window probabilities do not sum to one at row {row_index}"
            )
        parsed_rows.append(parsed)
    parsed_weights = [float(weight) for weight in window_weights]
    if any(not math.isfinite(weight) or weight <= 0 for weight in parsed_weights):
        raise ProxyComparisonError("window weights must be finite and positive")
    total_weight = sum(parsed_weights)
    weighted = [
        sum(row[index] * weight for row, weight in zip(parsed_rows, parsed_weights, strict=True))
        / total_weight
        for index in range(len(ordered_labels))
    ]
    severe = {str(code) for code in severe_codes}
    if not severe or not severe <= set(LABELS):
        raise ProxyComparisonError("invalid severe aggregation grade set")
    for index, grade in enumerate(ordered_labels):
        if grade in severe:
            weighted[index] = max(weighted[index], max(row[index] for row in parsed_rows))
    total = sum(weighted)
    if not math.isfinite(total) or total <= 0:
        raise ProxyComparisonError("aggregated document probability has no mass")
    normalized = [value / total for value in weighted]
    return {
        grade: normalized[index] for index, grade in enumerate(ordered_labels)
    }


def _encode_serving_windows(
    tokenizer,
    batch: list[str],
    *,
    max_length: int,
    chunk_overlap: int,
):
    """Exactly mirror M5 fast-tokenizer overflow and truncation fallback behavior."""
    if getattr(tokenizer, "is_fast", False):
        try:
            stride = min(int(chunk_overlap), max_length // 4)
            encoded = tokenizer(
                batch,
                truncation=True,
                max_length=max_length,
                stride=max(0, stride),
                return_overflowing_tokens=True,
                padding=True,
                return_tensors="pt",
            )
            raw_mapping = encoded.pop("overflow_to_sample_mapping")
            sample_mapping = (
                raw_mapping.tolist() if hasattr(raw_mapping, "tolist") else list(raw_mapping)
            )
            if not sample_mapping or any(
                not isinstance(index, int) or not 0 <= index < len(batch)
                for index in sample_mapping
            ):
                raise ValueError("invalid overflow_to_sample_mapping")
            return encoded, sample_mapping, "fast_overflow"
        except Exception:  # noqa: BLE001 - M5 deliberately falls back to truncation
            fallback_mode = "fast_overflow_error_truncation"
    else:
        fallback_mode = "slow_tokenizer_truncation"
    encoded = tokenizer(
        batch,
        truncation=True,
        max_length=max_length,
        padding=True,
        return_tensors="pt",
    )
    return encoded, list(range(len(batch))), fallback_mode


def _load_bundle_temperature(model_dir: Path) -> tuple[float, str, str | None]:
    path = model_dir / "temperature.json"
    if not path.exists():
        return 1.0, "identity_no_bundle", None
    payload, raw = _read_json_object(path, purpose="model temperature artifact")
    value = payload.get("temperature")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProxyComparisonError(f"invalid model temperature in {path}")
    temperature = float(value)
    if not math.isfinite(temperature) or temperature <= 0:
        raise ProxyComparisonError(f"model temperature must be finite and positive: {path}")
    return temperature, "bundle", _sha256_bytes(raw)


def _verify_finalized_model_bundle(model_dir: Path) -> dict[str, object]:
    """Verify finalizer manifest/COMPLETE and every calibration artifact hash."""
    manifest, manifest_raw = _read_json_object(
        model_dir / "finalization_manifest.json",
        purpose="model finalization manifest",
    )
    complete, complete_raw = _read_json_object(
        model_dir / "COMPLETE", purpose="model finalization COMPLETE"
    )
    if (
        manifest.get("schema_version") != "proxy-classifier-finalization-v1"
        or manifest.get("status") != "complete"
        or manifest.get("artifact_role") != "proxy_deployment_candidate"
        or manifest.get("production_eligible") is not False
        or manifest.get("customer_document_deployment_approved") is not False
    ):
        raise ProxyComparisonError(
            f"model is not a restricted finalized proxy candidate: {model_dir}"
        )
    if (
        complete.get("schema_version") != "proxy-classifier-finalization-v1"
        or complete.get("run_id") != manifest.get("run_id")
        or complete.get("manifest_sha256") != _sha256_bytes(manifest_raw)
    ):
        raise ProxyComparisonError(
            f"model finalization manifest/COMPLETE mismatch: {model_dir}"
        )
    artifacts = manifest.get("artifacts")
    complete_artifacts = complete.get("artifacts")
    required = {
        "checkpoint_selection",
        "validation_window_logits",
        "calibration_window_logits",
        "temperature",
        "operating_point",
    }
    if (
        not isinstance(artifacts, Mapping)
        or not isinstance(complete_artifacts, Mapping)
        or set(artifacts) != required
        or dict(complete_artifacts) != dict(artifacts)
    ):
        raise ProxyComparisonError(
            f"model finalization artifact inventory is invalid: {model_dir}"
        )
    for key, entry in artifacts.items():
        if not isinstance(entry, Mapping):
            raise ProxyComparisonError(f"invalid finalized artifact entry {key}")
        filename = entry.get("path")
        digest = entry.get("sha256")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not isinstance(digest, str)
            or not _SHA256.fullmatch(digest)
        ):
            raise ProxyComparisonError(f"invalid finalized artifact path/hash for {key}")
        path = model_dir / filename
        if path.is_symlink() or not path.is_file() or _sha256_file(path) != digest:
            raise ProxyComparisonError(f"finalized artifact hash mismatch for {key}")
        if entry.get("bytes") != path.stat().st_size:
            raise ProxyComparisonError(f"finalized artifact size mismatch for {key}")

    # The finalizer hashes the clean Hugging Face payload before it writes any
    # selection/calibration metadata.  Rebind those exact model bytes here so a
    # valid manifest cannot be copied next to an unrelated model checkpoint.
    clean_model = manifest.get("published_model_before_calibration_metadata")
    if not isinstance(clean_model, Mapping):
        raise ProxyComparisonError(
            f"finalized model payload attestation is missing: {model_dir}"
        )
    clean_files = clean_model.get("files")
    clean_tree = clean_model.get("tree_sha256")
    if (
        not isinstance(clean_files, list)
        or not clean_files
        or not isinstance(clean_tree, str)
        or not _SHA256.fullmatch(clean_tree)
    ):
        raise ProxyComparisonError(
            f"finalized model payload attestation is invalid: {model_dir}"
        )
    rebound_files: list[dict[str, object]] = []
    clean_paths: set[str] = set()
    for entry in clean_files:
        if not isinstance(entry, Mapping):
            raise ProxyComparisonError(
                f"finalized model payload file entry is invalid: {model_dir}"
            )
        relative = entry.get("path")
        size = entry.get("size")
        digest = entry.get("sha256")
        if (
            not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
            or Path(relative).as_posix() != relative
            or ".." in Path(relative).parts
            or relative in clean_paths
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not isinstance(digest, str)
            or not _SHA256.fullmatch(digest)
        ):
            raise ProxyComparisonError(
                f"finalized model payload file entry is invalid: {model_dir}"
            )
        clean_paths.add(relative)
        path = model_dir / Path(relative)
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != size
            or _sha256_file(path) != digest
        ):
            raise ProxyComparisonError(
                f"finalized model payload hash mismatch for {relative}"
            )
        rebound_files.append(
            {"path": relative, "size": size, "sha256": digest}
        )
    rebound_files.sort(key=lambda item: str(item["path"]))
    clean_canonical = json.dumps(
        rebound_files,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if (
        _sha256_bytes(clean_canonical) != clean_tree
        or clean_model.get("file_count") != len(rebound_files)
        or clean_model.get("total_bytes")
        != sum(int(item["size"]) for item in rebound_files)
    ):
        raise ProxyComparisonError(
            f"finalized model payload tree attestation mismatch: {model_dir}"
        )
    metadata_paths = {
        str(entry["path"])
        for entry in artifacts.values()
        if isinstance(entry, Mapping)
    } | {"finalization_manifest.json", "COMPLETE"}
    actual_paths: set[str] = set()
    for path in model_dir.rglob("*"):
        if path.is_symlink():
            raise ProxyComparisonError(
                f"finalized model bundle contains a symlink: {path}"
            )
        if path.is_file():
            actual_paths.add(path.relative_to(model_dir).as_posix())
    expected_paths = clean_paths | metadata_paths
    if actual_paths != expected_paths:
        raise ProxyComparisonError(
            "finalized model bundle file inventory mismatch: "
            + json.dumps(
                {
                    "missing": sorted(expected_paths - actual_paths),
                    "unexpected": sorted(actual_paths - expected_paths),
                },
                sort_keys=True,
            )
        )
    temperature, _ = _read_json_object(
        model_dir / "temperature.json", purpose="model temperature artifact"
    )
    operating_point, _ = _read_json_object(
        model_dir / "operating_point.json", purpose="model operating point artifact"
    )
    calibration = manifest.get("calibration")
    training_manifest_sha256 = temperature.get("training_run_manifest_sha256")
    serving_contract_sha256 = temperature.get("serving_aggregation_contract_sha256")
    if (
        temperature.get("schema_version") != "proxy-document-temperature-v1"
        or temperature.get("status") != "complete"
        or temperature.get("fit_unit") != "document"
        or not isinstance(training_manifest_sha256, str)
        or not _SHA256.fullmatch(training_manifest_sha256)
        or operating_point.get("training_run_manifest_sha256")
        != training_manifest_sha256
        or not isinstance(serving_contract_sha256, str)
        or not _SHA256.fullmatch(serving_contract_sha256)
        or operating_point.get("serving_aggregation_contract_sha256")
        != serving_contract_sha256
        or not isinstance(calibration, Mapping)
        or calibration.get("temperature") != temperature
        or calibration.get("operating_point") != operating_point
    ):
        raise ProxyComparisonError(
            f"calibration artifacts do not match finalization manifest: {model_dir}"
        )
    evaluation_contract = manifest.get("contracts")
    if (
        not isinstance(evaluation_contract, Mapping)
        or evaluation_contract.get("calibration_use")
        != "temperature_and_escalation_tau_only"
        or evaluation_contract.get("frozen_or_blind_tuning_allowed") is not False
    ):
        raise ProxyComparisonError(
            f"finalized model does not prohibit evaluation-set tuning: {model_dir}"
        )
    return {
        "manifest_sha256": _sha256_bytes(manifest_raw),
        "complete_sha256": _sha256_bytes(complete_raw),
        "run_id": manifest["run_id"],
        "calibration_input_sha256": temperature.get("calibration_input_sha256"),
        "calibration_trace_sha256": temperature.get("calibration_trace_sha256"),
        "training_run_manifest_sha256": training_manifest_sha256,
        "serving_aggregation_contract_sha256": serving_contract_sha256,
        "model_payload_tree_sha256": clean_tree,
        "threshold_reselected_on_evaluation_corpora": False,
    }


def _load_bundle_operating_point(
    model_dir: Path,
    *,
    temperature: float,
) -> tuple[
    float | None,
    str,
    str | None,
    str | None,
    str | None,
    str | None,
]:
    """Load tau bound to the same calibration trace as bundled temperature."""
    path = model_dir / "operating_point.json"
    if not path.exists():
        raise ProxyComparisonError(
            f"bundle operating-point mode requires {path}"
        )
    payload, raw = _read_json_object(path, purpose="model operating point artifact")
    if (
        payload.get("schema_version") != "proxy-operating-point-v1"
        or payload.get("status") != "complete"
        or payload.get("selection_split") != "calibration_documents"
        or payload.get("selection_unit") != "document"
    ):
        raise ProxyComparisonError(f"invalid model operating point contract in {path}")
    fitted_temperature = payload.get("temperature")
    if (
        isinstance(fitted_temperature, bool)
        or not isinstance(fitted_temperature, (int, float))
        or not math.isfinite(float(fitted_temperature))
        or not math.isclose(
            float(fitted_temperature), temperature, rel_tol=1e-9, abs_tol=1e-12
        )
    ):
        raise ProxyComparisonError(
            f"operating point was not fitted at the bundled temperature in {path}"
        )
    temperature_payload, _ = _read_json_object(
        model_dir / "temperature.json", purpose="model temperature artifact"
    )
    if (
        temperature_payload.get("schema_version")
        != "proxy-document-temperature-v1"
        or temperature_payload.get("status") != "complete"
        or temperature_payload.get("fit_unit") != "document"
    ):
        raise ProxyComparisonError(
            f"invalid finalized temperature contract in {model_dir}"
        )
    temperature_trace = temperature_payload.get("calibration_trace_sha256")
    if (
        not isinstance(temperature_trace, str)
        or not _SHA256.fullmatch(temperature_trace)
        or payload.get("calibration_trace_sha256") != temperature_trace
    ):
        raise ProxyComparisonError(
            f"temperature and operating point use different calibration traces in {model_dir}"
        )
    calibration_input = temperature_payload.get("calibration_input_sha256")
    if (
        not isinstance(calibration_input, str)
        or not _SHA256.fullmatch(calibration_input)
        or payload.get("calibration_input_sha256") != calibration_input
    ):
        raise ProxyComparisonError(
            f"temperature and operating point use different calibration inputs in {model_dir}"
        )
    training_manifest = temperature_payload.get("training_run_manifest_sha256")
    if (
        not isinstance(training_manifest, str)
        or not _SHA256.fullmatch(training_manifest)
        or payload.get("training_run_manifest_sha256") != training_manifest
    ):
        raise ProxyComparisonError(
            "temperature and operating point use different materialized training "
            f"manifests in {model_dir}"
        )
    tau = payload.get("classifier_escalation_tau")
    if tau is None:
        return (
            None,
            "bundle_argmax",
            _sha256_bytes(raw),
            temperature_trace,
            calibration_input,
            training_manifest,
        )
    if (
        isinstance(tau, bool)
        or not isinstance(tau, (int, float))
        or not math.isfinite(float(tau))
        or not 0.0 < float(tau) < 1.0
    ):
        raise ProxyComparisonError(f"invalid escalation tau in {path}")
    return (
        float(tau),
        "bundle_tau",
        _sha256_bytes(raw),
        temperature_trace,
        calibration_input,
        training_manifest,
    )


def _select_with_operating_point(scores: Mapping[str, float], tau: float | None) -> str:
    if tau is not None:
        for grade in LABELS:
            if float(scores[grade]) >= tau:
                return grade
    return max(LABELS, key=lambda grade: float(scores[grade]))


def predict_model(
    model_dir: Path,
    rows: Sequence[Mapping[str, object]],
    *,
    batch_size: int = SERVING_INFERENCE_BATCH_SIZE,
    device: str = "auto",
    max_length: int = SERVING_MAX_LENGTH,
    chunk_overlap: int = SERVING_CHUNK_OVERLAP,
    severe_codes: Sequence[str] = SERVING_SEVERE_AGG_CODES,
    apply_bundle_operating_point: bool = False,
    raw_model: bool = False,
    require_fast_overflow: bool = False,
) -> ModelPredictionBatch:
    """Run M5-faithful chunk/window aggregation and return document predictions."""
    contract = serving_aggregation_contract(
        max_length=max_length,
        chunk_overlap=chunk_overlap,
        severe_codes=severe_codes,
        forward_batch_size=batch_size,
        apply_bundle_operating_point=apply_bundle_operating_point,
        raw_model=raw_model,
        require_fast_overflow=require_fast_overflow,
    )
    if device not in {"auto", "cpu", "cuda"}:
        raise ProxyComparisonError("device must be auto, cpu, or cuda")
    try:
        import torch
        import torch.nn.functional as functional
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError as exc:
        raise ProxyComparisonError(
            "torch and transformers are required for model comparison"
        ) from exc

    if device == "cuda" and not torch.cuda.is_available():
        raise ProxyComparisonError("CUDA was requested but is unavailable")
    selected_device = "cuda" if device == "auto" and torch.cuda.is_available() else device
    if selected_device == "auto":
        selected_device = "cpu"

    tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
    model = AutoModelForSequenceClassification.from_pretrained(str(model_dir))
    id2label = {int(key): str(value) for key, value in model.config.id2label.items()}
    expected_ids = set(range(len(LABELS)))
    if set(id2label) != expected_ids or set(id2label.values()) != set(LABELS):
        raise ProxyComparisonError(
            f"model label mapping must be exactly {LABELS}; found {id2label}"
        )
    label_order = tuple(id2label[index] for index in sorted(id2label))
    finalization_attestation: dict[str, object] | None = None
    if raw_model:
        temperature, temperature_source, temperature_sha256 = (
            1.0,
            "forced_identity_raw_model",
            None,
        )
    else:
        temperature, temperature_source, temperature_sha256 = _load_bundle_temperature(
            model_dir
        )
    escalation_tau: float | None = None
    operating_point_source = "excluded"
    operating_point_sha256: str | None = None
    operating_point_trace_sha256: str | None = None
    operating_point_input_sha256: str | None = None
    operating_point_training_manifest_sha256: str | None = None
    if apply_bundle_operating_point:
        finalization_attestation = _verify_finalized_model_bundle(model_dir)
        if temperature_source != "bundle":
            raise ProxyComparisonError(
                "bundle operating-point mode requires bundled temperature.json"
            )
        (
            escalation_tau,
            operating_point_source,
            operating_point_sha256,
            operating_point_trace_sha256,
            operating_point_input_sha256,
            operating_point_training_manifest_sha256,
        ) = _load_bundle_operating_point(model_dir, temperature=temperature)
    model = model.to(selected_device)
    model.eval()
    predictions: list[dict[str, object]] = []
    mode_counts: Counter[str] = Counter()
    total_char_chunks = 0
    total_token_windows = 0
    multi_chunk_documents = 0
    overflow_expanded_documents = 0
    max_char_chunks = 0
    max_token_windows = 0
    try:
        with torch.no_grad():
            for row in rows:
                char_chunks = serving_char_chunks(
                    str(row["text"]),
                    max_length=max_length,
                    chunk_overlap=chunk_overlap,
                )
                chunk_weights = [max(len(value.strip()), 1) for value in char_chunks]
                window_probabilities: list[list[float]] = []
                window_weights: list[int] = []
                document_modes: Counter[str] = Counter()
                for start in range(0, len(char_chunks), batch_size):
                    batch = char_chunks[start : start + batch_size]
                    encoded, sample_mapping, mode = _encode_serving_windows(
                        tokenizer,
                        batch,
                        max_length=max_length,
                        chunk_overlap=chunk_overlap,
                    )
                    if require_fast_overflow and mode != "fast_overflow":
                        raise ProxyComparisonError(
                            "production comparison requires fast tokenizer overflow; "
                            f"document {row.get('doc_id')!r} used {mode}"
                        )
                    document_modes[mode] += 1
                    mode_counts[mode] += 1
                    encoded = encoded.to(selected_device)
                    logits = model(**encoded).logits
                    if temperature != 1.0:
                        logits = logits / temperature
                    probabilities = functional.softmax(logits, dim=-1).cpu().tolist()
                    if len(probabilities) != len(sample_mapping):
                        raise ProxyComparisonError(
                            "tokenizer overflow mapping does not match model windows"
                        )
                    window_probabilities.extend(probabilities)
                    window_weights.extend(
                        chunk_weights[start + parent_index]
                        for parent_index in sample_mapping
                    )
                scores = aggregate_serving_probabilities(
                    window_probabilities,
                    window_weights,
                    severe_codes=severe_codes,
                    label_order=label_order,
                )
                label = _select_with_operating_point(scores, escalation_tau)
                char_chunk_count = len(char_chunks)
                token_window_count = len(window_probabilities)
                total_char_chunks += char_chunk_count
                total_token_windows += token_window_count
                multi_chunk_documents += int(char_chunk_count > 1)
                overflow_expanded_documents += int(token_window_count > char_chunk_count)
                max_char_chunks = max(max_char_chunks, char_chunk_count)
                max_token_windows = max(max_token_windows, token_window_count)
                predictions.append(
                    {
                        "label": label,
                        "confidence": scores[label],
                        "scores": scores,
                        "aggregation_trace": {
                            "char_chunk_count": char_chunk_count,
                            "token_window_count": token_window_count,
                            "tokenizer_mode_counts": dict(sorted(document_modes.items())),
                        },
                    }
                )
    finally:
        del model
        if selected_device == "cuda":
            torch.cuda.empty_cache()
    runtime: dict[str, object] = {
        "aggregation_contract_sha256": contract["contract_sha256"],
        "documents": len(rows),
        "document_level_predictions": len(predictions),
        "total_character_chunks": total_char_chunks,
        "total_token_windows": total_token_windows,
        "documents_with_multiple_character_chunks": multi_chunk_documents,
        "documents_with_overflow_expansion": overflow_expanded_documents,
        "max_character_chunks_per_document": max_char_chunks,
        "max_token_windows_per_document": max_token_windows,
        "tokenizer": {
            "class": type(tokenizer).__name__,
            "is_fast": bool(getattr(tokenizer, "is_fast", False)),
            "mode_counts": dict(sorted(mode_counts.items())),
        },
        "temperature": {
            "value": temperature,
            "source": temperature_source,
            "artifact_sha256": temperature_sha256,
            "environment_override_applied": False,
        },
        "operating_point": {
            "applied": apply_bundle_operating_point,
            "classifier_escalation_tau": escalation_tau,
            "source": operating_point_source,
            "artifact_sha256": operating_point_sha256,
            "selection_split": (
                "calibration_documents"
                if operating_point_source in {"bundle_tau", "bundle_argmax"}
                else None
            ),
            "calibration_trace_sha256": operating_point_trace_sha256,
            "calibration_input_sha256": operating_point_input_sha256,
            "training_run_manifest_sha256": (
                operating_point_training_manifest_sha256
            ),
            "environment_override_applied": False,
            "reselected_on_evaluation_corpora": False,
        },
        "finalization": finalization_attestation,
        "device": selected_device,
    }
    return ModelPredictionBatch(tuple(predictions), runtime)


def _validate_predictions(
    predictions: Sequence[Mapping[str, object]],
    *,
    expected_count: int,
    model_name: str,
    escalation_tau: float | None = None,
) -> list[str]:
    if len(predictions) != expected_count:
        raise ProxyComparisonError(
            f"{model_name} returned {len(predictions)} predictions; expected {expected_count}"
        )
    labels: list[str] = []
    for index, prediction in enumerate(predictions):
        label = prediction.get("label")
        if label not in GRADE_CODES:
            raise ProxyComparisonError(
                f"{model_name} prediction {index} has invalid label {label!r}"
            )
        confidence = prediction.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ProxyComparisonError(
                f"{model_name} prediction {index} has invalid confidence"
            )
        confidence_value = float(confidence)
        if not math.isfinite(confidence_value) or not 0 <= confidence_value <= 1:
            raise ProxyComparisonError(
                f"{model_name} prediction {index} has non-probability confidence"
            )
        scores = prediction.get("scores")
        if not isinstance(scores, Mapping) or set(scores) != set(LABELS):
            raise ProxyComparisonError(
                f"{model_name} prediction {index} must contain all four grade scores"
            )
        parsed_scores: dict[str, float] = {}
        for grade in LABELS:
            value = scores[grade]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ProxyComparisonError(
                    f"{model_name} prediction {index} has invalid score for {grade}"
                )
            parsed_scores[grade] = float(value)
        if any(
            not math.isfinite(value) or not 0 <= value <= 1
            for value in parsed_scores.values()
        ) or not math.isclose(
            sum(parsed_scores.values()), 1.0, rel_tol=1e-5, abs_tol=1e-5
        ):
            raise ProxyComparisonError(
                f"{model_name} prediction {index} has invalid probability scores"
            )
        expected_label = _select_with_operating_point(parsed_scores, escalation_tau)
        if not math.isclose(
            parsed_scores[str(label)], confidence_value, rel_tol=1e-6, abs_tol=1e-6
        ) or str(label) != expected_label:
            raise ProxyComparisonError(
                f"{model_name} prediction {index} label/confidence disagree with scores"
            )
        labels.append(str(label))
    return labels


def _validate_prediction_batch(
    batch: ModelPredictionBatch,
    *,
    expected_count: int,
    expected_contract_sha256: str,
    model_name: str,
    expect_operating_point_applied: bool = False,
    expect_raw_temperature: bool = False,
    require_fast_overflow: bool = False,
) -> tuple[list[dict[str, object]], list[str], dict[str, object]]:
    if not isinstance(batch, ModelPredictionBatch):
        raise ProxyComparisonError(
            f"{model_name} predictor did not return an aggregation-attested batch"
        )
    predictions = list(batch.predictions)
    runtime = batch.runtime_attestation
    if not isinstance(runtime, Mapping):
        raise ProxyComparisonError(f"{model_name} has no runtime aggregation attestation")
    if runtime.get("aggregation_contract_sha256") != expected_contract_sha256:
        raise ProxyComparisonError(
            f"{model_name} aggregation contract hash does not match evaluator contract"
        )
    if runtime.get("documents") != expected_count or runtime.get(
        "document_level_predictions"
    ) != expected_count:
        raise ProxyComparisonError(f"{model_name} runtime document counts do not match")
    escalation_tau: float | None = None
    operating_point = runtime.get("operating_point")
    if expect_operating_point_applied:
        if not isinstance(operating_point, Mapping):
            raise ProxyComparisonError(
                f"{model_name} operating-point attestation is missing"
            )
        source = operating_point.get("source")
        tau = operating_point.get("classifier_escalation_tau")
        artifact_hash = operating_point.get("artifact_sha256")
        trace_hash = operating_point.get("calibration_trace_sha256")
        input_hash = operating_point.get("calibration_input_sha256")
        training_manifest_hash = operating_point.get(
            "training_run_manifest_sha256"
        )
        if (
            operating_point.get("applied") is not True
            or source not in {"bundle_tau", "bundle_argmax"}
            or operating_point.get("environment_override_applied") is not False
            or operating_point.get("reselected_on_evaluation_corpora") is not False
        ):
            raise ProxyComparisonError(
                f"{model_name} operating-point policy attestation is invalid"
            )
        if source == "bundle_tau":
            if (
                isinstance(tau, bool)
                or not isinstance(tau, (int, float))
                or not math.isfinite(float(tau))
                or not 0.0 < float(tau) < 1.0
            ):
                raise ProxyComparisonError(
                    f"{model_name} bundled escalation tau is invalid"
                )
            escalation_tau = float(tau)
        elif tau is not None:
            raise ProxyComparisonError(
                f"{model_name} argmax operating point must have null tau"
            )
        bundled = True
        if (
            (bundled and not _SHA256.fullmatch(str(artifact_hash)))
            or (bundled and operating_point.get("selection_split") != "calibration_documents")
            or (bundled and not _SHA256.fullmatch(str(trace_hash)))
            or (bundled and not _SHA256.fullmatch(str(input_hash)))
            or (bundled and not _SHA256.fullmatch(str(training_manifest_hash)))
            or (not bundled and artifact_hash is not None)
            or (not bundled and trace_hash is not None)
            or (not bundled and input_hash is not None)
            or (not bundled and training_manifest_hash is not None)
        ):
            raise ProxyComparisonError(
                f"{model_name} operating-point provenance attestation is invalid"
            )
    elif isinstance(operating_point, Mapping) and operating_point.get("applied") is not False:
        raise ProxyComparisonError(
            f"{model_name} applied an operating point in argmax comparison mode"
        )
    labels = _validate_predictions(
        predictions,
        expected_count=expected_count,
        model_name=model_name,
        escalation_tau=escalation_tau,
    )
    char_total = 0
    window_total = 0
    observed_modes: Counter[str] = Counter()
    observed_max_chars = 0
    observed_max_windows = 0
    for index, prediction in enumerate(predictions):
        trace = prediction.get("aggregation_trace")
        if not isinstance(trace, Mapping):
            raise ProxyComparisonError(
                f"{model_name} prediction {index} has no aggregation trace"
            )
        char_count = trace.get("char_chunk_count")
        window_count = trace.get("token_window_count")
        mode_counts = trace.get("tokenizer_mode_counts")
        if (
            isinstance(char_count, bool)
            or not isinstance(char_count, int)
            or char_count < 1
            or isinstance(window_count, bool)
            or not isinstance(window_count, int)
            or window_count < char_count
            or not isinstance(mode_counts, Mapping)
            or not mode_counts
        ):
            raise ProxyComparisonError(
                f"{model_name} prediction {index} has an invalid aggregation trace"
            )
        char_total += char_count
        window_total += window_count
        observed_modes.update(
            {
                str(mode): int(count)
                for mode, count in mode_counts.items()
                if isinstance(count, int) and not isinstance(count, bool) and count > 0
            }
        )
        observed_max_chars = max(observed_max_chars, char_count)
        observed_max_windows = max(observed_max_windows, window_count)
    if runtime.get("total_character_chunks") != char_total or runtime.get(
        "total_token_windows"
    ) != window_total:
        raise ProxyComparisonError(f"{model_name} runtime chunk/window totals do not match")
    tokenizer = runtime.get("tokenizer")
    temperature = runtime.get("temperature")
    if not isinstance(tokenizer, Mapping) or not isinstance(temperature, Mapping):
        raise ProxyComparisonError(
            f"{model_name} runtime tokenizer/temperature attestation is missing"
        )
    allowed_modes = {
        "fast_overflow",
        "fast_overflow_error_truncation",
        "slow_tokenizer_truncation",
    }
    tokenizer_modes = tokenizer.get("mode_counts")
    if (
        not isinstance(tokenizer.get("class"), str)
        or not isinstance(tokenizer.get("is_fast"), bool)
        or not isinstance(tokenizer_modes, Mapping)
        or set(observed_modes) - allowed_modes
        or dict(observed_modes) != dict(tokenizer_modes)
    ):
        raise ProxyComparisonError(f"{model_name} tokenizer mode attestation is invalid")
    if require_fast_overflow and (
        tokenizer.get("is_fast") is not True
        or set(tokenizer_modes) != {"fast_overflow"}
    ):
        raise ProxyComparisonError(
            f"{model_name} did not use fast overflow for every production comparison window"
        )
    temperature_value = temperature.get("value")
    temperature_source = temperature.get("source")
    temperature_hash = temperature.get("artifact_sha256")
    if (
        isinstance(temperature_value, bool)
        or not isinstance(temperature_value, (int, float))
        or not math.isfinite(float(temperature_value))
        or float(temperature_value) <= 0
        or temperature_source
        not in {"bundle", "identity_no_bundle", "forced_identity_raw_model"}
        or temperature.get("environment_override_applied") is not False
        or (temperature_source == "bundle" and not _SHA256.fullmatch(str(temperature_hash)))
        or (
            temperature_source in {"identity_no_bundle", "forced_identity_raw_model"}
            and temperature_hash is not None
        )
    ):
        raise ProxyComparisonError(f"{model_name} temperature attestation is invalid")
    if expect_raw_temperature and (
        temperature_source != "forced_identity_raw_model"
        or not math.isclose(float(temperature_value), 1.0, abs_tol=1e-12)
    ):
        raise ProxyComparisonError(
            f"{model_name} raw-model comparison did not force identity temperature"
        )
    if expect_operating_point_applied and temperature_source != "bundle":
        raise ProxyComparisonError(
            f"{model_name} bundle operating-point mode lacks bundled temperature"
        )
    finalization = runtime.get("finalization")
    if expect_operating_point_applied:
        if (
            not isinstance(finalization, Mapping)
            or not _SHA256.fullmatch(str(finalization.get("manifest_sha256")))
            or not _SHA256.fullmatch(str(finalization.get("complete_sha256")))
            or not _SHA256.fullmatch(
                str(finalization.get("model_payload_tree_sha256"))
            )
            or finalization.get("threshold_reselected_on_evaluation_corpora") is not False
            or finalization.get("calibration_trace_sha256")
            != operating_point.get("calibration_trace_sha256")
            or finalization.get("calibration_input_sha256")
            != operating_point.get("calibration_input_sha256")
            or finalization.get("training_run_manifest_sha256")
            != operating_point.get("training_run_manifest_sha256")
        ):
            raise ProxyComparisonError(
                f"{model_name} finalized-bundle attestation is invalid"
            )
    elif finalization is not None:
        raise ProxyComparisonError(
            f"{model_name} unexpectedly attached finalization evidence in non-bundle mode"
        )
    if runtime.get("max_character_chunks_per_document") != observed_max_chars or runtime.get(
        "max_token_windows_per_document"
    ) != observed_max_windows:
        raise ProxyComparisonError(f"{model_name} runtime maximum counts do not match")
    return predictions, labels, dict(runtime)


def compute_classification_metrics(
    y_true: Sequence[str], y_pred: Sequence[str]
) -> dict[str, object]:
    """Compute exact and directional four-grade classification metrics."""
    if len(y_true) != len(y_pred) or not y_true:
        raise ProxyComparisonError("truth/prediction lengths must match and be non-empty")
    if any(label not in GRADE_CODES for label in [*y_true, *y_pred]):
        raise ProxyComparisonError("truth and predictions must use TS/S1/S2/S3 only")
    confusion = {
        truth: {prediction: 0 for prediction in LABELS} for truth in LABELS
    }
    for truth, prediction in zip(y_true, y_pred, strict=True):
        confusion[truth][prediction] += 1

    per_grade: dict[str, dict[str, object]] = {}
    for grade in LABELS:
        true_positive = confusion[grade][grade]
        support = sum(confusion[grade].values())
        predicted_count = sum(confusion[truth][grade] for truth in LABELS)
        false_positive = predicted_count - true_positive
        false_negative = support - true_positive
        precision = (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else 0.0
        )
        recall = true_positive / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        underclassified = sum(
            confusion[grade][prediction]
            for prediction in LABELS
            if GRADE_SEVERITY[prediction] > GRADE_SEVERITY[grade]
        )
        per_grade[grade] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support,
            "false_negative_count": false_negative,
            "fnr": false_negative / support if support else 0.0,
            "underclassification_count": underclassified,
            "underclassification_rate": underclassified / support if support else 0.0,
        }

    count = len(y_true)
    high_support = sum(1 for truth in y_true if truth in {"TS", "S1"})
    # Combined TS/S1 FNR treats the two high grades as one positive class.  A
    # TS<->S1 confusion remains inside that positive class and is therefore not
    # a combined false negative.  Keep exact-grade error separate so that this
    # safety grouping does not hide boundary confusion.
    high_false_negative = sum(
        1
        for truth, prediction in zip(y_true, y_pred, strict=True)
        if truth in {"TS", "S1"} and prediction not in {"TS", "S1"}
    )
    high_exact_error = sum(
        1
        for truth, prediction in zip(y_true, y_pred, strict=True)
        if truth in {"TS", "S1"} and prediction != truth
    )
    high_under = sum(
        1
        for truth, prediction in zip(y_true, y_pred, strict=True)
        if truth in {"TS", "S1"}
        and GRADE_SEVERITY[prediction] > GRADE_SEVERITY[truth]
    )
    return {
        "sample_count": count,
        "accuracy": sum(
            truth == prediction
            for truth, prediction in zip(y_true, y_pred, strict=True)
        )
        / count,
        "precision_macro": sum(
            float(per_grade[grade]["precision"]) for grade in LABELS
        )
        / len(LABELS),
        "recall_macro": sum(float(per_grade[grade]["recall"]) for grade in LABELS)
        / len(LABELS),
        "f1_macro": sum(float(per_grade[grade]["f1"]) for grade in LABELS)
        / len(LABELS),
        "f1_weighted": sum(
            float(per_grade[grade]["f1"]) * int(per_grade[grade]["support"])
            for grade in LABELS
        )
        / count,
        "ts_s1_false_negative_count": high_false_negative,
        "ts_s1_fnr": high_false_negative / high_support if high_support else 0.0,
        "high_grade_exact_error_count": high_exact_error,
        "high_grade_exact_error_rate": (
            high_exact_error / high_support if high_support else 0.0
        ),
        "high_grade_underclassification_count": high_under,
        "high_grade_underclassification_rate": (
            high_under / high_support if high_support else 0.0
        ),
        "per_grade": per_grade,
        "confusion_matrix": confusion,
    }


def compute_public_s3_challenge_metrics(
    predictions: Sequence[str],
) -> dict[str, object]:
    """Measure public-S3 false positives without blending into primary metrics."""
    if not predictions:
        raise ProxyComparisonError("public S3 challenge predictions are empty")
    if any(label not in GRADE_CODES for label in predictions):
        raise ProxyComparisonError("public S3 predictions contain an invalid grade")
    distribution = Counter(predictions)
    count = len(predictions)
    correct = distribution["S3"]
    false_positives = count - correct
    severity_distance = {"S3": 0, "S2": 1, "S1": 2, "TS": 3}
    distance_counts = Counter(severity_distance[label] for label in predictions)
    severity_sum = sum(
        severity_distance[label] * occurrences
        for label, occurrences in distribution.items()
    )
    return {
        "sample_count": count,
        "truth_label": "S3",
        "s3_recall": correct / count,
        "public_false_positive_count": false_positives,
        "public_false_positive_rate": false_positives / count,
        "overclassification_rate": false_positives / count,
        "mean_overclassification_severity": severity_sum / count,
        "severe_overclassification_count": distribution["TS"] + distribution["S1"],
        "severe_overclassification_rate": (
            distribution["TS"] + distribution["S1"]
        )
        / count,
        "maximum_overclassification_severity": max(
            (severity_distance[label] for label in predictions), default=0
        ),
        "prediction_distribution": {
            grade: distribution[grade] for grade in LABELS
        },
        "overclassification_severity_counts": {
            str(distance): distance_counts[distance] for distance in range(4)
        },
        "severity_scale": {
            "0": "S3 (correct public grade)",
            "1": "S2",
            "2": "S1",
            "3": "TS",
        },
    }


def _flatten_metrics(metrics: Mapping[str, object]) -> dict[str, float]:
    flattened = {
        key: float(metrics[key])
        for key in (
            "accuracy",
            "precision_macro",
            "recall_macro",
            "f1_macro",
            "f1_weighted",
            "ts_s1_fnr",
            "high_grade_exact_error_rate",
            "high_grade_underclassification_rate",
        )
    }
    per_grade = metrics["per_grade"]
    if not isinstance(per_grade, Mapping):
        raise ProxyComparisonError("invalid per-grade metrics")
    for grade in LABELS:
        grade_metrics = per_grade[grade]
        if not isinstance(grade_metrics, Mapping):
            raise ProxyComparisonError(f"invalid metrics for grade {grade}")
        for key in ("precision", "recall", "f1", "fnr", "underclassification_rate"):
            flattened[f"per_grade.{grade}.{key}"] = float(grade_metrics[key])
    return flattened


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ProxyComparisonError("cannot compute a percentile from no values")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return float(ordered[lower] * (1 - fraction) + ordered[upper] * fraction)


def family_cluster_bootstrap(
    rows: Sequence[Mapping[str, object]],
    baseline_predictions: Sequence[str],
    candidate_predictions: Sequence[str],
    *,
    replicates: int = 2_000,
    seed: int = 20260808,
    confidence_level: float = 0.95,
) -> dict[str, object]:
    """Paired percentile CI by resampling whole document families."""
    if replicates < 200:
        raise ProxyComparisonError("bootstrap_replicates must be at least 200")
    if not 0.5 < confidence_level < 1:
        raise ProxyComparisonError("confidence_level must be between 0.5 and 1")
    if not (len(rows) == len(baseline_predictions) == len(candidate_predictions)):
        raise ProxyComparisonError("bootstrap input lengths do not match")
    by_family: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        family = str(row.get("document_family_id") or "").strip()
        if not family:
            raise ProxyComparisonError(f"bootstrap row {index} has no document family")
        by_family[family].append(index)
    families = sorted(by_family)
    if len(families) < 2:
        raise ProxyComparisonError("family-cluster bootstrap needs at least two families")

    truths = [str(row["label"]) for row in rows]
    baseline_full = _flatten_metrics(
        compute_classification_metrics(truths, baseline_predictions)
    )
    candidate_full = _flatten_metrics(
        compute_classification_metrics(truths, candidate_predictions)
    )
    baseline_samples = {key: [] for key in baseline_full}
    candidate_samples = {key: [] for key in candidate_full}
    delta_samples = {key: [] for key in baseline_full}
    generator = random.Random(seed)
    for _ in range(replicates):
        sampled_indices: list[int] = []
        for _family_index in families:
            selected_family = families[generator.randrange(len(families))]
            sampled_indices.extend(by_family[selected_family])
        sampled_truth = [truths[index] for index in sampled_indices]
        sampled_baseline = [baseline_predictions[index] for index in sampled_indices]
        sampled_candidate = [candidate_predictions[index] for index in sampled_indices]
        baseline_metrics = _flatten_metrics(
            compute_classification_metrics(sampled_truth, sampled_baseline)
        )
        candidate_metrics = _flatten_metrics(
            compute_classification_metrics(sampled_truth, sampled_candidate)
        )
        for key in baseline_full:
            baseline_samples[key].append(baseline_metrics[key])
            candidate_samples[key].append(candidate_metrics[key])
            delta_samples[key].append(candidate_metrics[key] - baseline_metrics[key])

    tail = (1 - confidence_level) / 2

    def summarize(
        estimates: Mapping[str, float], samples: Mapping[str, Sequence[float]]
    ) -> dict[str, object]:
        return {
            key: {
                "estimate": estimates[key],
                "lower": _percentile(samples[key], tail),
                "upper": _percentile(samples[key], 1 - tail),
            }
            for key in estimates
        }

    delta_estimates = {
        key: candidate_full[key] - baseline_full[key] for key in baseline_full
    }
    return {
        "method": "paired_family_cluster_percentile_bootstrap",
        "resampling_unit": "document_family_id",
        "family_count": len(families),
        "replicates": replicates,
        "seed": seed,
        "confidence_level": confidence_level,
        "baseline": summarize(baseline_full, baseline_samples),
        "candidate": summarize(candidate_full, candidate_samples),
        "candidate_minus_baseline": summarize(delta_estimates, delta_samples),
    }


def _prediction_rows(
    rows: Sequence[Mapping[str, object]],
    predictions: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row, prediction in zip(rows, predictions, strict=True):
        result.append(
            {
                "doc_id": row["doc_id"],
                "document_family_id": row["document_family_id"],
                "text_sha1_normalized": text_hash(row["text"]),
                "truth": row["label"],
                "prediction": prediction["label"],
                "confidence": prediction.get("confidence"),
                "scores": prediction.get("scores"),
                "aggregation_trace": prediction.get("aggregation_trace"),
            }
        )
    return result


def _json_bytes(value: object, *, indent: int | None = None) -> bytes:
    separators = None if indent else (",", ":")
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            indent=indent,
            separators=separators,
        )
        + "\n"
    ).encode("utf-8")


def _jsonl_bytes(rows: Sequence[Mapping[str, object]]) -> bytes:
    return b"".join(_json_bytes(dict(row)) for row in rows)


def _render_markdown(report: Mapping[str, object]) -> bytes:
    baseline = report["models"]["baseline"]["metrics"]  # type: ignore[index]
    candidate = report["models"]["candidate"]["metrics"]  # type: ignore[index]
    lines = [
        "# Frozen Proxy Model Comparison",
        "",
        "Primary metrics measure regression behavior on the fixed proxy corpus only.",
        "It is not evidence of accuracy on customer or production documents.",
        "",
        "| Metric | Baseline | Candidate | Delta |",
        "|---|---:|---:|---:|",
    ]
    for key in (
        "f1_macro",
        "f1_weighted",
        "ts_s1_fnr",
        "high_grade_exact_error_rate",
        "high_grade_underclassification_rate",
    ):
        before = float(baseline[key])  # type: ignore[index]
        after = float(candidate[key])  # type: ignore[index]
        lines.append(f"| {key} | {before:.4f} | {after:.4f} | {after-before:+.4f} |")
    lines += [
        "",
        "## Per-grade metrics",
        "",
        "| Model | Grade | Precision | Recall | F1 | FNR | Underclass rate | N |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for model_name, metrics in (("baseline", baseline), ("candidate", candidate)):
        for grade in LABELS:
            item = metrics["per_grade"][grade]  # type: ignore[index]
            lines.append(
                f"| {model_name} | {grade} | {item['precision']:.4f} | "
                f"{item['recall']:.4f} | {item['f1']:.4f} | {item['fnr']:.4f} | "
                f"{item['underclassification_rate']:.4f} | {item['support']} |"
            )
    public_challenge = report.get("public_s3_challenge")
    if isinstance(public_challenge, Mapping) and public_challenge.get("present") is True:
        metrics = public_challenge.get("metrics")
        if not isinstance(metrics, Mapping):
            raise ProxyComparisonError("public S3 report metrics are missing")
        public_baseline = metrics.get("baseline")
        public_candidate = metrics.get("candidate")
        if not isinstance(public_baseline, Mapping) or not isinstance(
            public_candidate, Mapping
        ):
            raise ProxyComparisonError("public S3 model metrics are missing")
        lines += [
            "",
            "## Separate public-real S3 overclassification challenge",
            "",
            "These 300 public-real S3 documents are not included in any primary ",
            "metric or bootstrap interval. They measure false positive and ",
            "overclassification behavior only.",
            "",
            "| Metric | Baseline | Candidate | Delta |",
            "|---|---:|---:|---:|",
        ]
        for key in (
            "s3_recall",
            "public_false_positive_rate",
            "mean_overclassification_severity",
            "severe_overclassification_rate",
        ):
            before = float(public_baseline[key])
            after = float(public_candidate[key])
            lines.append(
                f"| {key} | {before:.4f} | {after:.4f} | {after-before:+.4f} |"
            )
    lines += [
        "",
        "Family-cluster bootstrap confidence intervals and complete hashes are in ",
        "`comparison.json`.",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _atomic_write_new(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"proxy-compare-{stamp}-{uuid.uuid4().hex[:10]}"


Predictor = Callable[..., ModelPredictionBatch]


def compare_proxy_models(
    *,
    frozen_corpus_path: Path,
    frozen_manifest_path: Path | None,
    baseline_model_dir: Path,
    candidate_model_dir: Path,
    output_root: Path,
    baseline_training_manifest_path: Path | None = None,
    candidate_training_manifest_path: Path | None = None,
    baseline_legacy_training_attestation_path: Path | None = None,
    candidate_legacy_training_attestation_path: Path | None = None,
    public_s3_challenge_path: Path | None = None,
    final_suite_manifest_path: Path | None = None,
    run_id: str | None = None,
    batch_size: int = SERVING_INFERENCE_BATCH_SIZE,
    device: str = "auto",
    bootstrap_replicates: int = 2_000,
    bootstrap_seed: int = 20260808,
    predictor: Predictor | None = None,
    apply_bundle_operating_point: bool = False,
    comparison_mode: str | None = None,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    """Compare baseline/candidate and atomically publish a hash-attested run."""
    if comparison_mode not in {"raw_model", "bundle_operating_point"}:
        raise ProxyComparisonError(
            "production comparison requires explicit comparison_mode raw_model or "
            "bundle_operating_point; legacy_model_component is diagnostic-only"
        )
    effective_mode = comparison_mode
    if apply_bundle_operating_point and effective_mode != "bundle_operating_point":
        raise ProxyComparisonError(
            "apply_bundle_operating_point conflicts with comparison_mode"
        )
    apply_operating_point = effective_mode == "bundle_operating_point"
    raw_model = effective_mode == "raw_model"
    require_fast_overflow = effective_mode in {"raw_model", "bundle_operating_point"}
    if batch_size != SERVING_INFERENCE_BATCH_SIZE:
        raise ProxyComparisonError(
            "production comparison batch_size must match M5 serving "
            f"({SERVING_INFERENCE_BATCH_SIZE})"
        )
    run_id_value = run_id or _new_run_id()
    if not _SAFE_RUN_ID.fullmatch(run_id_value):
        raise ProxyComparisonError(f"unsafe run id: {run_id_value!r}")
    final_dir = output_root / run_id_value
    if final_dir.exists():
        raise ProxyComparisonError(f"comparison run already exists: {final_dir}")

    if final_suite_manifest_path is not None:
        if frozen_manifest_path is not None:
            raise ProxyComparisonError(
                "supply either frozen assembly manifest or final suite manifest, not both"
            )
        rows, frozen_audit = load_final_locked_proxy_eval(
            frozen_corpus_path, final_suite_manifest_path
        )
    else:
        if frozen_manifest_path is None:
            raise ProxyComparisonError(
                "comparison requires frozen assembly manifest or final suite manifest"
            )
        rows, frozen_audit = load_frozen_proxy_gold(
            frozen_corpus_path, frozen_manifest_path
        )
    public_s3_rows: list[dict[str, object]] = []
    public_s3_audit: dict[str, object] | None = None
    public_s3_separation: dict[str, object] = {
        "checked": False,
        "reason": "no_public_s3_challenge_supplied",
        "metrics_combined": False,
    }
    if public_s3_challenge_path is not None:
        public_s3_rows, public_s3_audit = load_public_s3_challenge(
            public_s3_challenge_path
        )
        public_s3_separation = assert_separate_evaluation_corpora(
            rows, public_s3_rows
        )
    inference_rows = [*rows, *public_s3_rows]
    def load_model_training_provenance(
        model_name: str,
        model_dir: Path,
        manifest_path: Path | None,
        legacy_path: Path | None,
    ) -> tuple[list[dict[str, object]], dict[str, object], bool]:
        if (manifest_path is None) == (legacy_path is None):
            raise ProxyComparisonError(
                f"{model_name} requires exactly one training manifest or legacy "
                "training corpus attestation"
            )
        if legacy_path is not None:
            if effective_mode != "raw_model":
                raise ProxyComparisonError(
                    "legacy training corpus attestations are permitted only for "
                    "raw_model comparison"
                )
            rows, audit = load_legacy_training_corpus_attestation(
                legacy_path, model_dir=model_dir
            )
            return rows, audit, True
        assert manifest_path is not None
        rows, audit = load_training_manifest(manifest_path)
        # A materialized run alone is not provenance for an arbitrary directory
        # that happens to be supplied as a model.  Verify the finalized proxy
        # candidate before any inference, even in raw-model mode where its
        # temperature and operating point are deliberately excluded.
        finalization = _verify_finalized_model_bundle(model_dir)
        if finalization.get("training_run_manifest_sha256") != audit["sha256"]:
            raise ProxyComparisonError(
                f"{model_name} finalized proxy candidate is not bound to its "
                "supplied training manifest"
            )
        audit = {
            **audit,
            "finalized_model": finalization,
        }
        return rows, audit, False

    (
        baseline_training_rows,
        baseline_training_audit,
        baseline_is_legacy,
    ) = load_model_training_provenance(
        "baseline",
        baseline_model_dir,
        baseline_training_manifest_path,
        baseline_legacy_training_attestation_path,
    )
    (
        candidate_training_rows,
        candidate_training_audit,
        candidate_is_legacy,
    ) = load_model_training_provenance(
        "candidate",
        candidate_model_dir,
        candidate_training_manifest_path,
        candidate_legacy_training_attestation_path,
    )
    leakage_audit = {
        "baseline": assert_no_training_overlap(rows, baseline_training_rows),
        "candidate": assert_no_training_overlap(rows, candidate_training_rows),
    }
    public_s3_training_leakage_audit: dict[str, object] = {
        "baseline": (
            assert_no_training_overlap(public_s3_rows, baseline_training_rows)
            if public_s3_rows
            else {"checked": True, "reason": "no_public_s3_challenge_supplied"}
        ),
        "candidate": (
            assert_no_training_overlap(public_s3_rows, candidate_training_rows)
            if public_s3_rows
            else {"checked": True, "reason": "no_public_s3_challenge_supplied"}
        ),
    }
    training_audit: dict[str, object] = {
        "baseline": baseline_training_audit,
        "candidate": candidate_training_audit,
    }
    uses_legacy_training_provenance = baseline_is_legacy or candidate_is_legacy

    baseline_hash = hash_model_directory(baseline_model_dir)
    candidate_hash = hash_model_directory(candidate_model_dir)
    if baseline_hash["tree_sha256"] == candidate_hash["tree_sha256"]:
        raise ProxyComparisonError(
            "baseline and candidate model artifact hashes are identical"
        )

    aggregation_contract = serving_aggregation_contract(
        max_length=SERVING_MAX_LENGTH,
        chunk_overlap=SERVING_CHUNK_OVERLAP,
        severe_codes=SERVING_SEVERE_AGG_CODES,
        forward_batch_size=batch_size,
        apply_bundle_operating_point=apply_operating_point,
        raw_model=raw_model,
        require_fast_overflow=require_fast_overflow,
    )
    contract_sha256 = str(aggregation_contract["contract_sha256"])
    effective_predictor = predictor or predict_model
    baseline_batch = effective_predictor(
        baseline_model_dir,
        inference_rows,
        batch_size=batch_size,
        device=device,
        max_length=SERVING_MAX_LENGTH,
        chunk_overlap=SERVING_CHUNK_OVERLAP,
        severe_codes=SERVING_SEVERE_AGG_CODES,
        apply_bundle_operating_point=apply_operating_point,
        raw_model=raw_model,
        require_fast_overflow=require_fast_overflow,
    )
    baseline_all_raw, baseline_all_labels, baseline_runtime = _validate_prediction_batch(
        baseline_batch,
        expected_count=len(inference_rows),
        expected_contract_sha256=contract_sha256,
        model_name="baseline",
        expect_operating_point_applied=apply_operating_point,
        expect_raw_temperature=raw_model,
        require_fast_overflow=require_fast_overflow,
    )
    candidate_batch = effective_predictor(
        candidate_model_dir,
        inference_rows,
        batch_size=batch_size,
        device=device,
        max_length=SERVING_MAX_LENGTH,
        chunk_overlap=SERVING_CHUNK_OVERLAP,
        severe_codes=SERVING_SEVERE_AGG_CODES,
        apply_bundle_operating_point=apply_operating_point,
        raw_model=raw_model,
        require_fast_overflow=require_fast_overflow,
    )
    candidate_all_raw, candidate_all_labels, candidate_runtime = _validate_prediction_batch(
        candidate_batch,
        expected_count=len(inference_rows),
        expected_contract_sha256=contract_sha256,
        model_name="candidate",
        expect_operating_point_applied=apply_operating_point,
        expect_raw_temperature=raw_model,
        require_fast_overflow=require_fast_overflow,
    )
    if apply_operating_point:
        if baseline_is_legacy or candidate_is_legacy:
            raise ProxyComparisonError(
                "bundle operating-point comparison requires full proxy-training "
                "manifests for both models"
            )
        baseline_operating = baseline_runtime["operating_point"]
        candidate_operating = candidate_runtime["operating_point"]
        if (
            baseline_operating["calibration_input_sha256"]
            != candidate_operating["calibration_input_sha256"]
        ):
            raise ProxyComparisonError(
                "full-bundle A/B requires both models to use the same independent "
                "calibration split"
            )
        for model_name, runtime, audit in (
            ("baseline", baseline_runtime, baseline_training_audit),
            ("candidate", candidate_runtime, candidate_training_audit),
        ):
            finalization = runtime.get("finalization")
            if not isinstance(finalization, Mapping) or (
                finalization.get("training_run_manifest_sha256") != audit["sha256"]
            ):
                raise ProxyComparisonError(
                    f"{model_name} finalized bundle is not bound to its supplied "
                    "training manifest"
                )
    # Detect a model directory being changed while inference is in progress.
    # The recorded digest must describe exactly the bytes used for both passes.
    if hash_model_directory(baseline_model_dir)["tree_sha256"] != baseline_hash["tree_sha256"]:
        raise ProxyComparisonError("baseline model changed during comparison")
    if hash_model_directory(candidate_model_dir)["tree_sha256"] != candidate_hash["tree_sha256"]:
        raise ProxyComparisonError("candidate model changed during comparison")
    primary_count = len(rows)
    baseline_raw = baseline_all_raw[:primary_count]
    baseline_labels = baseline_all_labels[:primary_count]
    candidate_raw = candidate_all_raw[:primary_count]
    candidate_labels = candidate_all_labels[:primary_count]
    baseline_public_raw = baseline_all_raw[primary_count:]
    baseline_public_labels = baseline_all_labels[primary_count:]
    candidate_public_raw = candidate_all_raw[primary_count:]
    candidate_public_labels = candidate_all_labels[primary_count:]
    scope_document_counts = {
        "primary_frozen": primary_count,
        "public_s3_challenge": len(public_s3_rows),
        "total_inference": len(inference_rows),
    }
    baseline_runtime["scope_document_counts"] = scope_document_counts
    candidate_runtime["scope_document_counts"] = scope_document_counts
    truths = [str(row["label"]) for row in rows]
    baseline_metrics = compute_classification_metrics(truths, baseline_labels)
    candidate_metrics = compute_classification_metrics(truths, candidate_labels)
    bootstrap = family_cluster_bootstrap(
        rows,
        baseline_labels,
        candidate_labels,
        replicates=bootstrap_replicates,
        seed=bootstrap_seed,
    )

    baseline_output = _prediction_rows(rows, baseline_raw)
    candidate_output = _prediction_rows(rows, candidate_raw)
    baseline_bytes = _jsonl_bytes(baseline_output)
    candidate_bytes = _jsonl_bytes(candidate_output)
    baseline_public_output: list[dict[str, object]] = []
    candidate_public_output: list[dict[str, object]] = []
    baseline_public_bytes: bytes | None = None
    candidate_public_bytes: bytes | None = None
    public_s3_report: dict[str, object] = {
        "present": False,
        "included_in_primary_metrics": False,
        "reason": "no_public_s3_challenge_supplied",
    }
    if public_s3_rows:
        baseline_public_metrics = compute_public_s3_challenge_metrics(
            baseline_public_labels
        )
        candidate_public_metrics = compute_public_s3_challenge_metrics(
            candidate_public_labels
        )
        public_delta_keys = (
            "s3_recall",
            "public_false_positive_rate",
            "overclassification_rate",
            "mean_overclassification_severity",
            "severe_overclassification_rate",
        )
        public_s3_report = {
            "present": True,
            "included_in_primary_metrics": False,
            "claim_scope": "public_real_s3_overclassification_challenge_only",
            "prohibited_interpretation": (
                "These all-S3 public documents cannot measure four-grade accuracy, "
                "high-grade recall, or customer-document accuracy."
            ),
            "input": public_s3_audit,
            "separation_from_primary": public_s3_separation,
            "metrics": {
                "baseline": baseline_public_metrics,
                "candidate": candidate_public_metrics,
                "candidate_minus_baseline": {
                    key: float(candidate_public_metrics[key])
                    - float(baseline_public_metrics[key])
                    for key in public_delta_keys
                },
            },
        }
        baseline_public_output = _prediction_rows(
            public_s3_rows, baseline_public_raw
        )
        candidate_public_output = _prediction_rows(
            public_s3_rows, candidate_public_raw
        )
        baseline_public_bytes = _jsonl_bytes(baseline_public_output)
        candidate_public_bytes = _jsonl_bytes(candidate_public_output)
    evaluator_path = Path(__file__).resolve()
    cli_path = _PROJECT_ROOT / "scripts" / "compare_proxy_models.py"
    if not cli_path.is_file():
        raise ProxyComparisonError(f"comparison CLI source is missing: {cli_path}")
    report: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id_value,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "claim_scope": (
            "raw_model_frozen_proxy_regression_only_legacy_training_provenance_"
            "not_customer_real_accuracy"
            if uses_legacy_training_provenance
            else CLAIM_SCOPE
        ),
        "human_reviewed_customer_documents": False,
        "prohibited_interpretation": (
            "This is not customer-document or production accuracy evidence."
        ),
        "valid_interpretation": (
            "Paired regression, calibration, and safety-direction comparison on the "
            "fixed proxy corpus only."
        ),
        "evaluation_contract": {
            "frozen_records": len(rows),
            "expected_grade_distribution": dict(frozen_audit["grade_distribution"]),
            "prediction_mode": (
                "m5_chunked_severe_aggregate_bundle_operating_point"
                if apply_operating_point
                else "m5_chunked_severe_aggregate_raw_T1_argmax"
            ),
            "comparison_mode": effective_mode,
            "legacy_training_provenance": uses_legacy_training_provenance,
            "legacy_mode": False,
            "bundle_operating_point_applied": apply_operating_point,
            "forced_identity_temperature": raw_model,
            "fast_tokenizer_overflow_required": require_fast_overflow,
            "operating_point_tuning_corpora": (
                "independent_calibration_documents_only"
                if apply_operating_point
                else "not_applicable_argmax_mode"
            ),
            "operating_point_reselected_on_frozen_or_public_evaluation": False,
            "evaluation_unit": "document",
            "validation_rows_remain_document_level": True,
            "aggregation": aggregation_contract,
            "family_clustered_uncertainty": True,
            "optional_public_s3_challenge": {
                "expected_records": EXPECTED_PUBLIC_S3_CHALLENGE_DOCUMENTS,
                "truth_label": "S3",
                "document_origin": PUBLIC_REAL,
                "included_in_primary_metrics": False,
                "bootstrap_combined_with_primary": False,
            },
            "training_overlap_fields": [
                "doc_id",
                "document_family_id",
                "normalized_text_hash",
            ],
        },
        "inputs": {
            "frozen_proxy_gold": frozen_audit,
            "public_s3_challenge": public_s3_audit,
            "public_s3_separation": public_s3_separation,
            "public_s3_training_leakage_check": public_s3_training_leakage_audit,
            "training_manifest": training_audit,
            "training_leakage_check": leakage_audit,
        },
        "models": {
            "baseline": {
                "artifact": baseline_hash,
                "aggregation_runtime": baseline_runtime,
                "metrics": baseline_metrics,
            },
            "candidate": {
                "artifact": candidate_hash,
                "aggregation_runtime": candidate_runtime,
                "metrics": candidate_metrics,
            },
        },
        "candidate_minus_baseline": {
            key: candidate_value - _flatten_metrics(baseline_metrics)[key]
            for key, candidate_value in _flatten_metrics(candidate_metrics).items()
        },
        "bootstrap_confidence_intervals": bootstrap,
        "public_s3_challenge": public_s3_report,
        "evaluator": {
            "schema_version": SCHEMA_VERSION,
            "module_path": str(evaluator_path),
            "module_sha256": _sha256_file(evaluator_path),
            "cli_path": str(cli_path.resolve()),
            "cli_sha256": _sha256_file(cli_path),
        },
    }
    markdown_bytes = _render_markdown(report)
    output_artifacts: dict[str, object] = {
        "baseline_predictions": {
            "path": "baseline_predictions.jsonl",
            "sha256": _sha256_bytes(baseline_bytes),
            "records": len(baseline_output),
        },
        "candidate_predictions": {
            "path": "candidate_predictions.jsonl",
            "sha256": _sha256_bytes(candidate_bytes),
            "records": len(candidate_output),
        },
        "markdown_report": {
            "path": "REPORT.md",
            "sha256": _sha256_bytes(markdown_bytes),
        },
    }
    if baseline_public_bytes is not None and candidate_public_bytes is not None:
        output_artifacts.update(
            {
                "baseline_public_s3_predictions": {
                    "path": "baseline_public_s3_predictions.jsonl",
                    "sha256": _sha256_bytes(baseline_public_bytes),
                    "records": len(baseline_public_output),
                    "included_in_primary_metrics": False,
                },
                "candidate_public_s3_predictions": {
                    "path": "candidate_public_s3_predictions.jsonl",
                    "sha256": _sha256_bytes(candidate_public_bytes),
                    "records": len(candidate_public_output),
                    "included_in_primary_metrics": False,
                },
            }
        )
    report["artifacts"] = output_artifacts
    report_bytes = _json_bytes(report, indent=2)
    complete: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id_value,
        "committed_at": datetime.now(timezone.utc).isoformat(),
        "claim_scope": CLAIM_SCOPE,
        "artifacts": {
            "comparison": {
                "path": "comparison.json",
                "sha256": _sha256_bytes(report_bytes),
            },
            **dict(report["artifacts"]),
        },
        "frozen_input_sha256": frozen_audit["file_sha256"],
        "public_s3_input_sha256": (
            public_s3_audit["file_sha256"] if public_s3_audit is not None else None
        ),
        "baseline_model_tree_sha256": baseline_hash["tree_sha256"],
        "candidate_model_tree_sha256": candidate_hash["tree_sha256"],
        "aggregation_contract_sha256": contract_sha256,
    }
    complete_bytes = _json_bytes(complete, indent=2)

    output_root.mkdir(parents=True, exist_ok=True)
    staging_dir = output_root / f".{run_id_value}.staging-{uuid.uuid4().hex}"
    staging_dir.mkdir(exist_ok=False)
    try:
        _atomic_write_new(staging_dir / "baseline_predictions.jsonl", baseline_bytes)
        _atomic_write_new(staging_dir / "candidate_predictions.jsonl", candidate_bytes)
        if baseline_public_bytes is not None and candidate_public_bytes is not None:
            _atomic_write_new(
                staging_dir / "baseline_public_s3_predictions.jsonl",
                baseline_public_bytes,
            )
            _atomic_write_new(
                staging_dir / "candidate_public_s3_predictions.jsonl",
                candidate_public_bytes,
            )
        _atomic_write_new(staging_dir / "REPORT.md", markdown_bytes)
        _atomic_write_new(staging_dir / "comparison.json", report_bytes)
        _atomic_write_new(staging_dir / "COMPLETE.json", complete_bytes)
        staging_dir.rename(final_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    return final_dir, report, complete
