"""Immutable one-model evaluation on the public-real S3 challenge.

The challenge is deliberately separate from the matched four-grade proxy set.
Because every truth label is S3, it can measure public-document false positives
and overclassification severity only.  It cannot support an overall,
balanced, or customer-document accuracy claim.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import uuid

from lloydk.proxy_model_comparison import (
    EXPECTED_PUBLIC_S3_CHALLENGE_DOCUMENTS,
    LABELS,
    SERVING_CHUNK_OVERLAP,
    SERVING_INFERENCE_BATCH_SIZE,
    SERVING_MAX_LENGTH,
    SERVING_SEVERE_AGG_CODES,
    ModelPredictionBatch,
    ProxyComparisonError,
    _atomic_write_new,
    _json_bytes,
    _jsonl_bytes,
    _prediction_rows,
    _sha256_bytes,
    _sha256_file,
    _validate_prediction_batch,
    compute_public_s3_challenge_metrics,
    hash_model_directory,
    load_public_s3_challenge,
    predict_model,
    serving_aggregation_contract,
)


SCHEMA_VERSION = "public-s3-challenge-evaluation-v1"
CLAIM_SCOPE = "public_real_s3_overclassification_challenge_only"
PROHIBITED_CLAIMS = (
    "overall_four_grade_accuracy",
    "balanced_accuracy",
    "customer_document_accuracy",
)
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


ChallengePredictor = Callable[..., ModelPredictionBatch]


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"public-s3-eval-{stamp}-{uuid.uuid4().hex[:10]}"


def _assert_unique_families(rows: Sequence[Mapping[str, object]]) -> None:
    families = [str(row["document_family_id"]) for row in rows]
    duplicates = sorted(
        family for family, count in Counter(families).items() if count > 1
    )
    if duplicates:
        raise ProxyComparisonError(
            "public S3 challenge has duplicate document_family_id values: "
            + json.dumps(duplicates[:20], ensure_ascii=False)
        )


def _evaluation_code_attestation() -> dict[str, object]:
    paths = (
        Path(__file__).resolve(),
        (_PROJECT_ROOT / "src" / "lloydk" / "proxy_model_comparison.py").resolve(),
        (_PROJECT_ROOT / "scripts" / "evaluate_public_s3_challenge.py").resolve(),
    )
    files: list[dict[str, object]] = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ProxyComparisonError(f"evaluation source is missing: {path}")
        try:
            relative = path.relative_to(_PROJECT_ROOT).as_posix()
        except ValueError as exc:
            raise ProxyComparisonError(
                f"evaluation source is outside the project root: {path}"
            ) from exc
        files.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    canonical = json.dumps(
        files, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "files": files,
        "tree_sha256": _sha256_bytes(canonical),
    }


def _challenge_metrics(predictions: Sequence[str]) -> dict[str, object]:
    metrics = compute_public_s3_challenge_metrics(predictions)
    distribution = metrics["prediction_distribution"]
    if not isinstance(distribution, Mapping):
        raise ProxyComparisonError("public S3 prediction distribution is invalid")
    metrics.update(
        {
            "ts_overclassification_count": int(distribution["TS"]),
            "s1_overclassification_count": int(distribution["S1"]),
            "ts_s1_severe_overclassification_count": (
                int(distribution["TS"]) + int(distribution["S1"])
            ),
        }
    )
    return metrics


def _render_markdown(manifest: Mapping[str, object]) -> bytes:
    metrics = manifest.get("metrics")
    if not isinstance(metrics, Mapping):
        raise ProxyComparisonError("challenge manifest metrics are missing")
    distribution = metrics.get("prediction_distribution")
    if not isinstance(distribution, Mapping):
        raise ProxyComparisonError("challenge prediction distribution is missing")
    lines = [
        "# Public-real S3 Overclassification Challenge",
        "",
        "This exact 300-document, all-S3 challenge measures public-document ",
        "false positives and overclassification severity only.",
        "It does not measure overall four-grade, balanced, customer-document, ",
        "or production accuracy.",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| S3 recall | {float(metrics['s3_recall']):.4f} |",
        f"| Any-overclassification / FPR | {float(metrics['public_false_positive_rate']):.4f} |",
        f"| Mean overclassification severity | {float(metrics['mean_overclassification_severity']):.4f} |",
        f"| Maximum overclassification severity | {int(metrics['maximum_overclassification_severity'])} |",
        f"| TS overclassifications | {int(metrics['ts_overclassification_count'])} |",
        f"| S1 overclassifications | {int(metrics['s1_overclassification_count'])} |",
        "",
        "## Predicted grade distribution",
        "",
        "| TS | S1 | S2 | S3 |",
        "|---:|---:|---:|---:|",
        "| "
        + " | ".join(str(int(distribution[grade])) for grade in LABELS)
        + " |",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def evaluate_public_s3_challenge(
    *,
    challenge_path: Path,
    model_dir: Path,
    output_root: Path,
    run_id: str | None = None,
    batch_size: int = SERVING_INFERENCE_BATCH_SIZE,
    device: str = "auto",
    predictor: ChallengePredictor | None = None,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    """Evaluate one model and publish an immutable, hash-attested run."""
    run_id_value = run_id or _new_run_id()
    if not _SAFE_RUN_ID.fullmatch(run_id_value):
        raise ProxyComparisonError(f"unsafe run id: {run_id_value!r}")
    if batch_size < 1:
        raise ProxyComparisonError("batch_size must be positive")
    if device not in {"auto", "cpu", "cuda"}:
        raise ProxyComparisonError(f"unsupported device: {device!r}")
    final_dir = output_root / run_id_value
    if final_dir.exists():
        raise ProxyComparisonError(f"challenge evaluation run already exists: {final_dir}")

    rows, input_audit = load_public_s3_challenge(challenge_path)
    _assert_unique_families(rows)
    if len(rows) != EXPECTED_PUBLIC_S3_CHALLENGE_DOCUMENTS:
        raise ProxyComparisonError("public S3 challenge exact-count invariant failed")

    model_audit = hash_model_directory(model_dir)
    code_audit = _evaluation_code_attestation()
    aggregation_contract = serving_aggregation_contract(
        max_length=SERVING_MAX_LENGTH,
        chunk_overlap=SERVING_CHUNK_OVERLAP,
        severe_codes=SERVING_SEVERE_AGG_CODES,
        forward_batch_size=batch_size,
    )
    contract_sha256 = str(aggregation_contract["contract_sha256"])
    effective_predictor = predictor or predict_model
    batch = effective_predictor(
        model_dir,
        rows,
        batch_size=batch_size,
        device=device,
        max_length=SERVING_MAX_LENGTH,
        chunk_overlap=SERVING_CHUNK_OVERLAP,
        severe_codes=SERVING_SEVERE_AGG_CODES,
    )
    raw_predictions, labels, runtime = _validate_prediction_batch(
        batch,
        expected_count=EXPECTED_PUBLIC_S3_CHALLENGE_DOCUMENTS,
        expected_contract_sha256=contract_sha256,
        model_name="challenge_model",
    )
    if hash_model_directory(model_dir)["tree_sha256"] != model_audit["tree_sha256"]:
        raise ProxyComparisonError("model changed during public S3 challenge evaluation")

    metrics = _challenge_metrics(labels)
    prediction_rows = _prediction_rows(rows, raw_predictions)
    prediction_bytes = _jsonl_bytes(prediction_rows)
    created_at = datetime.now(timezone.utc).isoformat()
    manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id_value,
        "created_at": created_at,
        "status": "complete",
        "claim_scope": CLAIM_SCOPE,
        "human_reviewed_customer_documents": False,
        "valid_interpretation": (
            "False-positive and overclassification behavior on the fixed, "
            "public-real, all-S3 challenge only."
        ),
        "prohibited_claims": list(PROHIBITED_CLAIMS),
        "prohibited_interpretation": (
            "The all-S3 challenge cannot measure overall four-grade accuracy, "
            "balanced accuracy, high-grade recall, customer-document accuracy, "
            "or production accuracy."
        ),
        "evaluation_contract": {
            "records": EXPECTED_PUBLIC_S3_CHALLENGE_DOCUMENTS,
            "truth_label": "S3",
            "document_origin": "public_real",
            "intended_use": "evaluation",
            "evaluation_unit": "document",
            "prediction_mode": "m5_chunked_severe_aggregate_argmax",
            "duplicate_policy": {
                "doc_id": "forbidden",
                "document_family_id": "forbidden",
                "normalized_text_hash": "forbidden",
            },
            "bootstrap_applied": False,
            "primary_proxy_metrics_combined": False,
            "aggregation": aggregation_contract,
        },
        "input": input_audit,
        "model": {
            "artifact": model_audit,
            "aggregation_runtime": runtime,
        },
        "metrics": metrics,
        "evaluation_code": code_audit,
    }
    report_bytes = _render_markdown(manifest)
    manifest["artifacts"] = {
        "predictions": {
            "path": "predictions.jsonl",
            "sha256": _sha256_bytes(prediction_bytes),
            "records": len(prediction_rows),
        },
        "report": {
            "path": "REPORT.md",
            "sha256": _sha256_bytes(report_bytes),
        },
    }
    manifest_bytes = _json_bytes(manifest, indent=2)
    complete: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id_value,
        "committed_at": datetime.now(timezone.utc).isoformat(),
        "claim_scope": CLAIM_SCOPE,
        "artifacts": {
            "manifest": {
                "path": "manifest.json",
                "sha256": _sha256_bytes(manifest_bytes),
            },
            **dict(manifest["artifacts"]),
        },
        "challenge_input_sha256": input_audit["file_sha256"],
        "model_tree_sha256": model_audit["tree_sha256"],
        "evaluation_code_tree_sha256": code_audit["tree_sha256"],
        "aggregation_contract_sha256": contract_sha256,
    }
    complete_bytes = _json_bytes(complete, indent=2)

    output_root.mkdir(parents=True, exist_ok=True)
    staging_dir = output_root / f".{run_id_value}.staging-{uuid.uuid4().hex}"
    staging_dir.mkdir(exist_ok=False)
    try:
        _atomic_write_new(staging_dir / "predictions.jsonl", prediction_bytes)
        _atomic_write_new(staging_dir / "REPORT.md", report_bytes)
        _atomic_write_new(staging_dir / "manifest.json", manifest_bytes)
        _atomic_write_new(staging_dir / "COMPLETE.json", complete_bytes)
        staging_dir.rename(final_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    return final_dir, manifest, complete

