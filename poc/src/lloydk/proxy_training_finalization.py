"""Serving-faithful checkpoint selection and proxy calibration primitives.

The normal Hugging Face evaluation loop treats one truncated token sequence as
one evaluation sample.  LloydK serving does not: it character-splits a document,
uses fast-tokenizer overflow windows, applies temperature to every window logit,
and then aggregates probabilities at document level.  This module deliberately
reuses the model-comparison mirror of that contract and keeps raw window logits
so validation, temperature fitting, and operating-point fitting cannot silently
fall back to first-window evaluation.

This module contains no policy that permits proxy results to be described as
customer-document accuracy.  Its intended inputs are the family-separated
``validation_documents.jsonl`` and ``calibration_documents.jsonl`` emitted by
``materialize_proxy_training_set.py``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from lloydk.hygiene import text_hash
from lloydk.proxy_model_comparison import (
    LABELS,
    SERVING_CHAR_CHUNK_MULTIPLIER,
    SERVING_CHUNK_OVERLAP,
    SERVING_INFERENCE_BATCH_SIZE,
    SERVING_MAX_LENGTH,
    SERVING_SEVERE_AGG_CODES,
    _encode_serving_windows,
    aggregate_serving_probabilities,
    serving_aggregation_contract,
    serving_char_chunks,
)


TRACE_SCHEMA_VERSION = "proxy-document-window-logits-v1"
TEMPERATURE_SCHEMA_VERSION = "proxy-document-temperature-v1"
OPERATING_POINT_SCHEMA_VERSION = "proxy-operating-point-v1"
MATERIALIZATION_SCHEMA_VERSION = "proxy-training-run-v1"
GRADE_ORDER = {grade: index for index, grade in enumerate(LABELS)}
HIGH_GRADES = frozenset(("TS", "S1"))
_CHUNK_FIELDS = frozenset(
    ("chunk_id", "chunk_index", "chunk_start", "chunk_end", "source_doc_id")
)


class ProxyTrainingFinalizationError(ValueError):
    """A production checkpoint/calibration contract was violated."""


@dataclass(frozen=True)
class DocumentWindowLogits:
    """All unscaled model windows belonging to one document."""

    doc_id: str
    document_family_id: str
    label: str
    label_idx: int
    label_order: tuple[str, ...]
    window_logits: tuple[tuple[float, ...], ...]
    window_weights: tuple[float, ...]
    char_chunk_count: int
    tokenizer_mode_counts: tuple[tuple[str, int], ...]

    def to_json_record(self) -> dict[str, object]:
        record = asdict(self)
        record["schema_version"] = TRACE_SCHEMA_VERSION
        record["label_order"] = list(self.label_order)
        record["window_logits"] = [list(row) for row in self.window_logits]
        record["window_weights"] = list(self.window_weights)
        record["tokenizer_mode_counts"] = dict(self.tokenizer_mode_counts)
        return record


@dataclass(frozen=True)
class DocumentLogitBatch:
    """Raw per-document window logits plus runtime evidence."""

    documents: tuple[DocumentWindowLogits, ...]
    runtime_attestation: dict[str, object]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_trace_bytes(traces: Sequence[DocumentWindowLogits]) -> bytes:
    """Return deterministic JSONL bytes for an auditable raw-logit cache."""
    return b"".join(
        (
            json.dumps(
                trace.to_json_record(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        for trace in traces
    )


def load_document_rows(path: Path, *, purpose: str) -> tuple[list[dict[str, object]], bytes]:
    """Read one document-level split with strict identity and label checks."""
    if path.is_symlink() or not path.is_file():
        raise ProxyTrainingFinalizationError(f"{purpose} is not a regular file: {path}")
    try:
        payload = path.read_bytes()
        decoded = payload.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ProxyTrainingFinalizationError(f"cannot read {purpose} {path}: {exc}") from exc
    rows: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for line_number, line in enumerate(decoded.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProxyTrainingFinalizationError(
                f"malformed JSON at {path}:{line_number}: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise ProxyTrainingFinalizationError(
                f"{purpose} row {line_number} must be a JSON object"
            )
        if _CHUNK_FIELDS & row.keys():
            raise ProxyTrainingFinalizationError(
                f"{purpose} row {line_number} is a chunk, not a document"
            )
        parsed: dict[str, str] = {}
        for field in ("doc_id", "document_family_id", "text", "label"):
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ProxyTrainingFinalizationError(
                    f"{purpose} row {line_number} has invalid {field}"
                )
            parsed[field] = value if field == "text" else value.strip()
        if parsed["label"] not in LABELS:
            raise ProxyTrainingFinalizationError(
                f"{purpose} row {line_number} has invalid label {parsed['label']!r}"
            )
        digest = text_hash(parsed["text"])
        if parsed["doc_id"] in seen_ids:
            raise ProxyTrainingFinalizationError(
                f"{purpose} has duplicate doc_id {parsed['doc_id']!r}"
            )
        if digest in seen_hashes:
            raise ProxyTrainingFinalizationError(
                f"{purpose} has duplicate normalized text at row {line_number}"
            )
        seen_ids.add(parsed["doc_id"])
        seen_hashes.add(digest)
        rows.append({**row, **parsed})
    if not rows:
        raise ProxyTrainingFinalizationError(f"{purpose} is empty: {path}")
    missing_grades = set(LABELS) - {str(row["label"]) for row in rows}
    if missing_grades:
        raise ProxyTrainingFinalizationError(
            f"{purpose} is missing grades: {sorted(missing_grades)}"
        )
    return rows, payload


def _strict_json_object(path: Path, *, purpose: str) -> tuple[dict[str, object], bytes]:
    if path.is_symlink() or not path.is_file():
        raise ProxyTrainingFinalizationError(f"{purpose} is not a regular file: {path}")

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant {value}")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProxyTrainingFinalizationError(
            f"cannot read {purpose} {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ProxyTrainingFinalizationError(f"{purpose} must be a JSON object: {path}")
    return value, raw


def _jsonl_record_count(payload: bytes, *, purpose: str) -> int:
    try:
        decoded = payload.decode("utf-8")
    except UnicodeError as exc:
        raise ProxyTrainingFinalizationError(f"{purpose} is not UTF-8") from exc
    count = 0
    for line_number, line in enumerate(decoded.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProxyTrainingFinalizationError(
                f"malformed JSON in {purpose} row {line_number}: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise ProxyTrainingFinalizationError(
                f"{purpose} row {line_number} must be a JSON object"
            )
        count += 1
    if count == 0:
        raise ProxyTrainingFinalizationError(f"{purpose} is empty")
    return count


def verify_materialized_training_run(
    run_dir: Path,
) -> dict[str, object]:
    """Rehash every materialized artifact and return bound split records.

    Both the trainer and finalizer call this at their start and end.  It prevents
    an arbitrary checkpoint tree from being paired with just the validation and
    calibration files while train_chunks or train_documents have changed.
    """
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise ProxyTrainingFinalizationError(
            f"training run is not a regular directory: {run_dir}"
        )
    manifest, manifest_bytes = _strict_json_object(
        run_dir / "manifest.json", purpose="training materialization manifest"
    )
    complete, complete_bytes = _strict_json_object(
        run_dir / "COMPLETE", purpose="training materialization COMPLETE"
    )
    if manifest.get("schema_version") != MATERIALIZATION_SCHEMA_VERSION:
        raise ProxyTrainingFinalizationError(
            f"unsupported training materialization schema: {manifest.get('schema_version')!r}"
        )
    if manifest.get("status") != "complete":
        raise ProxyTrainingFinalizationError("training materialization is not complete")
    if complete.get("schema_version") != MATERIALIZATION_SCHEMA_VERSION:
        raise ProxyTrainingFinalizationError("training COMPLETE schema mismatch")
    if complete.get("run_id") != manifest.get("run_id"):
        raise ProxyTrainingFinalizationError("training manifest/COMPLETE run_id mismatch")
    manifest_hash = _sha256_bytes(manifest_bytes)
    if complete.get("manifest_sha256") != manifest_hash:
        raise ProxyTrainingFinalizationError("training manifest hash does not match COMPLETE")
    artifacts = manifest.get("artifacts")
    complete_artifacts = complete.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(complete_artifacts, dict):
        raise ProxyTrainingFinalizationError("training artifact attestations are missing")

    expected = {
        "train_documents": "train_documents.jsonl",
        "validation_documents": "validation_documents.jsonl",
        "calibration_documents": "calibration_documents.jsonl",
        "train_chunks": "train_chunks.jsonl",
    }
    artifact_audit: dict[str, dict[str, object]] = {}
    document_rows: dict[str, list[dict[str, object]]] = {}
    train_chunk_rows: list[dict[str, object]] = []
    for key, expected_name in expected.items():
        attestation = artifacts.get(key)
        completion = complete_artifacts.get(key)
        if not isinstance(attestation, dict) or not isinstance(completion, dict):
            raise ProxyTrainingFinalizationError(f"training artifact {key} is not attested")
        if attestation.get("path") != expected_name:
            raise ProxyTrainingFinalizationError(
                f"training artifact {key} has unexpected path {attestation.get('path')!r}"
            )
        path = run_dir / expected_name
        if path.is_symlink() or not path.is_file():
            raise ProxyTrainingFinalizationError(
                f"training artifact {key} is not a regular file: {path}"
            )
        payload = path.read_bytes()
        if key == "train_chunks":
            record_count = _jsonl_record_count(payload, purpose=key)
            train_chunk_rows = _load_train_chunk_rows(path)
            if len(train_chunk_rows) != record_count:
                raise ProxyTrainingFinalizationError(
                    "train_chunks changed while it was being read"
                )
        else:
            rows, rebound_payload = load_document_rows(path, purpose=key)
            if rebound_payload != payload:  # defensive; both reads must observe same bytes
                raise ProxyTrainingFinalizationError(
                    f"training artifact {key} changed while it was being read"
                )
            document_rows[key] = rows
            record_count = len(rows)
        digest = _sha256_bytes(payload)
        expected_hash = attestation.get("sha256")
        if (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or any(value not in "0123456789abcdef" for value in expected_hash)
        ):
            raise ProxyTrainingFinalizationError(f"training artifact {key} has invalid SHA-256")
        if digest != expected_hash or completion.get("sha256") != digest:
            raise ProxyTrainingFinalizationError(f"training artifact {key} hash mismatch")
        if (
            attestation.get("records") != record_count
            or completion.get("records") != record_count
        ):
            raise ProxyTrainingFinalizationError(f"training artifact {key} count mismatch")
        artifact_audit[key] = {
            "path": str(path.resolve()),
            "sha256": digest,
            "records": record_count,
        }

    separation = assert_materialized_split_isolation(
        document_rows["train_documents"],
        document_rows["validation_documents"],
        document_rows["calibration_documents"],
        train_chunk_rows,
    )
    _assert_train_chunks_match_documents(
        document_rows["train_documents"],
        train_chunk_rows,
        grade_weight_multipliers=_grade_weight_multipliers_from_manifest(manifest),
    )
    leakage_checks = manifest.get("leakage_checks")
    if not isinstance(leakage_checks, Mapping):
        raise ProxyTrainingFinalizationError(
            "training materialization leakage checks are missing"
        )
    expected_checks = {
        "family_overlap_with_frozen_or_blocked": 0,
        "normalized_text_hash_overlap_with_frozen_or_blocked": 0,
        "doc_id_overlap_with_frozen_or_blocked": 0,
        "family_overlap_across_splits": separation["document_family_id_overlap"],
        "doc_id_overlap_across_splits": separation["doc_id_overlap"],
        "normalized_text_hash_overlap_across_splits": separation[
            "normalized_text_hash_overlap"
        ],
        "train_chunk_source_doc_id_overlap_with_validation_or_calibration": separation[
            "train_chunk_source_doc_id_overlap"
        ],
        "train_chunk_family_overlap_with_validation_or_calibration": separation[
            "train_chunk_document_family_id_overlap"
        ],
        "train_chunk_text_hash_overlap_with_validation_or_calibration": separation[
            "train_chunk_normalized_text_hash_overlap"
        ],
        "frozen_records_in_splits": 0,
    }
    if set(leakage_checks) != set(expected_checks):
        raise ProxyTrainingFinalizationError(
            "training materialization leakage check keys do not match the "
            "production contract: "
            + json.dumps(
                {
                    "missing": sorted(set(expected_checks) - set(leakage_checks)),
                    "unexpected": sorted(set(leakage_checks) - set(expected_checks)),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    for key, actual in expected_checks.items():
        if leakage_checks.get(key) != actual:
            raise ProxyTrainingFinalizationError(
                f"training materialization leakage check mismatch for {key}: "
                f"manifest={leakage_checks.get(key)!r}, actual={actual}"
            )
    if any(value != 0 for value in leakage_checks.values()):
        raise ProxyTrainingFinalizationError(
            "training materialization leakage checks are nonzero"
        )
    if any(value != 0 for value in expected_checks.values()):
        raise ProxyTrainingFinalizationError(
            "training materialization split leakage detected: "
            + json.dumps(expected_checks, ensure_ascii=False, sort_keys=True)
        )
    return {
        "run_dir": str(run_dir.resolve()),
        "run_id": manifest["run_id"],
        "manifest_sha256": manifest_hash,
        "complete_sha256": _sha256_bytes(complete_bytes),
        "artifacts": artifact_audit,
        "separation": separation,
        "document_rows": document_rows,
    }


def assert_document_splits_disjoint(
    validation_rows: Sequence[Mapping[str, object]],
    calibration_rows: Sequence[Mapping[str, object]],
) -> dict[str, int]:
    """Recheck doc/family/text separation instead of trusting a manifest flag."""

    def identities(rows: Sequence[Mapping[str, object]]) -> tuple[set[str], set[str], set[str]]:
        return (
            {str(row["doc_id"]).strip() for row in rows},
            {str(row["document_family_id"]).strip() for row in rows},
            {text_hash(str(row["text"])) for row in rows},
        )

    validation = identities(validation_rows)
    calibration = identities(calibration_rows)
    names = ("doc_id", "document_family_id", "normalized_text_hash")
    overlaps = {
        f"{name}_overlap": len(left & right)
        for name, left, right in zip(names, validation, calibration, strict=True)
    }
    if any(overlaps.values()):
        raise ProxyTrainingFinalizationError(
            "validation/calibration leakage detected: "
            + json.dumps(overlaps, ensure_ascii=False, sort_keys=True)
        )
    return overlaps


def _load_train_chunk_rows(path: Path) -> list[dict[str, object]]:
    """Read the train-only chunk artifact needed for split-leakage verification."""
    try:
        decoded = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ProxyTrainingFinalizationError(f"cannot read train_chunks {path}: {exc}") from exc
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(decoded.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProxyTrainingFinalizationError(
                f"malformed JSON in train_chunks row {line_number}: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise ProxyTrainingFinalizationError(
                f"train_chunks row {line_number} must be a JSON object"
            )
        for field in ("source_doc_id", "document_family_id", "text"):
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ProxyTrainingFinalizationError(
                    f"train_chunks row {line_number} has invalid {field}"
                )
        rows.append(row)
    if not rows:
        raise ProxyTrainingFinalizationError("train_chunks is empty")
    return rows


def _grade_weight_multipliers_from_manifest(
    manifest: Mapping[str, object],
) -> dict[str, float]:
    contract = manifest.get("contract")
    if not isinstance(contract, Mapping):
        return {}
    raw = contract.get("grade_weight_multipliers")
    if raw in (None, {}):
        return {}
    if not isinstance(raw, Mapping):
        raise ProxyTrainingFinalizationError(
            "training materialization grade_weight_multipliers must be an object"
        )
    allowed = {"TS", "S1", "S2", "S3"}
    multipliers: dict[str, float] = {}
    for key, value in raw.items():
        grade = str(key)
        if grade not in allowed:
            raise ProxyTrainingFinalizationError(
                f"unknown grade weight multiplier target: {grade!r}"
            )
        try:
            multiplier = float(value)
        except (TypeError, ValueError) as exc:
            raise ProxyTrainingFinalizationError(
                f"invalid grade weight multiplier for {grade}: {value!r}"
            ) from exc
        if not (0.0 < multiplier <= 10.0):
            raise ProxyTrainingFinalizationError(
                f"grade weight multiplier for {grade} must be in (0, 10], got {multiplier}"
            )
        multipliers[grade] = multiplier
    return multipliers


def _apply_grade_weight_multipliers_to_chunks(
    chunks: Sequence[Mapping[str, object]],
    multipliers: Mapping[str, float],
) -> list[dict[str, object]]:
    if not multipliers:
        return [dict(chunk) for chunk in chunks]
    weighted: list[dict[str, object]] = []
    for chunk in chunks:
        row = dict(chunk)
        label = str(row.get("label") or "")
        multiplier = float(multipliers.get(label, 1.0))
        if multiplier != 1.0:
            base_weight = float(row.get("sample_weight", 1.0))
            row["sample_weight"] = base_weight * multiplier
            notes = list(row.get("weighting_notes") or [])
            notes.append(f"grade_weight_multiplier:{label}:{multiplier:g}")
            row["weighting_notes"] = notes
        weighted.append(row)
    return weighted


def _assert_train_chunks_match_documents(
    train_rows: Sequence[Mapping[str, object]],
    stored_chunks: Sequence[Mapping[str, object]],
    *,
    grade_weight_multipliers: Mapping[str, float] | None = None,
) -> None:
    """Rebuild train chunks to bind every chunk byte to its source document.

    A train chunk is executable training input, not merely an auxiliary audit
    artifact.  Checking its IDs against evaluation splits is insufficient: a
    hostile chunk can relabel a frozen sentence with a harmless source ID.  The
    only accepted artifact is the exact deterministic output of the same
    evidence-aware expander over the attested train documents.
    """
    try:
        from lloydk.modules.m4_training.chunk_expand import (  # noqa: PLC0415
            expand_records_evidence_aware,
        )

        expected_chunks = _apply_grade_weight_multipliers_to_chunks(
            expand_records_evidence_aware(train_rows),
            dict(grade_weight_multipliers or {}),
        )
    except Exception as exc:  # noqa: BLE001
        raise ProxyTrainingFinalizationError(
            f"cannot deterministically reconstruct train_chunks from train_documents: {exc}"
        ) from exc
    expected_ids = {str(row["doc_id"]).strip() for row in train_rows}
    actual_sources = {str(row.get("source_doc_id") or "").strip() for row in stored_chunks}
    if actual_sources != expected_ids:
        raise ProxyTrainingFinalizationError(
            "train_chunks source_doc_id coverage does not exactly match train_documents"
        )
    if len(expected_chunks) != len(stored_chunks):
        raise ProxyTrainingFinalizationError(
            "train_chunks row count does not match deterministic expansion"
        )
    for index, (expected, actual) in enumerate(
        zip(expected_chunks, stored_chunks, strict=True)
    ):
        if dict(actual) != expected:
            raise ProxyTrainingFinalizationError(
                "train_chunks deterministic expansion mismatch at row "
                f"{index}; source_doc_id/chunk_id/offset/text and all inherited "
                "fields must exactly match train_documents"
            )


def assert_materialized_split_isolation(
    train_rows: Sequence[Mapping[str, object]],
    validation_rows: Sequence[Mapping[str, object]],
    calibration_rows: Sequence[Mapping[str, object]],
    train_chunks: Sequence[Mapping[str, object]],
) -> dict[str, int]:
    """Recompute every train/evaluation split boundary from artifact contents."""

    def identities(rows: Sequence[Mapping[str, object]]) -> dict[str, set[str]]:
        return {
            "doc_id": {str(row["doc_id"]).strip() for row in rows},
            "document_family_id": {
                str(row["document_family_id"]).strip() for row in rows
            },
            "normalized_text_hash": {text_hash(str(row["text"])) for row in rows},
        }

    train = identities(train_rows)
    validation = identities(validation_rows)
    calibration = identities(calibration_rows)
    # Count each leaked identity once even if it appears in both evaluation splits.
    evaluation = {
        name: validation[name] | calibration[name]
        for name in train
    }
    validation_calibration = {
        name: validation[name] & calibration[name]
        for name in train
    }
    chunks = {
        "source_doc_id": {
            str(row["source_doc_id"]).strip() for row in train_chunks
        },
        "document_family_id": {
            str(row["document_family_id"]).strip() for row in train_chunks
        },
        "normalized_text_hash": {
            text_hash(str(row["text"])) for row in train_chunks
        },
    }
    result = {
        "doc_id_overlap": len(train["doc_id"] & evaluation["doc_id"])
        + len(validation_calibration["doc_id"]),
        "document_family_id_overlap": len(
            train["document_family_id"] & evaluation["document_family_id"]
        )
        + len(validation_calibration["document_family_id"]),
        "normalized_text_hash_overlap": len(
            train["normalized_text_hash"] & evaluation["normalized_text_hash"]
        )
        + len(validation_calibration["normalized_text_hash"]),
        "train_chunk_source_doc_id_overlap": len(
            chunks["source_doc_id"] & evaluation["doc_id"]
        ),
        "train_chunk_document_family_id_overlap": len(
            chunks["document_family_id"] & evaluation["document_family_id"]
        ),
        "train_chunk_normalized_text_hash_overlap": len(
            chunks["normalized_text_hash"] & evaluation["normalized_text_hash"]
        ),
    }
    return result


def _resolve_model_label_order(model: object) -> tuple[str, ...]:
    config = getattr(model, "config", None)
    raw = getattr(config, "id2label", None)
    if not isinstance(raw, Mapping):
        raise ProxyTrainingFinalizationError("model config.id2label is missing")
    try:
        normalized = {int(index): str(label) for index, label in raw.items()}
    except (TypeError, ValueError) as exc:
        raise ProxyTrainingFinalizationError("model config.id2label is invalid") from exc
    expected_ids = set(range(len(LABELS)))
    if set(normalized) != expected_ids:
        raise ProxyTrainingFinalizationError(
            f"model label ids must be contiguous {sorted(expected_ids)}; found {sorted(normalized)}"
        )
    order = tuple(normalized[index] for index in range(len(LABELS)))
    # Serving can technically map an arbitrary valid order, but checkpoint
    # selection tie-breaking and severity order are safest when the training
    # contract remains the canonical TS,S1,S2,S3 mapping.
    if order != tuple(LABELS):
        raise ProxyTrainingFinalizationError(
            f"model label order must be exactly {tuple(LABELS)}; found {order}"
        )
    return order


def _model_device(model: object, requested: str):
    if requested not in {"auto", "cpu", "cuda"}:
        raise ProxyTrainingFinalizationError("device must be auto, cpu, or cuda")
    try:
        import torch
    except ImportError as exc:
        raise ProxyTrainingFinalizationError("torch is required for model inference") from exc
    if requested == "cuda" and not torch.cuda.is_available():
        raise ProxyTrainingFinalizationError("CUDA was requested but is unavailable")
    selected = "cuda" if requested == "auto" and torch.cuda.is_available() else requested
    if selected == "auto":
        selected = "cpu"
    model.to(selected)
    return selected


def collect_document_window_logits(
    model: object,
    tokenizer: object,
    rows: Sequence[Mapping[str, object]],
    *,
    batch_size: int = SERVING_INFERENCE_BATCH_SIZE,
    device: str = "auto",
    max_length: int = SERVING_MAX_LENGTH,
    chunk_overlap: int = SERVING_CHUNK_OVERLAP,
    require_fast_overflow: bool = True,
    apply_bundle_operating_point: bool = False,
) -> DocumentLogitBatch:
    """Run one unscaled forward pass using the exact comparison/M5 window contract.

    The output is raw logits, not probabilities.  Temperature is therefore
    applied *before* softmax for every candidate T, and the nonlinear severe-max
    document aggregation is recomputed for every T.
    """
    if not rows:
        raise ProxyTrainingFinalizationError("document inference rows are empty")
    if batch_size < 1:
        raise ProxyTrainingFinalizationError("batch_size must be positive")
    label_order = _resolve_model_label_order(model)
    if require_fast_overflow and not bool(getattr(tokenizer, "is_fast", False)):
        raise ProxyTrainingFinalizationError(
            "production document evaluation requires a fast tokenizer with overflow windows"
        )
    selected_device = _model_device(model, device)
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - guarded above
        raise ProxyTrainingFinalizationError("torch is required for model inference") from exc

    contract = serving_aggregation_contract(
        max_length=max_length,
        chunk_overlap=chunk_overlap,
        severe_codes=SERVING_SEVERE_AGG_CODES,
        forward_batch_size=batch_size,
        apply_bundle_operating_point=apply_bundle_operating_point,
        require_fast_overflow=require_fast_overflow,
    )
    was_training = bool(getattr(model, "training", False))
    model.eval()
    traces: list[DocumentWindowLogits] = []
    global_modes: Counter[str] = Counter()
    total_char_chunks = 0
    total_token_windows = 0
    multi_chunk_documents = 0
    overflow_documents = 0
    try:
        with torch.no_grad():
            for row_index, row in enumerate(rows):
                for field in ("doc_id", "document_family_id", "text", "label"):
                    if not isinstance(row.get(field), str) or not str(row[field]).strip():
                        raise ProxyTrainingFinalizationError(
                            f"inference row {row_index} has invalid {field}"
                        )
                label = str(row["label"]).strip()
                if label not in label_order:
                    raise ProxyTrainingFinalizationError(
                        f"inference row {row_index} has invalid label {label!r}"
                    )
                char_chunks = serving_char_chunks(
                    str(row["text"]),
                    max_length=max_length,
                    chunk_overlap=chunk_overlap,
                )
                parent_weights = [max(len(chunk.strip()), 1) for chunk in char_chunks]
                logits_rows: list[tuple[float, ...]] = []
                window_weights: list[float] = []
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
                        raise ProxyTrainingFinalizationError(
                            "fast-tokenizer overflow failed; refusing truncation fallback "
                            f"for document {row['doc_id']!r} (mode={mode})"
                        )
                    document_modes[mode] += 1
                    global_modes[mode] += 1
                    encoded = encoded.to(selected_device)
                    output = model(**encoded)
                    raw_logits = output.logits.detach().cpu().tolist()
                    if len(raw_logits) != len(sample_mapping):
                        raise ProxyTrainingFinalizationError(
                            "tokenizer overflow mapping does not match model windows"
                        )
                    for values in raw_logits:
                        parsed = tuple(float(value) for value in values)
                        if len(parsed) != len(LABELS) or any(
                            not math.isfinite(value) for value in parsed
                        ):
                            raise ProxyTrainingFinalizationError(
                                f"invalid model logits for document {row['doc_id']!r}"
                            )
                        logits_rows.append(parsed)
                    window_weights.extend(
                        float(parent_weights[start + parent_index])
                        for parent_index in sample_mapping
                    )
                if not logits_rows or len(logits_rows) != len(window_weights):
                    raise ProxyTrainingFinalizationError(
                        f"document {row['doc_id']!r} produced no matched windows"
                    )
                total_char_chunks += len(char_chunks)
                total_token_windows += len(logits_rows)
                multi_chunk_documents += int(len(char_chunks) > 1)
                overflow_documents += int(len(logits_rows) > len(char_chunks))
                traces.append(
                    DocumentWindowLogits(
                        doc_id=str(row["doc_id"]).strip(),
                        document_family_id=str(row["document_family_id"]).strip(),
                        label=label,
                        label_idx=label_order.index(label),
                        label_order=label_order,
                        window_logits=tuple(logits_rows),
                        window_weights=tuple(window_weights),
                        char_chunk_count=len(char_chunks),
                        tokenizer_mode_counts=tuple(sorted(document_modes.items())),
                    )
                )
    finally:
        if was_training:
            model.train()
    trace_bytes = canonical_trace_bytes(traces)
    runtime = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "aggregation_contract_sha256": contract["contract_sha256"],
        "documents": len(traces),
        "total_character_chunks": total_char_chunks,
        "total_token_windows": total_token_windows,
        "documents_with_multiple_character_chunks": multi_chunk_documents,
        "documents_with_overflow_expansion": overflow_documents,
        "tokenizer": {
            "class": type(tokenizer).__name__,
            "is_fast": bool(getattr(tokenizer, "is_fast", False)),
            "mode_counts": dict(sorted(global_modes.items())),
            "fallback_permitted": not require_fast_overflow,
        },
        "raw_logits": True,
        "temperature_applied_during_forward": False,
        "evaluation_unit": "document",
        "chunk_rows_counted_as_evaluation_samples": False,
        "device": selected_device,
        "trace_sha256": _sha256_bytes(trace_bytes),
    }
    return DocumentLogitBatch(tuple(traces), runtime)


def load_model_document_logits(
    model_dir: Path,
    rows: Sequence[Mapping[str, object]],
    **kwargs: object,
) -> DocumentLogitBatch:
    """Load a checkpoint and collect serving-faithful unscaled document traces."""
    if model_dir.is_symlink() or not model_dir.is_dir():
        raise ProxyTrainingFinalizationError(
            f"model checkpoint is not a regular directory: {model_dir}"
        )
    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError as exc:
        raise ProxyTrainingFinalizationError(
            "torch and transformers are required for checkpoint evaluation"
        ) from exc
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
    model = AutoModelForSequenceClassification.from_pretrained(str(model_dir))
    try:
        return collect_document_window_logits(model, tokenizer, rows, **kwargs)
    finally:
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _softmax(logits: Sequence[float], temperature: float) -> list[float]:
    if not math.isfinite(temperature) or temperature <= 0:
        raise ProxyTrainingFinalizationError("temperature must be finite and positive")
    scaled = [float(value) / temperature for value in logits]
    if not scaled or any(not math.isfinite(value) for value in scaled):
        raise ProxyTrainingFinalizationError("logits must be finite and non-empty")
    maximum = max(scaled)
    exponentials = [math.exp(value - maximum) for value in scaled]
    total = sum(exponentials)
    if not math.isfinite(total) or total <= 0:
        raise ProxyTrainingFinalizationError("softmax probability mass is invalid")
    return [value / total for value in exponentials]


def aggregate_trace_probabilities(
    trace: DocumentWindowLogits,
    *,
    temperature: float,
) -> dict[str, float]:
    """Apply T at window-logit level, then recompute the nonlinear M5 aggregation."""
    if trace.label_order != tuple(LABELS):
        raise ProxyTrainingFinalizationError("trace label order is not canonical")
    if len(trace.window_logits) != len(trace.window_weights) or not trace.window_logits:
        raise ProxyTrainingFinalizationError("trace window logits/weights are empty or mismatched")
    probabilities = [_softmax(row, temperature) for row in trace.window_logits]
    return aggregate_serving_probabilities(
        probabilities,
        trace.window_weights,
        severe_codes=SERVING_SEVERE_AGG_CODES,
        label_order=trace.label_order,
    )


def document_probabilities(
    traces: Sequence[DocumentWindowLogits],
    *,
    temperature: float,
) -> list[dict[str, float]]:
    if not traces:
        raise ProxyTrainingFinalizationError("document traces are empty")
    return [aggregate_trace_probabilities(trace, temperature=temperature) for trace in traces]


def predict_with_escalation(
    scores: Mapping[str, float], *, tau: float | None
) -> str:
    """Mirror M5 ``_select_pred_idx`` over canonical grade order."""
    parsed = {grade: float(scores.get(grade, math.nan)) for grade in LABELS}
    if any(not math.isfinite(value) or value < 0 for value in parsed.values()):
        raise ProxyTrainingFinalizationError("document probabilities are invalid")
    if tau is not None:
        if not math.isfinite(tau) or not 0.0 < tau < 1.0:
            raise ProxyTrainingFinalizationError("escalation tau must be in (0,1)")
        for grade in LABELS:
            if parsed[grade] >= tau:
                return grade
    return max(LABELS, key=lambda grade: parsed[grade])


def _confusion_matrix(truths: Sequence[str], predictions: Sequence[str]) -> list[list[int]]:
    if not truths or len(truths) != len(predictions):
        raise ProxyTrainingFinalizationError("truth/prediction vectors are empty or mismatched")
    matrix = [[0 for _ in LABELS] for _ in LABELS]
    for truth, prediction in zip(truths, predictions, strict=True):
        if truth not in GRADE_ORDER or prediction not in GRADE_ORDER:
            raise ProxyTrainingFinalizationError("truth/prediction has an invalid grade")
        matrix[GRADE_ORDER[truth]][GRADE_ORDER[prediction]] += 1
    return matrix


def classification_metrics(
    truths: Sequence[str], predictions: Sequence[str]
) -> dict[str, object]:
    """Metrics used for checkpoint and escalation selection at document level."""
    matrix = _confusion_matrix(truths, predictions)
    supports = [sum(row) for row in matrix]
    total = sum(supports)
    per_grade: dict[str, dict[str, float | int]] = {}
    f1_values: list[float] = []
    for index, grade in enumerate(LABELS):
        true_positive = matrix[index][index]
        false_positive = sum(matrix[row][index] for row in range(len(LABELS)) if row != index)
        false_negative = supports[index] - true_positive
        precision = (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else 0.0
        )
        recall = (
            true_positive / (true_positive + false_negative)
            if true_positive + false_negative
            else 0.0
        )
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        f1_values.append(f1)
        per_grade[grade] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": supports[index],
        }

    high_ids = tuple(GRADE_ORDER[grade] for grade in HIGH_GRADES)
    high_total = sum(supports[index] for index in high_ids)
    high_under = sum(
        matrix[index][predicted]
        for index in high_ids
        for predicted in range(len(LABELS))
        if predicted > index
    )
    low_ids = tuple(index for index in range(len(LABELS)) if index not in high_ids)
    low_total = sum(supports[index] for index in low_ids)
    low_to_high = sum(
        matrix[index][predicted]
        for index in low_ids
        for predicted in high_ids
    )
    largest_prediction_column = max(
        sum(matrix[row][column] for row in range(len(LABELS)))
        for column in range(len(LABELS))
    )
    degenerate_penalty = 1.0 if largest_prediction_column >= 0.99 * total else 0.0
    fnr_high = high_under / high_total if high_total else 0.0
    over_class_rate = low_to_high / low_total if low_total else 0.0
    s3_index = GRADE_ORDER["S3"]
    s3_over = supports[s3_index] - matrix[s3_index][s3_index]
    review_count = sum(prediction in HIGH_GRADES for prediction in predictions)
    return {
        "documents": total,
        "accuracy": sum(matrix[index][index] for index in range(len(LABELS))) / total,
        "f1_macro": sum(f1_values) / len(LABELS),
        "fnr_high": fnr_high,
        "over_class_rate": over_class_rate,
        "degenerate_penalty": degenerate_penalty,
        "fnr_high_balanced": fnr_high + over_class_rate + degenerate_penalty,
        "s3_overclassification_rate": s3_over / supports[s3_index]
        if supports[s3_index]
        else 0.0,
        "review_burden": review_count / total,
        "confusion_matrix": matrix,
        "per_grade": per_grade,
        "prediction_distribution": dict(sorted(Counter(predictions).items())),
    }


def document_nll(
    traces: Sequence[DocumentWindowLogits], *, temperature: float
) -> float:
    probabilities = document_probabilities(traces, temperature=temperature)
    loss = 0.0
    for trace, scores in zip(traces, probabilities, strict=True):
        loss -= math.log(max(float(scores[trace.label]), 1e-12))
    return loss / len(traces)


def document_ece(
    traces: Sequence[DocumentWindowLogits], *, temperature: float, n_bins: int = 10
) -> float:
    if n_bins < 2:
        raise ProxyTrainingFinalizationError("n_bins must be at least two")
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for trace, scores in zip(
        traces,
        document_probabilities(traces, temperature=temperature),
        strict=True,
    ):
        prediction = predict_with_escalation(scores, tau=None)
        confidence = float(scores[prediction])
        index = min(int(confidence * n_bins), n_bins - 1)
        buckets[index].append((confidence, int(prediction == trace.label)))
    result = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        accuracy = sum(correct for _, correct in bucket) / len(bucket)
        confidence = sum(value for value, _ in bucket) / len(bucket)
        result += (len(bucket) / len(traces)) * abs(confidence - accuracy)
    return result


def fit_document_temperature(
    traces: Sequence[DocumentWindowLogits],
    *,
    lo: float = 0.05,
    hi: float = 5.0,
    steps: int = 200,
    n_bins: int = 10,
    fail_on_boundary: bool = True,
) -> dict[str, object]:
    """Fit T on document NLL while preserving window/aggregation nonlinearity."""
    if not traces:
        raise ProxyTrainingFinalizationError("calibration traces are empty")
    if not 0 < lo < hi or steps < 2:
        raise ProxyTrainingFinalizationError("invalid temperature search bounds")
    if set(LABELS) - {trace.label for trace in traces}:
        raise ProxyTrainingFinalizationError("calibration traces must contain all grades")
    baseline_nll = document_nll(traces, temperature=1.0)
    best_temperature = 1.0
    best_nll = baseline_nll
    for index in range(steps + 1):
        candidate = lo + (hi - lo) * (index / steps)
        candidate_nll = document_nll(traces, temperature=candidate)
        if candidate_nll < best_nll:
            best_temperature = candidate
            best_nll = candidate_nll
    # Store enough precision that reloading T does not move the subsequent tau
    # sweep onto a different probability boundary.
    stored_temperature = float(f"{best_temperature:.12g}")
    boundary_hit = math.isclose(best_temperature, lo, abs_tol=1e-12) or math.isclose(
        best_temperature, hi, abs_tol=1e-12
    )
    if boundary_hit and fail_on_boundary:
        raise ProxyTrainingFinalizationError(
            "document temperature optimum hit the search boundary; expand the search "
            f"instead of publishing a clipped calibration (T={best_temperature}, "
            f"range=[{lo},{hi}])"
        )
    stored_nll = document_nll(traces, temperature=stored_temperature)
    if stored_nll > baseline_nll + 1e-12:
        raise ProxyTrainingFinalizationError(
            "temperature fitting increased document NLL; refusing calibration artifact"
        )
    trace_bytes = canonical_trace_bytes(traces)
    return {
        "schema_version": TEMPERATURE_SCHEMA_VERSION,
        "status": "complete",
        "temperature": stored_temperature,
        "fit_unit": "document",
        "fit_objective": "document_nll_after_window_softmax_and_m5_aggregation",
        "n_documents": len(traces),
        "n_windows": sum(len(trace.window_logits) for trace in traces),
        "nll_before": baseline_nll,
        "nll_after": stored_nll,
        "ece_before": document_ece(traces, temperature=1.0, n_bins=n_bins),
        "ece_after": document_ece(
            traces, temperature=stored_temperature, n_bins=n_bins
        ),
        "search": {"lo": lo, "hi": hi, "steps": steps},
        "search_boundary_hit": boundary_hit,
        "calibration_trace_sha256": _sha256_bytes(trace_bytes),
    }


def _tau_candidates(probabilities: Sequence[Mapping[str, float]]) -> list[float | None]:
    values = sorted(
        {
            float(scores[grade])
            for scores in probabilities
            for grade in LABELS
            if 0.0 < float(scores[grade]) < 1.0
        }
    )
    if not values:
        raise ProxyTrainingFinalizationError("calibration probabilities have no tau boundaries")
    # Choose interval midpoints, not exact observed probabilities.  This avoids
    # a production decision changing solely because CPU/GPU recomputation moves
    # one probability by a final floating-point bit.
    bounds = [0.0, *values, 1.0]
    candidates = {
        (left + right) / 2.0
        for left, right in zip(bounds[:-1], bounds[1:], strict=True)
        if 0.0 < (left + right) / 2.0 < 1.0
    }
    return [None, *sorted(candidates, reverse=True)]


def fit_escalation_operating_point(
    traces: Sequence[DocumentWindowLogits],
    *,
    temperature: float,
    fnr_target: float = 0.05,
) -> dict[str, object]:
    """Select tau only on calibration documents after T is frozen.

    Feasible points must meet the configured high-grade underclassification
    target.  Within that set we maximize macro F1, then minimize S3
    overclassification and review burden, and finally prefer the less aggressive
    (higher) threshold.  No feasible point is a loud failure.
    """
    if not 0.0 <= fnr_target <= 1.0:
        raise ProxyTrainingFinalizationError("fnr_target must be in [0,1]")
    probabilities = document_probabilities(traces, temperature=temperature)
    truths = [trace.label for trace in traces]
    candidates = _tau_candidates(probabilities)
    sweep: list[dict[str, object]] = []
    for tau in candidates:
        predictions = [
            predict_with_escalation(scores, tau=tau) for scores in probabilities
        ]
        metrics = classification_metrics(truths, predictions)
        sweep.append({"tau": tau, **metrics})
    feasible = [
        row
        for row in sweep
        if float(row["fnr_high"]) <= fnr_target
        and float(row["degenerate_penalty"]) == 0.0
    ]
    if not feasible:
        best_fnr = min(float(row["fnr_high"]) for row in sweep)
        raise ProxyTrainingFinalizationError(
            "no escalation operating point meets the calibration FNR target; "
            f"target={fnr_target}, best={best_fnr}; degenerate points are ineligible"
        )

    def rank(row: Mapping[str, object]) -> tuple[float, float, float, float, float]:
        tau = row["tau"]
        # argmax (None) is preferred on an exact metric tie because it adds no
        # escalation.  Otherwise a higher tau is the less aggressive choice.
        conservatism = 1.0 if tau is None else float(tau)
        return (
            float(row["f1_macro"]),
            -float(row["s3_overclassification_rate"]),
            -float(row["review_burden"]),
            -float(row["over_class_rate"]),
            conservatism,
        )

    selected = max(feasible, key=rank)
    selected_tau = selected["tau"]
    # ``None`` is a valid, explicitly calibrated argmax operating point.  The
    # serving artifact keeps that distinction rather than inventing a number.
    compact_keys = (
        "tau",
        "documents",
        "accuracy",
        "f1_macro",
        "fnr_high",
        "over_class_rate",
        "degenerate_penalty",
        "fnr_high_balanced",
        "s3_overclassification_rate",
        "review_burden",
        "prediction_distribution",
        "confusion_matrix",
        "per_grade",
    )
    return {
        "schema_version": OPERATING_POINT_SCHEMA_VERSION,
        "status": "complete",
        "classifier_escalation_tau": selected_tau,
        "selection_split": "calibration_documents",
        "selection_unit": "document",
        "temperature": temperature,
        "fnr_target": fnr_target,
        "selection_policy": (
            "meet_fnr_target_then_max_f1_then_min_s3_overclassification_"
            "then_min_review_burden_then_min_overclass_then_high_tau"
        ),
        "candidate_count": len(sweep),
        "feasible_candidate_count": len(feasible),
        "selected_metrics": {key: selected[key] for key in compact_keys},
        "argmax_metrics": {
            key: sweep[0][key] for key in compact_keys if key != "tau"
        },
        "calibration_trace_sha256": _sha256_bytes(canonical_trace_bytes(traces)),
    }


def evaluate_checkpoint_traces(
    traces: Sequence[DocumentWindowLogits],
) -> dict[str, object]:
    """Compute the exact validation checkpoint metric at identity T and argmax."""
    probabilities = document_probabilities(traces, temperature=1.0)
    predictions = [predict_with_escalation(scores, tau=None) for scores in probabilities]
    metrics = classification_metrics(
        [trace.label for trace in traces], predictions
    )
    return {
        **metrics,
        "nll": document_nll(traces, temperature=1.0),
        "temperature": 1.0,
        "classifier_escalation_tau": None,
        "selection_split": "validation_documents",
        "evaluation_unit": "document",
        "trace_sha256": _sha256_bytes(canonical_trace_bytes(traces)),
    }


def select_checkpoint(
    candidates: Sequence[tuple[str, Mapping[str, object]]],
) -> tuple[str, dict[str, object]]:
    """Choose a validation checkpoint with deterministic safety-aware ties."""
    if not candidates:
        raise ProxyTrainingFinalizationError("no checkpoint candidates were evaluated")
    names: set[str] = set()
    parsed: list[tuple[str, dict[str, object]]] = []
    for name, metrics in candidates:
        if not name or name in names:
            raise ProxyTrainingFinalizationError(f"duplicate/empty checkpoint name: {name!r}")
        names.add(name)
        required = ("fnr_high_balanced", "f1_macro", "nll")
        values: dict[str, float] = {}
        for key in required:
            value = metrics.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ProxyTrainingFinalizationError(
                    f"checkpoint {name!r} has invalid metric {key}"
                )
            values[key] = float(value)
            if not math.isfinite(values[key]):
                raise ProxyTrainingFinalizationError(
                    f"checkpoint {name!r} has non-finite metric {key}"
                )
        parsed.append((name, dict(metrics)))
    selected_name, selected_metrics = min(
        parsed,
        key=lambda item: (
            float(item[1]["fnr_high_balanced"]),
            -float(item[1]["f1_macro"]),
            float(item[1]["nll"]),
            item[0],
        ),
    )
    return selected_name, selected_metrics


def serving_contract_summary() -> dict[str, Any]:
    """Expose the exact fixed runtime constants included in finalization reports."""
    return {
        "max_length_tokens": SERVING_MAX_LENGTH,
        "character_chunk_size": SERVING_MAX_LENGTH * SERVING_CHAR_CHUNK_MULTIPLIER,
        "character_overlap": SERVING_CHUNK_OVERLAP,
        "forward_batch_size_default": SERVING_INFERENCE_BATCH_SIZE,
        "severe_aggregation_codes": list(SERVING_SEVERE_AGG_CODES),
        "contract": serving_aggregation_contract(
            max_length=SERVING_MAX_LENGTH,
            chunk_overlap=SERVING_CHUNK_OVERLAP,
            severe_codes=SERVING_SEVERE_AGG_CODES,
            forward_batch_size=SERVING_INFERENCE_BATCH_SIZE,
        ),
    }
