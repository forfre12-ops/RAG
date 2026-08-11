"""Finalize a proxy classifier with independent, serving-faithful splits.

This command is the production boundary after ``p1_train_classifier.py``.  The
trainer's epoch checkpoints are treated as candidates only:

1. choose the checkpoint on ``validation_documents.jsonl`` using the exact M5
   character-chunk + fast-tokenizer-overflow + document aggregation path;
2. run the chosen model once on the disjoint ``calibration_documents.jsonl``
   and cache raw per-window logits;
3. fit temperature on document NLL, then fit severity-escalation tau on the
   temperature-scaled document probabilities;
4. publish a new immutable model directory only if every step succeeds.

The frozen 1,000 and public blind sets are not accepted as tuning inputs.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any
import uuid

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
_SRC = _ROOT / "src"
for _entry in (_ROOT, _SRC):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from lloydk.proxy_model_comparison import (  # noqa: E402
    SERVING_INFERENCE_BATCH_SIZE,
    hash_model_directory,
)
from lloydk.proxy_training_finalization import (  # noqa: E402
    ProxyTrainingFinalizationError,
    canonical_trace_bytes,
    evaluate_checkpoint_traces,
    fit_document_temperature,
    CHANCE_LEVEL_F1_MACRO,
    fit_escalation_operating_point,
    load_model_document_logits,
    select_checkpoint,
    serving_contract_summary,
    verify_materialized_training_run as verify_bound_training_run,
)


SCHEMA_VERSION = "proxy-classifier-finalization-v1"
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_CHECKPOINT_NAME = re.compile(r"checkpoint-(\d+)\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


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


def verify_materialized_training_run(
    run_dir: Path,
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
    """Rebind every artifact, then expose the two independent document splits."""
    bound = verify_bound_training_run(run_dir)
    rows = bound["document_rows"]
    artifacts = bound["artifacts"]
    audit: dict[str, object] = {
        key: value for key, value in bound.items() if key != "document_rows"
    }
    # Backwards-compatible convenient aliases used in the finalization manifest.
    audit["validation_documents"] = artifacts["validation_documents"]
    audit["calibration_documents"] = artifacts["calibration_documents"]
    return rows["validation_documents"], rows["calibration_documents"], audit


def discover_checkpoints(root: Path) -> list[Path]:
    """Return every immutable epoch checkpoint in numeric step order."""
    if root.is_symlink() or not root.is_dir():
        raise ProxyTrainingFinalizationError(
            f"checkpoint root is not a regular directory: {root}"
        )
    found: list[tuple[int, Path]] = []
    for path in root.iterdir():
        match = _CHECKPOINT_NAME.fullmatch(path.name)
        if match is None:
            continue
        if path.is_symlink() or not path.is_dir():
            raise ProxyTrainingFinalizationError(
                f"checkpoint candidate is not a regular directory: {path}"
            )
        found.append((int(match.group(1)), path))
    if not found:
        raise ProxyTrainingFinalizationError(f"no checkpoint-* directories found in {root}")
    found.sort(key=lambda item: (item[0], item[1].name))
    return [path for _, path in found]


def verify_proxy_training_execution(
    checkpoint_root: Path,
    *,
    materialization_audit: dict[str, object],
) -> tuple[list[Path], dict[str, object]]:
    """Bind checkpoint bytes to the exact materialized run and trainer source."""
    execution, execution_bytes = _strict_json_object(
        checkpoint_root / "TRAINING_EXECUTION.json",
        purpose="proxy training execution manifest",
    )
    complete, complete_bytes = _strict_json_object(
        checkpoint_root / "TRAINING_CANDIDATES_COMPLETE",
        purpose="proxy training candidate completion marker",
    )
    if (
        execution.get("schema_version") != "proxy-training-execution-v1"
        or execution.get("status") != "checkpoint_candidates_complete"
        or execution.get("deployable") is not False
        or execution.get("finalizer_required") is not True
    ):
        raise ProxyTrainingFinalizationError("proxy training execution contract is invalid")
    if (
        complete.get("schema_version") != "proxy-training-execution-v1"
        or complete.get("status") != "complete"
        or complete.get("run_id") != execution.get("run_id")
        or complete.get("manifest_sha256") != _sha256_bytes(execution_bytes)
    ):
        raise ProxyTrainingFinalizationError(
            "proxy training execution manifest/COMPLETE mismatch"
        )
    canonical_materialization = {
        key: value
        for key, value in materialization_audit.items()
        if key not in {"validation_documents", "calibration_documents"}
    }
    if execution.get("materialized_training_run") != canonical_materialization:
        raise ProxyTrainingFinalizationError(
            "checkpoint execution is not bound to this materialized training run"
        )
    inputs = execution.get("inputs")
    artifacts = canonical_materialization.get("artifacts")
    if not isinstance(inputs, dict) or not isinstance(artifacts, dict):
        raise ProxyTrainingFinalizationError("proxy training input hashes are missing")
    if (
        inputs.get("train_chunks_sha256")
        != artifacts["train_chunks"]["sha256"]
        or inputs.get("validation_documents_sha256")
        != artifacts["validation_documents"]["sha256"]
        or inputs.get("calibration_documents_used") is not False
        or inputs.get("test_or_frozen_documents_used") is not False
    ):
        raise ProxyTrainingFinalizationError("proxy training input-use contract is invalid")
    if Path(str(execution.get("checkpoint_root") or "")).resolve() != checkpoint_root.resolve():
        raise ProxyTrainingFinalizationError("proxy checkpoint root path binding mismatch")
    training_spec = execution.get("training_spec")
    if (
        not isinstance(training_spec, dict)
        or training_spec.get("proxy_candidate_mode") is not True
        or training_spec.get("test_path") is not None
        or training_spec.get("train_input_mode") != "pre_chunked"
        or training_spec.get("chunk_expand") is not False
        or Path(str(training_spec.get("output_dir") or "")).resolve()
        != checkpoint_root.resolve()
    ):
        raise ProxyTrainingFinalizationError("proxy training spec is not fail-closed")
    base_model = execution.get("base_model")
    if not isinstance(base_model, dict):
        raise ProxyTrainingFinalizationError("base-model attestation is missing")
    for field in (
        "resolved_revision",
        "initial_state_dict_sha256",
        "config_sha256",
        "tokenizer_contract_sha256",
    ):
        value = base_model.get(field)
        if field == "resolved_revision":
            if not isinstance(value, str) or not value:
                raise ProxyTrainingFinalizationError("base-model revision is missing")
        elif not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise ProxyTrainingFinalizationError(
                f"base-model {field} attestation is invalid"
            )
    source = execution.get("source")
    if not isinstance(source, dict):
        raise ProxyTrainingFinalizationError("training source attestation is missing")
    for path_field, hash_field in (
        ("trainer_path", "trainer_sha256"),
        ("entrypoint_path", "entrypoint_sha256"),
    ):
        source_path = Path(str(source.get(path_field) or ""))
        expected_hash = source.get(hash_field)
        if (
            source_path.is_symlink()
            or not source_path.is_file()
            or not isinstance(expected_hash, str)
            or _sha256_file(source_path) != expected_hash
        ):
            raise ProxyTrainingFinalizationError(
                f"training source changed or is missing: {path_field}"
            )
    checkpoints = discover_checkpoints(checkpoint_root)
    actual_attestations = [
        {"name": path.name, "artifact": hash_model_directory(path)}
        for path in checkpoints
    ]
    checkpoint_bytes = json.dumps(
        actual_attestations,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    checkpoint_set_sha256 = _sha256_bytes(checkpoint_bytes)
    if (
        execution.get("checkpoints") != actual_attestations
        or execution.get("checkpoint_set_sha256") != checkpoint_set_sha256
        or complete.get("checkpoint_set_sha256") != checkpoint_set_sha256
    ):
        raise ProxyTrainingFinalizationError(
            "checkpoint bytes do not match the training execution manifest"
        )
    audit = {
        "manifest_sha256": _sha256_bytes(execution_bytes),
        "complete_sha256": _sha256_bytes(complete_bytes),
        "run_id": execution["run_id"],
        "checkpoint_set_sha256": checkpoint_set_sha256,
        "base_model": base_model,
        "source": source,
        "input_use": inputs,
    }
    return checkpoints, audit


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_new(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _artifact_entry(path: Path, *, records: int | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "path": path.name,
        "sha256": _sha256_file(path),
        "bytes": path.stat().st_size,
    }
    if records is not None:
        result["records"] = records
    return result


def _save_clean_model(checkpoint: Path, output: Path) -> None:
    try:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError as exc:
        raise ProxyTrainingFinalizationError(
            "transformers is required to publish the selected checkpoint"
        ) from exc
    tokenizer = AutoTokenizer.from_pretrained(str(checkpoint))
    model = AutoModelForSequenceClassification.from_pretrained(str(checkpoint))
    model.save_pretrained(str(output), safe_serialization=True)
    tokenizer.save_pretrained(str(output))


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"proxy-model-{stamp}-{uuid.uuid4().hex[:10]}"


def _chmod_tree(path: Path) -> None:
    if os.name == "nt":
        return
    for child in path.rglob("*"):
        child.chmod(0o2750 if child.is_dir() else 0o640)
    path.chmod(0o2750)


def finalize_proxy_classifier(
    *,
    training_run_dir: Path,
    checkpoint_root: Path,
    output_root: Path,
    run_id: str | None = None,
    batch_size: int = 8,
    device: str = "auto",
    fnr_target: float = 0.05,
    min_f1_macro: float = CHANCE_LEVEL_F1_MACRO,
    baseline_f1_macro: float | None = None,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    """Select, calibrate, and publish one restricted proxy deployment candidate."""
    if batch_size != SERVING_INFERENCE_BATCH_SIZE:
        raise ProxyTrainingFinalizationError(
            "proxy finalization batch_size must match M5 serving "
            f"({SERVING_INFERENCE_BATCH_SIZE})"
        )
    validation_rows, calibration_rows, materialization_audit = (
        verify_materialized_training_run(training_run_dir)
    )
    checkpoints, training_execution_audit = verify_proxy_training_execution(
        checkpoint_root,
        materialization_audit=materialization_audit,
    )
    checkpoint_hashes = {
        checkpoint.name: hash_model_directory(checkpoint)
        for checkpoint in checkpoints
    }
    validation_batches = {}
    checkpoint_reports: list[dict[str, object]] = []
    for checkpoint in checkpoints:
        before = checkpoint_hashes[checkpoint.name]
        batch = load_model_document_logits(
            checkpoint,
            validation_rows,
            batch_size=batch_size,
            device=device,
            require_fast_overflow=True,
        )
        after = hash_model_directory(checkpoint)
        if after["tree_sha256"] != before["tree_sha256"]:
            raise ProxyTrainingFinalizationError(
                f"checkpoint changed during validation inference: {checkpoint}"
            )
        metrics = evaluate_checkpoint_traces(batch.documents)
        validation_batches[checkpoint.name] = batch
        checkpoint_reports.append(
            {
                "checkpoint": checkpoint.name,
                "artifact": before,
                "metrics": metrics,
                "runtime_attestation": batch.runtime_attestation,
            }
        )
    selected_name, selected_metrics = select_checkpoint(
        [(str(row["checkpoint"]), row["metrics"]) for row in checkpoint_reports]
    )
    selected_checkpoint = checkpoint_root / selected_name
    selected_batch = validation_batches[selected_name]

    run_id_value = run_id or _new_run_id()
    if not _SAFE_RUN_ID.fullmatch(run_id_value):
        raise ProxyTrainingFinalizationError(f"unsafe run id: {run_id_value!r}")
    output_root.mkdir(parents=True, exist_ok=True)
    final_dir = output_root / run_id_value
    if final_dir.exists():
        raise ProxyTrainingFinalizationError(f"final model run already exists: {final_dir}")
    staging = output_root / f".{run_id_value}.staging-{uuid.uuid4().hex}"
    staging.mkdir(exist_ok=False)
    try:
        _save_clean_model(selected_checkpoint, staging)
        clean_model_hash = hash_model_directory(staging)
        calibration_batch = load_model_document_logits(
            staging,
            calibration_rows,
            batch_size=batch_size,
            device=device,
            require_fast_overflow=True,
            apply_bundle_operating_point=True,
        )
        if hash_model_directory(staging)["tree_sha256"] != clean_model_hash["tree_sha256"]:
            raise ProxyTrainingFinalizationError(
                "published model files changed during calibration inference"
            )
        temperature = fit_document_temperature(calibration_batch.documents)
        operating_point = fit_escalation_operating_point(
            calibration_batch.documents,
            temperature=float(temperature["temperature"]),
            fnr_target=fnr_target,
            min_f1_macro=min_f1_macro,
            baseline_f1_macro=baseline_f1_macro,
        )
        calibration_input_sha256 = str(
            materialization_audit["calibration_documents"]["sha256"]
        )
        for calibration_artifact in (temperature, operating_point):
            calibration_artifact["calibration_input_sha256"] = (
                calibration_input_sha256
            )
            calibration_artifact["training_run_manifest_sha256"] = str(
                materialization_audit["manifest_sha256"]
            )
            calibration_artifact["serving_aggregation_contract_sha256"] = str(
                calibration_batch.runtime_attestation["aggregation_contract_sha256"]
            )

        validation_trace_path = staging / "validation_window_logits.jsonl"
        calibration_trace_path = staging / "calibration_window_logits.jsonl"
        selection_path = staging / "checkpoint_selection.json"
        temperature_path = staging / "temperature.json"
        operating_path = staging / "operating_point.json"
        _write_new(validation_trace_path, canonical_trace_bytes(selected_batch.documents))
        _write_new(calibration_trace_path, canonical_trace_bytes(calibration_batch.documents))
        selection_payload = {
            "schema_version": SCHEMA_VERSION,
            "status": "complete",
            "selection_split": "validation_documents",
            "selection_unit": "document",
            "selection_temperature": 1.0,
            "selection_escalation_tau": None,
            "metric": "fnr_high_balanced",
            "tie_break": ["max_f1_macro", "min_nll", "checkpoint_name"],
            "selected_checkpoint": selected_name,
            "selected_metrics": selected_metrics,
            "candidates": checkpoint_reports,
        }
        _write_new(selection_path, _json_bytes(selection_payload))
        _write_new(temperature_path, _json_bytes(temperature))
        _write_new(operating_path, _json_bytes(operating_point))

        artifacts = {
            "checkpoint_selection": _artifact_entry(selection_path),
            "validation_window_logits": _artifact_entry(
                validation_trace_path, records=len(selected_batch.documents)
            ),
            "calibration_window_logits": _artifact_entry(
                calibration_trace_path, records=len(calibration_batch.documents)
            ),
            "temperature": _artifact_entry(temperature_path),
            "operating_point": _artifact_entry(operating_path),
        }
        manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id_value,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "complete",
            "claim_scope": "proxy_training_only_not_customer_document_accuracy",
            "production_eligible": False,
            "artifact_role": "proxy_deployment_candidate",
            "customer_document_deployment_approved": False,
            "inputs": {
                "materialized_training_run": materialization_audit,
                "training_execution": training_execution_audit,
                "checkpoint_root": str(checkpoint_root),
                "checkpoint_count": len(checkpoints),
                "selected_checkpoint": {
                    "path": str(selected_checkpoint),
                    "artifact": checkpoint_hashes[selected_name],
                },
            },
            "contracts": {
                "validation_use": "checkpoint_selection_only",
                "calibration_use": "temperature_and_escalation_tau_only",
                "frozen_or_blind_tuning_allowed": False,
                "fast_tokenizer_overflow_required": True,
                "serving_aggregation": serving_contract_summary(),
                "temperature_order": (
                    "raw_window_logits / T -> window softmax -> length weighted mean -> "
                    "TS/S1 max -> renormalize"
                ),
                "tau_order": "after_temperature_and_document_probability_aggregation",
            },
            "selection": selection_payload,
            "calibration": {
                "runtime_attestation": calibration_batch.runtime_attestation,
                "temperature": temperature,
                "operating_point": operating_point,
            },
            "published_model_before_calibration_metadata": clean_model_hash,
            "artifacts": artifacts,
            "source": {
                "finalizer_sha256": _sha256_file(Path(__file__).resolve()),
                "core_sha256": _sha256_file(
                    (_SRC / "lloydk" / "proxy_training_finalization.py").resolve()
                ),
            },
        }
        manifest_path = staging / "finalization_manifest.json"
        manifest_bytes = _json_bytes(manifest)
        _write_new(manifest_path, manifest_bytes)
        complete: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id_value,
            "committed_at": datetime.now(timezone.utc).isoformat(),
            "manifest_sha256": _sha256_bytes(manifest_bytes),
            "artifacts": artifacts,
        }
        complete_path = staging / "COMPLETE"
        complete_bytes = _json_bytes(complete)
        _write_new(complete_path, complete_bytes)

        # Recheck both independent split files and every checkpoint after the
        # potentially long GPU work, then publish by one directory rename.
        _, _, final_materialization_audit = verify_materialized_training_run(
            training_run_dir
        )
        if final_materialization_audit != materialization_audit:
            raise ProxyTrainingFinalizationError(
                "materialized training run changed during model finalization"
            )
        _, final_training_execution_audit = verify_proxy_training_execution(
            checkpoint_root,
            materialization_audit=final_materialization_audit,
        )
        if final_training_execution_audit != training_execution_audit:
            raise ProxyTrainingFinalizationError(
                "training execution/checkpoint set changed during finalization"
            )
        _chmod_tree(staging)
        staging.rename(final_dir)
        return final_dir, manifest, complete
    except Exception:
        if staging.exists():
            resolved_root = output_root.resolve()
            resolved_staging = staging.resolve()
            if resolved_staging.parent == resolved_root and staging.name.startswith(
                f".{run_id_value}.staging-"
            ):
                shutil.rmtree(staging)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Select epoch checkpoint on independent validation documents, then fit "
            "temperature and escalation tau on independent calibration documents"
        )
    )
    parser.add_argument("--training-run-dir", required=True)
    parser.add_argument("--checkpoint-root", required=True)
    parser.add_argument(
        "--out-root", default="artifacts/proxy_classifier_finalized"
    )
    parser.add_argument("--run-id")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--fnr-target", type=float, default=0.05)
    # 품질 하한(2026-08-11). 기본은 무작위 수준이라 깨진 후보만 막는다.
    # 운영 게이트로 쓸 값은 --baseline-f1-macro 쪽이다(무회귀 강제).
    parser.add_argument("--min-f1-macro", type=float,
                        default=CHANCE_LEVEL_F1_MACRO,
                        help="선택된 운영점의 macro F1 절대 하한")
    parser.add_argument("--baseline-f1-macro", type=float, default=None,
                        help="직전 배포/기준 모델의 macro F1. 주면 무회귀 강제")
    args = parser.parse_args(argv)
    try:
        run_dir, manifest, complete = finalize_proxy_classifier(
            training_run_dir=Path(args.training_run_dir),
            checkpoint_root=Path(args.checkpoint_root),
            output_root=Path(args.out_root),
            run_id=args.run_id,
            batch_size=args.batch_size,
            device=args.device,
            fnr_target=args.fnr_target,
            min_f1_macro=args.min_f1_macro,
            baseline_f1_macro=args.baseline_f1_macro,
        )
    except (OSError, ProxyTrainingFinalizationError, ValueError) as exc:
        print(f"proxy classifier finalization failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "run_id": manifest["run_id"],
                "selected_checkpoint": manifest["selection"]["selected_checkpoint"],
                "temperature": manifest["calibration"]["temperature"]["temperature"],
                "classifier_escalation_tau": manifest["calibration"][
                    "operating_point"
                ]["classifier_escalation_tau"],
                "manifest_sha256": complete["manifest_sha256"],
                "complete": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
