"""Judge high-fidelity proxy candidates with a fail-closed semantic gate.

The input remains synthetic proxy data.  This runner creates automatic
``gold_candidate`` and ``uncertain`` buckets; it never creates human-signed
gold or changes the canonical gold dataset.

Safety properties:

* generator and primary judge must be different, known non-noop models;
* intended grade, primary grade, complete S/V/M votes and derived grade agree;
* the keyword rule is retained as advisory audit evidence, never as a veto;
* exact evidence spans and the eligible proxy-record contract must both pass;
* every input record must retain auditable generation provenance;
* decisions are appended and fsynced to a per-run journal;
* final bucket files are published atomically inside a unique run directory;
* ``COMPLETE.json`` is written last and is the commit marker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
sys.path.insert(0, str(_POC))
sys.path.insert(0, str(_POC / "src"))

from koipa.hygiene import text_hash  # noqa: E402
from koipa.ollama_attestation import (  # noqa: E402
    OllamaAttestationError,
    validate_ollama_attestation,
    verify_ollama_model,
)
from koipa.modules.m3_labeling.rule_engine import (  # noqa: E402
    grade_from_svm,
    has_real_evidence,
)
from koipa.modules.m3_labeling.llm_labeler import (  # noqa: E402
    PROXY_QUALITY_CHECKS,
)
from koipa.proxy_evidence import ProxyEvidenceError, build_evidence_card  # noqa: E402
from koipa.proxy_corpus import validate_proxy_record  # noqa: E402


_BLOCKED_IDENTITIES = frozenset(
    {"", "noop", "unknown", "none", "null", "empty", "unset", "default", "mock", "fake"}
)
_GRADES = frozenset({"TS", "S1", "S2", "S3"})
_INTENDED_USE_CONTRACTS = {
    "evaluation": {
        "catalog_split_role": "frozen_proxy_eval_only",
        "training_use_permitted": False,
        "evaluation_use_permitted": True,
    },
    "training": {
        "catalog_split_role": "train_pool_only",
        "training_use_permitted": True,
        "evaluation_use_permitted": False,
    },
}
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA256_REVISION_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RUN_SCHEMA_VERSION = "proxy-judge-run-v5"
PROXY_GATE_VERSION = "proxy_semantic_quality_v2"
GENERATION_RUN_SCHEMA_VERSION = "proxy-generation-run-v3"
UPSTREAM_ATTESTATION_SCHEMA = "proxy-generation-input-attestation-v1"
ARTIFACT_FILE_MODE = 0o640
ARTIFACT_DIRECTORY_MODE = 0o2750
_FACTORS = ("secrecy", "value", "management")
_DECISION_COLLISIONS = frozenset(
    {
        "label",
        "label_source",
        "review_status",
        "status",
        "rule_grade",
        "rule_confidence",
        "llm_grade",
        "llm_confidence",
        "agreement",
        "flags",
        "review_priority",
        "self_consistency",
        "shadow_grade",
        "gate_version",
    }
)


class ProxyJudgeContractError(ValueError):
    """Input, model-independence, or no-overwrite contract violation."""


@dataclass(frozen=True, order=True)
class ModelIdentity:
    provider: str
    model: str
    canonical_model: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical_model_id(value: object) -> str:
    """Collapse common local/HuggingFace aliases to a comparison identity."""
    raw = str(value or "").strip().lower().replace("\\", "/")
    leaf = raw.rsplit("/", 1)[-1]
    compact = re.sub(r"[^a-z0-9]+", "", leaf)
    family = re.match(r"((?:qwen|gemma|llama|mistral|exaone)[a-z0-9]*?\d+b)", compact)
    return family.group(1) if family else compact


def _is_blocked_identity(value: object) -> bool:
    compact = _canonical_model_id(value)
    return compact in _BLOCKED_IDENTITIES or any(
        compact.startswith(prefix) for prefix in ("noop", "unknown", "mock", "fake")
    )


def _identity(provider: object, model: object, *, context: str) -> ModelIdentity:
    provider_text = str(provider or "").strip()
    model_text = str(model or "").strip()
    if _is_blocked_identity(provider_text):
        raise ProxyJudgeContractError(f"{context}: blocked provider identity")
    if _is_blocked_identity(model_text):
        raise ProxyJudgeContractError(f"{context}: blocked model identity")
    return ModelIdentity(
        provider=provider_text,
        model=model_text,
        canonical_model=_canonical_model_id(model_text),
    )


def _generator_identities(record: Mapping[str, object]) -> set[ModelIdentity]:
    """Extract generator identities without inventing missing provenance."""
    lineage = record.get("generation_lineage")
    entries: list[object]
    if isinstance(lineage, Mapping):
        entries = [lineage]
    elif isinstance(lineage, Sequence) and not isinstance(lineage, (str, bytes)):
        entries = list(lineage)
    else:
        entries = []

    found: set[ModelIdentity] = set()
    for entry in entries:
        if isinstance(entry, str) and entry.startswith("generator:"):
            parts = entry.split(":", 2)
            if len(parts) == 3:
                found.add(_identity(parts[1], parts[2], context="generation_lineage"))
        elif isinstance(entry, Mapping):
            kind = str(entry.get("kind") or entry.get("type") or "").lower()
            if kind == "generator" or "generator_model" in entry:
                provider = entry.get("provider") or entry.get("generator_provider")
                model = entry.get("model") or entry.get("generator_model")
                found.add(_identity(provider, model, context="generation_lineage"))

    if not found:
        raise ProxyJudgeContractError(
            f"{record.get('doc_id') or '<missing-doc-id>'}: generator provenance missing"
        )
    return found


def validate_model_contract(
    records: Sequence[Mapping[str, object]],
    *,
    judge_model: str,
    shadow_model: str | None,
) -> tuple[ModelIdentity, tuple[ModelIdentity, ...], ModelIdentity | None]:
    """Validate real model identities and primary-judge independence."""
    judge = _identity("local_openai", judge_model, context="primary judge")
    generators: set[ModelIdentity] = set()
    for record in records:
        generators.update(_generator_identities(record))

    same = sorted(
        item.model
        for item in generators
        if item.canonical_model == judge.canonical_model
    )
    if same:
        raise ProxyJudgeContractError(
            "generator and primary judge must be independent models: "
            f"judge={judge.model}, generator={same[0]}"
        )

    shadow = None
    if shadow_model is not None:
        shadow = _identity("local_openai", shadow_model, context="shadow judge")
        if shadow.canonical_model == judge.canonical_model:
            raise ProxyJudgeContractError(
                "primary and shadow judge must not use the same model"
            )
    return judge, tuple(sorted(generators)), shadow


def _runtime_provider_identity(component: object, *, context: str) -> ModelIdentity:
    provider = getattr(component, "provider", None)
    if provider is None:
        raise ProxyJudgeContractError(f"{context}: provider identity unavailable")
    return _identity(
        getattr(provider, "name", None),
        getattr(provider, "model", None),
        context=context,
    )


def validate_runtime_judge(
    judge: object,
    *,
    expected_primary: ModelIdentity,
    expected_shadow: ModelIdentity | None,
) -> None:
    """Ensure the instantiated judge did not silently fall back to noop/unknown."""
    primary = getattr(judge, "primary", None)
    actual_primary = _runtime_provider_identity(
        primary, context="runtime primary judge"
    )
    if actual_primary.canonical_model != expected_primary.canonical_model:
        raise ProxyJudgeContractError(
            "runtime primary judge does not match declared judge model"
        )

    runtime_shadow = getattr(judge, "shadow", None)
    if expected_shadow is None:
        if runtime_shadow is not None:
            raise ProxyJudgeContractError("undeclared runtime shadow judge")
        return
    if runtime_shadow is None:
        raise ProxyJudgeContractError(
            "declared shadow judge is not configured at runtime"
        )
    actual_shadow = _runtime_provider_identity(
        runtime_shadow, context="runtime shadow judge"
    )
    if actual_shadow.canonical_model != expected_shadow.canonical_model:
        raise ProxyJudgeContractError(
            "runtime shadow judge does not match declared shadow model"
        )


def load_candidates(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProxyJudgeContractError(
                    f"invalid JSONL at line {line_no}: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise ProxyJudgeContractError(
                    f"candidate at line {line_no} must be an object"
                )
            rows.append(row)
    return rows


def validate_candidate_usage_contract(
    records: Sequence[Mapping[str, object]], *, intended_use: str
) -> dict[str, object]:
    """Bind a judged run to one catalog split and its explicit permissions."""
    expected = _INTENDED_USE_CONTRACTS.get(intended_use)
    if expected is None:
        raise ProxyJudgeContractError(
            "intended_use must be either 'evaluation' or 'training'"
        )
    failures: list[str] = []
    for index, record in enumerate(records, 1):
        if record.get("document_origin") != "synthetic":
            failures.append(f"row {index}: document_origin must be synthetic")
        for field, expected_value in expected.items():
            actual_value = record.get(field)
            mismatch = (
                actual_value is not expected_value
                if isinstance(expected_value, bool)
                else actual_value != expected_value
            )
            if mismatch:
                failures.append(
                    f"row {index}: {field}={actual_value!r}, "
                    f"expected {expected_value!r}"
                )
    if failures:
        preview = "; ".join(failures[:10])
        suffix = f"; plus {len(failures) - 10} more" if len(failures) > 10 else ""
        raise ProxyJudgeContractError(
            f"candidate {intended_use} usage contract failed: {preview}{suffix}"
        )
    return {
        "intended_use": intended_use,
        "catalog_split_role": expected["catalog_split_role"],
        "records": len(records),
        "training_use_permitted": sum(
            record.get("training_use_permitted") is True for record in records
        ),
        "evaluation_use_permitted": sum(
            record.get("evaluation_use_permitted") is True for record in records
        ),
    }


def validate_candidates(
    records: Sequence[Mapping[str, object]], *, intended_use: str = "evaluation"
) -> None:
    if not records:
        raise ProxyJudgeContractError("candidate input is empty")
    validate_candidate_usage_contract(records, intended_use=intended_use)
    seen_ids: set[str] = set()
    seen_texts: set[str] = set()
    errors: list[str] = []
    for index, record in enumerate(records, 1):
        check = validate_proxy_record(record, intended_use=intended_use)
        if not check.ok:
            errors.append(f"row {index}: {','.join(check.errors)}")
        doc_id = str(record.get("doc_id") or "").strip()
        text = str(record.get("text") or "").strip()
        if not text:
            errors.append(f"row {index}: missing canonical text")
        if doc_id in seen_ids:
            errors.append(f"row {index}: duplicate doc_id")
        if text:
            digest = text_hash(text)
            if digest in seen_texts:
                errors.append(f"row {index}: duplicate text")
            seen_texts.add(digest)
        seen_ids.add(doc_id)
    if errors:
        preview = "; ".join(errors[:10])
        suffix = f"; plus {len(errors) - 10} more" if len(errors) > 10 else ""
        raise ProxyJudgeContractError(f"candidate contract failed: {preview}{suffix}")


def create_unique_run_dir(root: Path, run_id: str | None = None) -> Path:
    if not root.exists():
        root.mkdir(mode=ARTIFACT_DIRECTORY_MODE, parents=True)
        os.chmod(root, ARTIFACT_DIRECTORY_MODE)
    elif root.is_symlink() or not root.is_dir():
        raise ProxyJudgeContractError(f"run root must be a regular directory: {root}")
    chosen = run_id or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:10]
    )
    if not _RUN_ID_RE.fullmatch(chosen) or chosen in {".", ".."}:
        raise ProxyJudgeContractError("invalid run_id")
    run_dir = root / chosen
    try:
        run_dir.mkdir(mode=ARTIFACT_DIRECTORY_MODE, exist_ok=False)
        os.chmod(run_dir, ARTIFACT_DIRECTORY_MODE)
    except FileExistsError as exc:
        raise ProxyJudgeContractError(
            f"run directory already exists; refusing overwrite: {run_dir}"
        ) from exc
    return run_dir


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _records_digest(records: Iterable[Mapping[str, object]]) -> str:
    digest = hashlib.sha256()
    for record in records:
        digest.update(_canonical_json_bytes(record))
        digest.update(b"\n")
    return digest.hexdigest()


def _read_json_object(path: Path, *, purpose: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProxyJudgeContractError(f"invalid {purpose}: {path}") from exc
    if not isinstance(value, dict):
        raise ProxyJudgeContractError(f"{purpose} must be a JSON object: {path}")
    return value


def _artifact_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular_artifact(path: Path, *, purpose: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ProxyJudgeContractError(
            f"missing or non-regular upstream {purpose}: {path}"
        )


def _strict_nonnegative_count(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise ProxyJudgeContractError(f"invalid upstream count: {field}")
    try:
        numeric = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ProxyJudgeContractError(f"invalid upstream count: {field}") from exc
    if numeric < 0 or str(value).strip() != str(numeric):
        raise ProxyJudgeContractError(f"invalid upstream count: {field}")
    return numeric


def _count_jsonl_objects(path: Path, *, purpose: str) -> int:
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProxyJudgeContractError(
                    f"invalid upstream {purpose} JSONL at line {line_no}"
                ) from exc
            if not isinstance(row, dict):
                raise ProxyJudgeContractError(
                    f"upstream {purpose} row {line_no} must be an object"
                )
            count += 1
    return count


def attest_generation_input(
    input_path: Path,
    *,
    records: Sequence[Mapping[str, object]] | None = None,
    intended_use: str = "evaluation",
) -> dict:
    """Verify and bind a judge input to one committed generation run.

    The judge accepts only the canonical ``candidates.jsonl`` published by the
    generation runner.  Its sibling COMPLETE marker, manifest, rejected bucket,
    and stats must all be present and mutually hash/count consistent.  This is
    deliberately rechecked inside the judge process instead of trusting a path
    or a caller-supplied digest.
    """
    path = Path(input_path)
    if path.name != "candidates.jsonl":
        raise ProxyJudgeContractError(
            "judge input must be the generation run's canonical candidates.jsonl"
        )
    parent = path.parent
    artifacts = {
        "candidates": path,
        "manifest": parent / "manifest.json",
        "rejected": parent / "rejected.jsonl",
        "stats": parent / "stats.json",
        "complete": parent / "COMPLETE.json",
    }
    for purpose, artifact_path in artifacts.items():
        _require_regular_artifact(artifact_path, purpose=purpose)

    complete = _read_json_object(artifacts["complete"], purpose="generation COMPLETE")
    manifest = _read_json_object(artifacts["manifest"], purpose="generation manifest")
    stats = _read_json_object(artifacts["stats"], purpose="generation stats")
    if complete.get("schema_version") != GENERATION_RUN_SCHEMA_VERSION:
        raise ProxyJudgeContractError("unsupported upstream generation COMPLETE schema")
    if manifest.get("schema_version") != GENERATION_RUN_SCHEMA_VERSION:
        raise ProxyJudgeContractError("unsupported upstream generation manifest schema")
    if manifest.get("status") != "complete":
        raise ProxyJudgeContractError("upstream generation manifest is not complete")
    if complete.get("target_met") is not True or stats.get("target_met") is not True:
        raise ProxyJudgeContractError("upstream generation target_met is not true")

    run_ids = {
        str(value or "")
        for value in (
            complete.get("run_id"),
            manifest.get("run_id"),
            stats.get("run_id"),
        )
    }
    if len(run_ids) != 1 or "" in run_ids:
        raise ProxyJudgeContractError("upstream generation run_id mismatch")
    generation_run_id = next(iter(run_ids))
    generation_namespace = str(manifest.get("generation_namespace") or "")
    if (
        not _RUN_ID_RE.fullmatch(generation_namespace)
        or generation_namespace in {".", ".."}
        or complete.get("generation_namespace") != generation_namespace
        or stats.get("generation_namespace") != generation_namespace
    ):
        raise ProxyJudgeContractError(
            "upstream generation namespace envelope mismatch"
        )
    generation_namespace_sha256 = hashlib.sha256(
        generation_namespace.encode("utf-8")
    ).hexdigest()
    if complete.get("run_contract_sha256") != manifest.get("run_contract_sha256"):
        raise ProxyJudgeContractError("upstream generation run contract mismatch")

    actual_hashes = {
        name: _artifact_sha256(artifacts[name])
        for name in ("manifest", "candidates", "rejected", "stats", "complete")
    }
    complete_hash_fields = {
        "manifest": "manifest_sha256",
        "candidates": "candidates_sha256",
        "rejected": "rejected_sha256",
        "stats": "stats_sha256",
    }
    for name, field in complete_hash_fields.items():
        if complete.get(field) != actual_hashes[name]:
            raise ProxyJudgeContractError(
                f"upstream generation {name} SHA-256 mismatch"
            )

    final_artifacts = manifest.get("final_artifacts")
    if not isinstance(final_artifacts, Mapping):
        raise ProxyJudgeContractError("upstream manifest lacks final_artifacts")
    expected_manifest_fields = {
        "candidates": ("candidates.jsonl", "candidates_sha256"),
        "rejected": ("rejected.jsonl", "rejected_sha256"),
        "stats": ("stats.json", "stats_sha256"),
    }
    for name, (filename, hash_field) in expected_manifest_fields.items():
        if final_artifacts.get(name) != filename:
            raise ProxyJudgeContractError(
                f"upstream manifest points to a noncanonical {name} artifact"
            )
        if final_artifacts.get(hash_field) != actual_hashes[name]:
            raise ProxyJudgeContractError(f"upstream manifest {name} SHA-256 mismatch")
    if manifest.get("stats") != stats:
        raise ProxyJudgeContractError("upstream manifest embedded stats mismatch")

    file_records = load_candidates(path)
    validate_candidates(file_records, intended_use=intended_use)
    usage_contract = validate_candidate_usage_contract(
        file_records, intended_use=intended_use
    )
    run_contract_sha256 = str(manifest.get("run_contract_sha256") or "")
    if not _SHA256_RE.fullmatch(run_contract_sha256):
        raise ProxyJudgeContractError("invalid upstream generation run contract digest")
    contract_material = {
        "schema_version": manifest.get("schema_version"),
        "generation_namespace": generation_namespace,
        "catalog_version": manifest.get("catalog_version"),
        "selection_targets": manifest.get("selection_targets"),
        "max_quality_retries": manifest.get("max_quality_retries"),
    }
    # Generation contracts include strict scenario quotas, a distinct
    # final/base target and deterministic family partitioning.  Reconstruct
    # every present optional field exactly before trusting the envelope.
    for field in (
        "selection_targets_by_scenario",
        "base_final_targets",
        "base_final_targets_by_scenario",
        "partition",
        "fact_ledger_materialization_schema",
    ):
        if field in manifest:
            contract_material[field] = manifest.get(field)
    for field, value in manifest.items():
        if field != "run_contract_sha256" and re.fullmatch(
            r"[a-z][a-z0-9_]*_sha256", field
        ):
            if not _SHA256_RE.fullmatch(str(value or "")):
                raise ProxyJudgeContractError(
                    f"invalid upstream generation contract digest: {field}"
                )
            contract_material[field] = value
    recomputed_contract_sha256 = hashlib.sha256(
        _canonical_json_bytes(contract_material)
    ).hexdigest()
    if recomputed_contract_sha256 != run_contract_sha256:
        raise ProxyJudgeContractError(
            "upstream generation run contract digest mismatch"
        )
    provider = manifest.get("provider")
    if not isinstance(provider, Mapping):
        raise ProxyJudgeContractError(
            "upstream generation provider identity is missing"
        )
    provider_runtime = str(provider.get("runtime") or "").strip()
    provider_model = str(provider.get("model") or "").strip()
    provider_revision = str(provider.get("revision") or "").strip()
    _identity(provider_runtime, provider_model, context="upstream generation provider")
    if not _SHA256_REVISION_RE.fullmatch(provider_revision):
        raise ProxyJudgeContractError(
            "upstream generation model revision is not a pinned manifest digest"
        )
    recorded_model_attestations = {
        "manifest": manifest.get("model_attestation"),
        "manifest latest": manifest.get("latest_model_attestation"),
        "COMPLETE": complete.get("model_attestation"),
    }
    validated_model_attestations: dict[str, dict[str, object]] = {}
    for context, value in recorded_model_attestations.items():
        try:
            validated_model_attestations[context] = validate_ollama_attestation(
                value, require_verified=True
            )
        except OllamaAttestationError as exc:
            raise ProxyJudgeContractError(
                f"upstream generation {context} model attestation is invalid"
            ) from exc
    model_attestation_binding = str(
        validated_model_attestations["manifest"]["binding_sha256"]
    )
    if any(
        attestation["binding_sha256"] != model_attestation_binding
        for attestation in validated_model_attestations.values()
    ):
        raise ProxyJudgeContractError(
            "upstream generation model attestation bindings disagree"
        )
    if any(
        attestation.get("requested_model") != provider_model
        or attestation.get("expected_model_digest") != provider_revision
        or attestation.get("live_model_digest") != provider_revision
        for attestation in validated_model_attestations.values()
    ):
        raise ProxyJudgeContractError(
            "upstream generation model attestation identity mismatch"
        )
    if (
        manifest.get("model_runtime_attestation_sha256")
        != model_attestation_binding
        or complete.get("model_runtime_attestation_sha256")
        != model_attestation_binding
        or provider.get("model_attestation_binding_sha256")
        != model_attestation_binding
        or provider.get("endpoint_identity_sha256")
        != validated_model_attestations["manifest"].get(
            "endpoint_identity_sha256"
        )
    ):
        raise ProxyJudgeContractError(
            "upstream generation model attestation envelope mismatch"
        )
    required_contract_fields = [
        "schema_version",
        "generation_namespace",
        "catalog_version",
        "catalog_sha256",
        "code_sha256",
        "provider_identity_sha256",
        "model_identity_sha256",
        "model_runtime_attestation_sha256",
        "plan_sha256",
        "selection_targets",
        "max_quality_retries",
        "run_contract_sha256",
    ]
    required_contract_fields.extend(
        field
        for field in (
            "selection_targets_by_scenario",
            "base_final_targets",
            "base_final_targets_by_scenario",
            "partition",
        )
        if field in manifest
    )
    resume_keys: set[str] = set()
    for index, row in enumerate(file_records, 1):
        if row.get("generation_run_id") != generation_run_id:
            raise ProxyJudgeContractError(
                f"upstream candidate generation_run_id mismatch at row {index}"
            )
        if row.get("generation_outcome") != "candidate":
            raise ProxyJudgeContractError(
                f"upstream candidate outcome mismatch at row {index}"
            )
        if (
            row.get("generation_namespace") != generation_namespace
            or row.get("generation_namespace_sha256")
            != generation_namespace_sha256
            or not str(row.get("doc_id") or "").startswith(
                f"proxy-{generation_namespace_sha256}-"
            )
        ):
            raise ProxyJudgeContractError(
                f"upstream candidate generation namespace mismatch at row {index}"
            )
        resume_key = str(row.get("generation_resume_key") or "").strip()
        if not resume_key or resume_key in resume_keys:
            raise ProxyJudgeContractError(
                f"upstream candidate resume key missing or duplicate at row {index}"
            )
        resume_keys.add(resume_key)
        row_contract = row.get("generation_contract")
        if not isinstance(row_contract, Mapping):
            raise ProxyJudgeContractError(
                f"upstream candidate generation contract missing at row {index}"
            )
        if any(
            field not in row_contract or row_contract.get(field) != manifest.get(field)
            for field in required_contract_fields
        ):
            raise ProxyJudgeContractError(
                f"upstream candidate generation contract mismatch at row {index}"
            )
        if (
            row_contract.get("provider") != provider_runtime
            or row_contract.get("model") != provider_model
            or row_contract.get("model_revision") != provider_revision
        ):
            raise ProxyJudgeContractError(
                f"upstream candidate provider/model binding mismatch at row {index}"
            )
        try:
            row_model_attestation = validate_ollama_attestation(
                row_contract.get("model_attestation"), require_verified=True
            )
        except OllamaAttestationError as exc:
            raise ProxyJudgeContractError(
                f"upstream candidate model attestation invalid at row {index}"
            ) from exc
        if (
            row_model_attestation.get("binding_sha256")
            != model_attestation_binding
            or row_model_attestation.get("requested_model") != provider_model
            or row_model_attestation.get("expected_model_digest")
            != provider_revision
            or row_model_attestation.get("live_model_digest") != provider_revision
        ):
            raise ProxyJudgeContractError(
                f"upstream candidate model attestation mismatch at row {index}"
            )
    canonical_input_sha256 = _records_digest(file_records)
    if canonical_input_sha256 != actual_hashes["candidates"]:
        raise ProxyJudgeContractError(
            "upstream candidates file is not the canonical generation JSONL"
        )
    if records is not None and _records_digest(records) != canonical_input_sha256:
        raise ProxyJudgeContractError(
            "in-memory judge records do not match the attested candidates file"
        )
    candidate_count = len(file_records)
    rejected_count = _count_jsonl_objects(artifacts["rejected"], purpose="rejected")
    stats_candidates = _strict_nonnegative_count(
        stats.get("candidates"), field="candidates"
    )
    stats_rejected = _strict_nonnegative_count(stats.get("rejected"), field="rejected")
    stats_completed = _strict_nonnegative_count(
        stats.get("completed"), field="completed"
    )
    selected_count = _strict_nonnegative_count(
        stats.get("selection_target_total"), field="selection_target_total"
    )
    if stats_candidates != candidate_count or selected_count != candidate_count:
        raise ProxyJudgeContractError(
            "upstream selected candidate count does not match judge input"
        )
    if stats_rejected != rejected_count:
        raise ProxyJudgeContractError("upstream rejected count mismatch")
    # A completed generation item is either selected or rejected.  Rejections
    # legitimately make completed larger than the judge input count.
    if stats_completed != candidate_count + rejected_count:
        raise ProxyJudgeContractError("upstream completed count mismatch")

    target_by_grade = stats.get("selection_target_by_grade")
    candidate_by_grade = stats.get("candidate_by_grade")
    if not isinstance(target_by_grade, Mapping) or not isinstance(
        candidate_by_grade, Mapping
    ):
        raise ProxyJudgeContractError("upstream grade counts are missing")
    actual_by_grade = dict(
        sorted(Counter(str(row.get("label") or "") for row in file_records).items())
    )
    normalized_targets = {
        str(grade): _strict_nonnegative_count(count, field=f"target:{grade}")
        for grade, count in target_by_grade.items()
    }
    normalized_candidates = {
        str(grade): _strict_nonnegative_count(count, field=f"candidate:{grade}")
        for grade, count in candidate_by_grade.items()
    }
    if sum(normalized_targets.values()) != selected_count:
        raise ProxyJudgeContractError("upstream selection target grade sum mismatch")
    if (
        normalized_targets != actual_by_grade
        or normalized_candidates != actual_by_grade
    ):
        raise ProxyJudgeContractError("upstream candidate grade counts mismatch")

    raw_target_by_scenario = stats.get("selection_target_by_scenario")
    raw_candidate_by_scenario = stats.get("candidate_by_scenario")
    raw_base_by_scenario = stats.get("base_final_target_by_scenario")
    scenario_contract_present = "selection_targets_by_scenario" in manifest
    if not all(
        isinstance(value, Mapping)
        for value in (
            raw_target_by_scenario,
            raw_candidate_by_scenario,
            raw_base_by_scenario,
        )
    ):
        if scenario_contract_present:
            raise ProxyJudgeContractError("upstream scenario counts are missing")
        raw_target_by_scenario = {}
        raw_candidate_by_scenario = {}
        raw_base_by_scenario = {}
    target_by_scenario = {
        str(key): _strict_nonnegative_count(value, field=f"scenario_target:{key}")
        for key, value in raw_target_by_scenario.items()
    }
    candidate_by_scenario = {
        str(key): _strict_nonnegative_count(value, field=f"scenario_candidate:{key}")
        for key, value in raw_candidate_by_scenario.items()
    }
    base_by_scenario = {
        str(key): _strict_nonnegative_count(value, field=f"scenario_base:{key}")
        for key, value in raw_base_by_scenario.items()
    }
    actual_by_scenario = dict(
        sorted(
            Counter(str(row.get("scenario_id") or "") for row in file_records).items()
        )
    )
    if scenario_contract_present and (
        target_by_scenario != actual_by_scenario
        or candidate_by_scenario != actual_by_scenario
        or set(base_by_scenario) != set(actual_by_scenario)
        or any(
            base_by_scenario[key] < 1
            or base_by_scenario[key] > actual_by_scenario[key]
            for key in actual_by_scenario
        )
    ):
        raise ProxyJudgeContractError("upstream candidate scenario counts mismatch")

    profile_by_scenario: dict[str, str] = {}
    actual_by_factor_profile: Counter[str] = Counter()
    for row in file_records:
        scenario_id = str(row.get("scenario_id") or "")
        profile = str(row.get("factor_profile_id") or "")
        previous = profile_by_scenario.setdefault(scenario_id, profile)
        if previous != profile:
            raise ProxyJudgeContractError(
                f"upstream scenario maps to multiple factor profiles: {scenario_id}"
            )
        actual_by_factor_profile[profile] += 1
    raw_candidate_by_profile = stats.get("candidate_by_factor_profile")
    if scenario_contract_present and not isinstance(raw_candidate_by_profile, Mapping):
        raise ProxyJudgeContractError("upstream factor-profile counts are missing")
    if not isinstance(raw_candidate_by_profile, Mapping):
        raw_candidate_by_profile = {}
    candidate_by_factor_profile = {
        str(key): _strict_nonnegative_count(value, field=f"profile_candidate:{key}")
        for key, value in raw_candidate_by_profile.items()
    }
    if scenario_contract_present and candidate_by_factor_profile != dict(
        sorted(actual_by_factor_profile.items())
    ):
        raise ProxyJudgeContractError(
            "upstream candidate factor-profile counts mismatch"
        )

    target_by_factor_profile: Counter[str] = Counter()
    base_by_factor_profile: Counter[str] = Counter()
    for scenario_id, target in target_by_scenario.items():
        profile = profile_by_scenario[scenario_id]
        target_by_factor_profile[profile] += target
        base_by_factor_profile[profile] += base_by_scenario[scenario_id]

    attestation = {
        "schema": UPSTREAM_ATTESTATION_SCHEMA,
        "status": "verified",
        "generation_run_id": generation_run_id,
        "generation_namespace": generation_namespace,
        "generation_namespace_sha256": generation_namespace_sha256,
        "generation_run_contract_sha256": manifest.get("run_contract_sha256"),
        "generation_provider": {
            "runtime": provider_runtime,
            "model": provider_model,
            "model_revision": provider_revision,
        },
        "generation_model_attestation": dict(
            validated_model_attestations["COMPLETE"]
        ),
        "generation_model_attestation_binding_sha256": (
            model_attestation_binding
        ),
        "generation_complete_sha256": actual_hashes["complete"],
        "generation_manifest_sha256": actual_hashes["manifest"],
        "candidates_sha256": actual_hashes["candidates"],
        "rejected_sha256": actual_hashes["rejected"],
        "stats_sha256": actual_hashes["stats"],
        "input_count": candidate_count,
        "completed_count": stats_completed,
        "rejected_count": rejected_count,
        "selection_target_total": selected_count,
        "candidate_by_grade": actual_by_grade,
        "candidate_by_scenario": actual_by_scenario,
        "selection_target_by_scenario": dict(sorted(target_by_scenario.items())),
        "base_final_target_by_scenario": dict(sorted(base_by_scenario.items())),
        "candidate_by_factor_profile": dict(
            sorted(actual_by_factor_profile.items())
        ),
        "selection_target_by_factor_profile": dict(
            sorted(target_by_factor_profile.items())
        ),
        "base_final_target_by_factor_profile": dict(
            sorted(base_by_factor_profile.items())
        ),
        "factor_profile_by_scenario": dict(sorted(profile_by_scenario.items())),
        "usage_contract": usage_contract,
        "target_met": True,
    }
    attestation["attestation_sha256"] = hashlib.sha256(
        _canonical_json_bytes(attestation)
    ).hexdigest()
    return attestation


def _legacy_unattested_input(
    records: Sequence[Mapping[str, object]],
    *,
    input_reference: str | None,
    intended_use: str,
) -> dict:
    """Make an explicit audit marker for the narrow migration/testing bypass."""
    usage_contract = validate_candidate_usage_contract(
        records, intended_use=intended_use
    )
    material = {
        "schema": "legacy-unattested-proxy-input-v1",
        "status": "explicit_legacy_override",
        "input_reference": input_reference,
        "input_count": len(records),
        "input_sha256": _records_digest(records),
        "usage_contract": usage_contract,
        "target_met": None,
    }
    material["attestation_sha256"] = hashlib.sha256(
        _canonical_json_bytes(material)
    ).hexdigest()
    return material


def _static_rule_pipeline() -> tuple[object, str]:
    """Build a deterministic rule side without consulting an operational DB."""
    from koipa.modules.m3_labeling.pipeline import LabelingPipeline  # noqa: PLC0415
    from koipa.modules.m3_labeling.rule_engine import LabelRuleEngine  # noqa: PLC0415
    from koipa.modules.m3_labeling.seeds import KEYWORD_SEEDS  # noqa: PLC0415

    seed_bytes = json.dumps(
        KEYWORD_SEEDS,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    seed_sha256 = hashlib.sha256(seed_bytes).hexdigest()
    pipeline = LabelingPipeline(rule_engine=LabelRuleEngine(seeds=KEYWORD_SEEDS))
    return pipeline, seed_sha256


def _record_digest(record: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json_bytes(record)).hexdigest()


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Publish one file atomically and never replace a completed artifact."""
    if path.exists():
        raise ProxyJudgeContractError(f"refusing to overwrite artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, ARTIFACT_FILE_MODE)
        if path.exists():
            raise ProxyJudgeContractError(f"refusing to overwrite artifact: {path}")
        os.replace(temp_path, path)
        os.chmod(path, ARTIFACT_FILE_MODE)
    finally:
        temp_path.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: object, *, replace: bool = False) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    if not replace:
        _atomic_write_bytes(path, data)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, ARTIFACT_FILE_MODE)
        os.replace(temp_path, path)
        os.chmod(path, ARTIFACT_FILE_MODE)
    finally:
        temp_path.unlink(missing_ok=True)


def _atomic_write_jsonl(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    payload = b"".join(_canonical_json_bytes(row) + b"\n" for row in rows)
    _atomic_write_bytes(path, payload)


class _RecordingRulePipeline:
    def __init__(self, inner: object) -> None:
        self.inner = inner
        self.results: dict[str, object] = {}

    def label(self, text: str) -> object:
        result = self.inner.label(text)  # type: ignore[attr-defined]
        self.results[text_hash(text)] = result
        return result


class _RecordingJudge:
    def __init__(self, inner: object) -> None:
        self.inner = inner
        self.results: dict[str, object] = {}

    def judge(self, text: str) -> object:
        result = self.inner.judge(text)  # type: ignore[attr-defined]
        self.results[text_hash(text)] = result
        return result


def _span_dict(span: object) -> dict:
    return {
        "start": int(getattr(span, "start", 0) or 0),
        "end": int(getattr(span, "end", 0) or 0),
        "text": str(getattr(span, "text", "") or ""),
        "weight": float(getattr(span, "weight", 0.0) or 0.0),
        "tag": getattr(span, "tag", None),
    }


def _strict_factor_scores(value: object) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    scores: dict[str, int] = {}
    for factor in _FACTORS:
        if isinstance(value.get(factor), bool):
            return None
        try:
            numeric = float(value[factor])
        except (KeyError, TypeError, ValueError):
            return None
        if not numeric.is_integer() or int(numeric) not in {0, 1, 2}:
            return None
        scores[factor] = int(numeric)
    return scores


def _strict_int(value: object, *, minimum: int = 0) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or not numeric.is_integer():
        return None
    integer = int(numeric)
    return integer if integer >= minimum else None


def _strict_factor_votes(value: object) -> dict[str, dict[int, int]] | None:
    if not isinstance(value, Mapping):
        return None
    normalized: dict[str, dict[int, int]] = {}
    for factor in _FACTORS:
        distribution = value.get(factor)
        if not isinstance(distribution, Mapping):
            return None
        counts: dict[int, int] = {}
        for raw_level, raw_count in distribution.items():
            level = _strict_int(raw_level)
            count = _strict_int(raw_count, minimum=1)
            if level is None or count is None:
                return None
            if level not in {0, 1, 2}:
                return None
            counts[level] = counts.get(level, 0) + count
        normalized[factor] = counts
    return normalized


def _strict_quality_votes(value: object) -> dict[str, dict[bool, int]] | None:
    """Accept only literal bool vote keys and positive integer counts."""
    if not isinstance(value, Mapping):
        return None
    normalized: dict[str, dict[bool, int]] = {}
    for check in PROXY_QUALITY_CHECKS:
        distribution = value.get(check)
        if not isinstance(distribution, Mapping):
            return None
        counts: dict[bool, int] = {}
        for raw_state, raw_count in distribution.items():
            if type(raw_state) is not bool:
                return None
            count = _strict_int(raw_count, minimum=1)
            if count is None:
                return None
            counts[raw_state] = counts.get(raw_state, 0) + count
        normalized[check] = counts
    return normalized


def _quality_span_attestation(text: str, quote: str) -> dict[str, object]:
    start = text.index(quote)
    return {
        "start": start,
        "end": start + len(quote),
        "quote": quote,
        "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
    }


def _document_quality_audit(
    *,
    judge_result: object | None,
    text: str,
    sample_count: int,
) -> tuple[dict[str, object], list[str]]:
    """Validate every proxy-primary quality sample without coercion.

    The per-sample record is authoritative. Aggregate votes supplied by the
    judge are recomputed and compared so a later consumer can detect tampering.
    """
    failures: list[str] = []
    required = getattr(judge_result, "document_quality_required", None) is True
    if not required:
        failures.append("document_quality_not_required")

    raw_samples = getattr(judge_result, "quality_samples", None)
    samples = raw_samples if isinstance(raw_samples, list) else []
    if sample_count < 1 or len(samples) != sample_count:
        failures.append("invalid_document_quality_samples")

    derived_votes: dict[str, Counter[bool]] = {
        check: Counter() for check in PROXY_QUALITY_CHECKS
    }
    derived_coverage = {check: 0 for check in PROXY_QUALITY_CHECKS}
    normalized_samples: list[dict[str, object]] = []
    issue_errors: list[str] = []

    for position, raw_sample in enumerate(samples, start=1):
        if not isinstance(raw_sample, Mapping):
            failures.append("invalid_document_quality_samples")
            continue
        sample_index = _strict_int(raw_sample.get("sample_index"), minimum=1)
        if sample_index != position:
            failures.append("invalid_document_quality_samples")
        raw_checks = raw_sample.get("checks")
        checks = raw_checks if isinstance(raw_checks, Mapping) else {}
        raw_issues = raw_sample.get("issues")
        issues = raw_issues if isinstance(raw_issues, list) else []
        if not isinstance(raw_checks, Mapping) or not isinstance(raw_issues, list):
            failures.append("invalid_document_quality_samples")

        normalized_checks: dict[str, object] = {}
        for check in PROXY_QUALITY_CHECKS:
            value = checks.get(check)
            normalized_checks[check] = value
            if type(value) is bool:
                derived_votes[check][value] += 1
                derived_coverage[check] += 1
            else:
                failures.append(f"incomplete_document_quality:{check}")

        normalized_issues: list[object] = []
        valid_issue_checks: Counter[str] = Counter()
        for raw_issue in issues:
            if not isinstance(raw_issue, Mapping):
                issue_errors.append("invalid_document_quality_issue")
                normalized_issues.append(raw_issue)
                continue
            issue = dict(raw_issue)
            check = issue.get("check")
            reason = issue.get("reason")
            spans = issue.get("spans")
            valid = (
                check in PROXY_QUALITY_CHECKS
                and isinstance(reason, str)
                and bool(reason.strip())
                and isinstance(spans, list)
                and bool(spans)
                and all(
                    isinstance(span, str) and bool(span) and span in text
                    for span in spans
                )
                and len(spans) == len(set(spans))
                and (check != "non_repetitive" or len(spans) >= 2)
            )
            if not valid:
                issue_errors.append("invalid_document_quality_issue")
                normalized_issues.append(issue)
                continue
            issue["spans"] = [_quality_span_attestation(text, span) for span in spans]
            normalized_issues.append(issue)
            valid_issue_checks[str(check)] += 1

        for check in PROXY_QUALITY_CHECKS:
            value = checks.get(check)
            if value is False and not valid_issue_checks[check]:
                issue_errors.append(f"missing_document_quality_issue:{check}")
            if value is True and valid_issue_checks[check]:
                issue_errors.append(f"contradictory_document_quality_issue:{check}")
        normalized_samples.append(
            {
                "sample_index": sample_index,
                "checks": normalized_checks,
                "issues": normalized_issues,
            }
        )

    failures.extend(issue_errors)
    declared_votes = _strict_quality_votes(getattr(judge_result, "quality_votes", None))
    raw_coverage = getattr(judge_result, "quality_coverage", None)
    declared_coverage: dict[str, int] | None = None
    if isinstance(raw_coverage, Mapping):
        coverage_values = {
            check: _strict_int(raw_coverage.get(check))
            for check in PROXY_QUALITY_CHECKS
        }
        if all(value is not None for value in coverage_values.values()):
            declared_coverage = {
                check: int(coverage_values[check]) for check in PROXY_QUALITY_CHECKS
            }
    derived_plain = {
        check: dict(derived_votes[check]) for check in PROXY_QUALITY_CHECKS
    }
    if declared_votes != derived_plain or declared_coverage != derived_coverage:
        failures.append("invalid_document_quality_vote_audit")

    all_passed: dict[str, bool] = {}
    for check in PROXY_QUALITY_CHECKS:
        distribution = derived_plain[check]
        complete = (
            sample_count > 0
            and derived_coverage[check] == sample_count
            and sum(distribution.values()) == sample_count
        )
        if not complete:
            failures.append(f"incomplete_document_quality:{check}")
            all_passed[check] = False
        elif distribution == {True: sample_count}:
            all_passed[check] = True
        elif len(distribution) > 1:
            failures.append(f"document_quality_disagreement:{check}")
            all_passed[check] = False
        else:
            failures.append(f"document_quality_failed:{check}")
            all_passed[check] = False

    failures = list(dict.fromkeys(failures))
    gate_passed = not failures and all(all_passed.values())
    return (
        {
            "primary_quality_required": required,
            "primary_quality_samples": normalized_samples,
            "primary_quality_votes": {
                check: {
                    ("true" if state else "false"): count
                    for state, count in distribution.items()
                }
                for check, distribution in derived_plain.items()
            },
            "primary_quality_coverage": derived_coverage,
            "quality_check_passed": all_passed,
            "document_quality_gate_passed": gate_passed,
            "document_quality_gate_failures": failures,
        },
        failures,
    )


def _positive_vote_counts(value: object) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    counts: dict[str, int] = {}
    for raw_grade, raw_count in value.items():
        grade = str(raw_grade)
        count = _strict_int(raw_count, minimum=1)
        if count is None:
            return None
        counts[grade] = counts.get(grade, 0) + count
    return counts


def _factor_grade(scores: Mapping[str, int] | None) -> str | None:
    if scores is None:
        return None
    return grade_from_svm(scores["secrecy"], scores["value"], scores["management"])


def _finite_float(value: object, *, default: float = 0.0) -> tuple[float, bool]:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default, False
    return (numeric, True) if math.isfinite(numeric) else (default, False)


def _consensus_evidence(
    *,
    candidate: Mapping[str, object],
    text: str,
    rule_result: object | None,
    rule_error: str | None,
    judge_result: object | None,
    judge_error: str | None,
    require_evidence: bool,
    min_self_consistency: float,
) -> tuple[dict, list[str], list[str]]:
    """Return the semantic gate audit, hard failures, and advisory warnings."""
    intended = str(candidate.get("label") or "")
    expected_scores = _strict_factor_scores(candidate.get("expected_factor_scores"))
    expected_grade = _factor_grade(expected_scores)

    raw_rule = getattr(rule_result, "rule_result", None)
    try:
        rule_evidence_present = bool(raw_rule) and has_real_evidence(raw_rule)
        rule_spans = [_span_dict(span) for span in getattr(rule_result, "evidence", [])]
    except Exception:  # malformed advisory output must not become a lexical veto
        rule_evidence_present = False
        rule_spans = []
        rule_error = rule_error or "RuleAuditError"
    raw_rule_grade = getattr(rule_result, "grade", None)
    rule_grade = (
        raw_rule_grade.value
        if hasattr(raw_rule_grade, "value")
        else str(raw_rule_grade or "")
    )
    if rule_grade not in _GRADES:
        rule_grade = None
    rule_confidence, _ = _finite_float(getattr(rule_result, "confidence", 0.0) or 0.0)

    judge_grade = str(getattr(judge_result, "grade", "") or "")
    if judge_grade not in _GRADES:
        judge_grade = None
    raw_votes = _positive_vote_counts(getattr(judge_result, "votes", None))
    votes = raw_votes or {}
    vote_count = sum(votes.values())
    valid_vote_count = sum(count for grade, count in votes.items() if grade in _GRADES)
    parse_fail_count = votes.get("PARSE_FAIL", 0)
    strict_sample_count = _strict_int(
        getattr(judge_result, "sample_count", 0) or 0, minimum=1
    )
    sample_count = strict_sample_count or 0
    primary_scores = _strict_factor_scores(getattr(judge_result, "factor_scores", None))
    primary_derived_grade = _factor_grade(primary_scores)
    factor_votes = _strict_factor_votes(getattr(judge_result, "factor_votes", None))
    raw_coverage = getattr(judge_result, "factor_coverage", None)
    factor_coverage: dict[str, int] | None = None
    if isinstance(raw_coverage, Mapping):
        normalized_coverage = {
            factor: _strict_int(raw_coverage.get(factor)) for factor in _FACTORS
        }
        if any(value is None for value in normalized_coverage.values()):
            factor_coverage = None
        else:
            factor_coverage = {
                factor: int(normalized_coverage[factor]) for factor in _FACTORS
            }
    self_consistency, self_consistency_valid = _finite_float(
        getattr(judge_result, "self_consistency", 0.0) or 0.0
    )

    hard_failures: list[str] = []
    if judge_error:
        hard_failures.append("judge_error")
    elif judge_result is None:
        hard_failures.append("judge_missing")
    if judge_grade != intended:
        hard_failures.append("judge_label_mismatch")
    if raw_votes is None or strict_sample_count is None or vote_count != sample_count:
        hard_failures.append("invalid_primary_vote_audit")
    if valid_vote_count < 1 or parse_fail_count or valid_vote_count != sample_count:
        hard_failures.append("incomplete_primary_votes")
    if (
        not self_consistency_valid
        or not 0.0 <= self_consistency <= 1.0
        or self_consistency < min_self_consistency
    ):
        hard_failures.append("low_self_consistency")
    if expected_scores is None or expected_grade != intended:
        hard_failures.append("invalid_expected_factors")
    if primary_scores is None:
        hard_failures.append("missing_primary_factor_scores")
    elif primary_scores != expected_scores:
        hard_failures.append("primary_factors_mismatch")
    if primary_derived_grade != intended:
        hard_failures.append("primary_derived_grade_mismatch")

    factor_vote_complete: dict[str, bool] = {}
    factor_vote_expected_match: dict[str, bool] = {}
    for factor in _FACTORS:
        distribution = factor_votes.get(factor, {}) if factor_votes else {}
        coverage = factor_coverage.get(factor, -1) if factor_coverage else -1
        complete = (
            sample_count > 0
            and coverage == sample_count
            and sum(distribution.values()) == sample_count
        )
        matches = bool(
            complete
            and expected_scores is not None
            and distribution == {expected_scores[factor]: sample_count}
        )
        factor_vote_complete[factor] = complete
        factor_vote_expected_match[factor] = matches
        if not complete:
            hard_failures.append(f"incomplete_factor_votes:{factor}")
        elif not matches:
            hard_failures.append(f"factor_vote_disagreement:{factor}")

    quality_audit, quality_failures = _document_quality_audit(
        judge_result=judge_result,
        text=text,
        sample_count=sample_count,
    )
    hard_failures.extend(quality_failures)

    rule_judge_agreement = (
        rule_grade == judge_grade
        if rule_grade is not None and judge_grade is not None
        else None
    )
    advisory_warnings: list[str] = []
    if rule_error:
        advisory_warnings.append("advisory_rule_error")
    elif rule_grade is None:
        advisory_warnings.append("advisory_rule_unavailable")
    elif rule_judge_agreement is False:
        advisory_warnings.append("advisory_rule_disagreement")
    if not rule_evidence_present:
        advisory_warnings.append("advisory_rule_no_evidence")
    shadow_grade = getattr(judge_result, "shadow_grade", None)
    if shadow_grade in _GRADES and shadow_grade != judge_grade:
        advisory_warnings.append("advisory_shadow_disagreement")

    # De-duplicate without erasing the original deterministic order.
    hard_failures = list(dict.fromkeys(hard_failures))
    advisory_warnings = list(dict.fromkeys(advisory_warnings))
    semantic_gate_passed = not hard_failures
    if judge_error:
        vote_state = "judge_error"
    elif judge_result is None:
        vote_state = "missing"
    elif valid_vote_count == 0:
        vote_state = "parse_failed"
    else:
        vote_state = "recorded"
    audit = {
        "schema": "proxy-semantic-quality-adjudication-v2",
        "intended_label": intended,
        "primary_grade": judge_grade,
        "intended_primary_agreement": judge_grade == intended,
        "semantic_agreement": judge_grade == intended,
        "semantic_gate_passed": semantic_gate_passed,
        "semantic_gate_failures": hard_failures,
        "advisory_warnings": advisory_warnings,
        "rule_advisory_only": True,
        "rule_grade": rule_grade,
        "rule_confidence": rule_confidence,
        "rule_judge_agreement": rule_judge_agreement,
        # Keep the legacy-named field truthful: it still means rule/judge
        # agreement and is never rewritten to make a semantic pass look lexical.
        "agreement": rule_judge_agreement is True,
        "rule_evidence_state": "present" if rule_evidence_present else "absent",
        "rule_has_real_evidence": rule_evidence_present,
        "rule_evidence_spans": rule_spans,
        "rule_evidence_count": len(rule_spans),
        "legacy_require_rule_evidence_requested": require_evidence,
        "rule_evidence_required_for_admission": False,
        "primary_provider": getattr(judge_result, "primary_provider", None),
        "primary_votes": votes,
        "primary_vote_count": vote_count,
        "primary_valid_vote_count": valid_vote_count,
        "primary_parse_fail_count": parse_fail_count,
        "primary_sample_count": sample_count,
        "primary_vote_state": vote_state,
        "primary_self_consistency": self_consistency,
        "primary_self_consistency_valid": self_consistency_valid,
        "min_self_consistency": min_self_consistency,
        "primary_rationale": str(getattr(judge_result, "rationale", "") or ""),
        "expected_factor_scores": expected_scores,
        "expected_factor_derived_grade": expected_grade,
        "primary_factor_scores": primary_scores,
        "primary_factor_derived_grade": primary_derived_grade,
        "primary_factor_votes": {
            factor: {str(level): count for level, count in distribution.items()}
            for factor, distribution in (factor_votes or {}).items()
        },
        "primary_factor_coverage": factor_coverage,
        "factor_vote_complete": factor_vote_complete,
        "factor_vote_expected_match": factor_vote_expected_match,
        **quality_audit,
        "shadow_grade": shadow_grade,
        "airgap": bool(getattr(judge_result, "airgap", False)),
        "gate_version": PROXY_GATE_VERSION,
        "judge_error": judge_error,
        "rule_error": rule_error,
        "text_sha256": text_hash(text),
    }
    return audit, hard_failures, advisory_warnings


def _semantic_decision(
    candidate: Mapping[str, object],
    *,
    rule_result: object | None,
    judge_result: object | None,
    evidence: Mapping[str, object],
    hard_failures: Sequence[str],
    advisory_warnings: Sequence[str],
) -> dict:
    intended = str(candidate.get("label") or "")
    judge_grade = evidence.get("primary_grade")
    rule_grade = evidence.get("rule_grade")
    passed = not hard_failures
    status = "gold_candidate" if passed else f"needs_review_{hard_failures[0]}"
    flags = list(advisory_warnings) + [f"semantic:{item}" for item in hard_failures]
    upper_priority = 10.0 if intended in {"TS", "S1"} else 0.0
    review_priority = (
        upper_priority
        + 5.0 * float(bool(hard_failures))
        + 2.0 * (1.0 - float(evidence["primary_self_consistency"]))
    )
    llm_confidence, _ = _finite_float(getattr(judge_result, "mean_conf", 0.0) or 0.0)
    return {
        "doc_id": candidate.get("doc_id")
        or text_hash(str(candidate.get("text") or ""))[:16],
        "text": str(candidate.get("text") or "").strip(),
        "label": intended if passed else None,
        "label_source": "proxy_semantic_judge" if passed else "needs_review",
        "source": candidate.get("source", ""),
        "domain": candidate.get("domain", ""),
        "review_status": "gold_candidate" if passed else "needs_review",
        "status": status,
        "rule_grade": rule_grade,
        "rule_confidence": round(float(evidence["rule_confidence"]), 4),
        "llm_grade": judge_grade,
        "llm_confidence": round(llm_confidence, 4),
        "agreement": evidence.get("rule_judge_agreement") is True,
        "flags": flags,
        "review_priority": round(review_priority, 4),
        "self_consistency": round(float(evidence["primary_self_consistency"]), 4),
        "shadow_grade": evidence.get("shadow_grade"),
        "gate_version": PROXY_GATE_VERSION,
    }


def _merge_decision(
    candidate: Mapping[str, object],
    decision: Mapping[str, object],
    *,
    evidence: Mapping[str, object],
    judge_model: str,
    judge_model_revision: str | None,
    shadow_model: str | None,
    shadow_model_revision: str | None,
    bucket: str,
) -> dict:
    row: dict[str, object] = {}
    for key, value in candidate.items():
        if key == "label":
            row["intended_label"] = value
        elif key in _DECISION_COLLISIONS:
            row[f"input_{key}"] = value
        else:
            row[key] = value
    row.update(decision)
    row["source_record_sha256"] = _record_digest(candidate)
    row["decision_bucket"] = bucket
    row["consensus_evidence"] = dict(evidence)
    row["primary_judge_model"] = judge_model
    row["primary_judge_model_revision"] = judge_model_revision or "unavailable"
    row["judging_lineage"] = [
        f"consensus_gate:{decision.get('gate_version') or 'unknown'}",
        f"primary_judge:local_openai:{judge_model}",
    ]
    if shadow_model:
        row["judging_lineage"].append(f"shadow_judge:local_openai:{shadow_model}")
        row["shadow_judge_model_revision"] = shadow_model_revision or "unavailable"
    return row


def _model_dict(identity: ModelIdentity | None) -> dict | None:
    return asdict(identity) if identity is not None else None


def judge_proxy_candidates(
    records: Sequence[Mapping[str, object]],
    *,
    run_dir: Path,
    judge: object,
    judge_model: str,
    shadow_model: str | None,
    intended_use: str = "evaluation",
    rule_pipeline: object | None = None,
    require_evidence: bool = False,
    min_self_consistency: float = 0.67,
    input_reference: str | None = None,
    input_path: Path | None = None,
    allow_unattested_legacy_input: bool = False,
    judge_model_revision: str | None = None,
    shadow_model_revision: str | None = None,
    primary_judge_runtime_attestation: Mapping[str, object] | None = None,
    shadow_judge_runtime_attestation: Mapping[str, object] | None = None,
) -> dict:
    """Judge records and commit an auditable, non-overwriting run."""
    validate_candidates(records, intended_use=intended_use)
    effective_input_reference = (
        str(input_path) if input_path is not None else input_reference
    )
    if allow_unattested_legacy_input:
        upstream_attestation = _legacy_unattested_input(
            records,
            input_reference=effective_input_reference,
            intended_use=intended_use,
        )
    else:
        if input_path is None:
            raise ProxyJudgeContractError(
                "attested generation input_path is required; the legacy override is "
                "for migration/testing only"
            )
        upstream_attestation = attest_generation_input(
            input_path, records=records, intended_use=intended_use
        )
    if (
        not math.isfinite(min_self_consistency)
        or not 0.0 <= min_self_consistency <= 1.0
    ):
        raise ProxyJudgeContractError(
            "min_self_consistency must be finite and between 0 and 1"
        )
    if judge_model_revision and not _SHA256_REVISION_RE.fullmatch(judge_model_revision):
        raise ProxyJudgeContractError("invalid primary judge model revision")
    if shadow_model_revision and not _SHA256_REVISION_RE.fullmatch(
        shadow_model_revision
    ):
        raise ProxyJudgeContractError("invalid shadow judge model revision")
    if shadow_model_revision and not shadow_model:
        raise ProxyJudgeContractError(
            "shadow model revision provided without shadow model"
        )
    runtime_attestation_pairs = (
        (
            "primary judge",
            primary_judge_runtime_attestation,
            judge_model,
            judge_model_revision,
        ),
        (
            "shadow judge",
            shadow_judge_runtime_attestation,
            shadow_model,
            shadow_model_revision,
        ),
    )
    for context, attestation, model_name, expected_digest in runtime_attestation_pairs:
        if model_name is None:
            if attestation is not None:
                raise ProxyJudgeContractError(
                    f"{context} runtime attestation provided without a model"
                )
            continue
        if expected_digest is not None and attestation is None:
            raise ProxyJudgeContractError(
                f"{context} live Ollama model attestation is required"
            )
        if attestation is not None:
            try:
                validated_attestation = validate_ollama_attestation(attestation)
            except OllamaAttestationError as exc:
                raise ProxyJudgeContractError(
                    f"{context} live Ollama model attestation is invalid: {exc}"
                ) from exc
            if (
                validated_attestation.get("requested_model") != model_name
                or validated_attestation.get("expected_model_digest")
                != expected_digest
                or validated_attestation.get("live_model_digest")
                != expected_digest
            ):
                raise ProxyJudgeContractError(
                    f"{context} live Ollama model attestation does not match the run"
                )
    primary_identity, generator_identities, shadow_identity = validate_model_contract(
        records, judge_model=judge_model, shadow_model=shadow_model
    )
    validate_runtime_judge(
        judge,
        expected_primary=primary_identity,
        expected_shadow=shadow_identity,
    )
    if not run_dir.is_dir() or any(run_dir.iterdir()):
        raise ProxyJudgeContractError(
            "run_dir must be an existing empty unique directory"
        )

    if rule_pipeline is None:
        rule_pipeline, rule_seed_sha256 = _static_rule_pipeline()
        rule_source = "static_keyword_seeds"
    else:
        rule_seed_sha256 = "unavailable_injected_rule_pipeline"
        rule_source = "injected"
    recording_rule = _RecordingRulePipeline(rule_pipeline)
    recording_judge = _RecordingJudge(judge)
    judge_errors: dict[str, str] = {}
    rule_errors: dict[str, str] = {}

    run_id = run_dir.name
    manifest_path = run_dir / "run_manifest.json"
    progress_path = run_dir / "progress.json"
    journal_path = run_dir / "decisions.journal.jsonl"
    input_digest = _records_digest(records)
    manifest = {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "status": "running",
        "started_at": _utc_now(),
        "input_reference": effective_input_reference,
        "input_sha256": input_digest,
        "input_count": len(records),
        "upstream_generation": upstream_attestation,
        "intended_use": intended_use,
        "catalog_split_role": upstream_attestation["usage_contract"][
            "catalog_split_role"
        ],
        "claim_scope": "synthetic_proxy_candidate_only",
        "human_reviewed": False,
        "gate_version": PROXY_GATE_VERSION,
        "gate_contract": (
            "intended_label=primary_grade; complete unanimous S/V/M votes; "
            "derived_grade; complete unanimous document-quality checks with "
            "exact issue spans; exact evidence card; eligible proxy contract"
        ),
        "rule_advisory_only": True,
        "legacy_require_rule_evidence_requested": require_evidence,
        "min_self_consistency": min_self_consistency,
        "rule_source": rule_source,
        "rule_seed_sha256": rule_seed_sha256,
        "generator_models": [_model_dict(item) for item in generator_identities],
        "primary_judge": _model_dict(primary_identity),
        "primary_judge_model_revision": judge_model_revision or "unavailable",
        "primary_judge_runtime_attestation": (
            dict(primary_judge_runtime_attestation)
            if primary_judge_runtime_attestation is not None
            else None
        ),
        "shadow_judge": _model_dict(shadow_identity),
        "shadow_judge_model_revision": (
            shadow_model_revision or "unavailable" if shadow_model else None
        ),
        "shadow_judge_runtime_attestation": (
            dict(shadow_judge_runtime_attestation)
            if shadow_judge_runtime_attestation is not None
            else None
        ),
    }
    _atomic_write_json(manifest_path, manifest)

    gold_rows: list[dict] = []
    uncertain_rows: list[dict] = []
    completed_ids: list[str] = []
    journal = journal_path.open("x", encoding="utf-8", newline="")
    os.chmod(journal_path, ARTIFACT_FILE_MODE)
    try:
        for candidate in records:
            text = str(candidate["text"]).strip()
            digest = text_hash(text)
            try:
                recording_rule.label(text)
            except Exception as exc:  # advisory channel must not block the judge
                rule_errors[digest] = type(exc).__name__
            try:
                recording_judge.judge(text)
            except Exception as exc:  # one failed judge call must never become gold
                judge_errors[digest] = type(exc).__name__

            evidence, hard_failures, advisory_warnings = _consensus_evidence(
                candidate=candidate,
                text=text,
                rule_result=recording_rule.results.get(digest),
                rule_error=rule_errors.get(digest),
                judge_result=recording_judge.results.get(digest),
                judge_error=judge_errors.get(digest),
                require_evidence=require_evidence,
                min_self_consistency=min_self_consistency,
            )
            decision = _semantic_decision(
                candidate,
                rule_result=recording_rule.results.get(digest),
                judge_result=recording_judge.results.get(digest),
                evidence=evidence,
                hard_failures=hard_failures,
                advisory_warnings=advisory_warnings,
            )
            evidence["gate_status"] = decision["status"]
            evidence["gate_flags"] = decision["flags"]
            bucket = "gold_candidate" if not hard_failures else "uncertain"
            merged = _merge_decision(
                candidate,
                decision,
                evidence=evidence,
                judge_model=judge_model,
                judge_model_revision=judge_model_revision,
                shadow_model=shadow_model,
                shadow_model_revision=shadow_model_revision,
                bucket=bucket,
            )
            if bucket == "gold_candidate":
                proxy_errors: list[str] = []
                try:
                    merged["evidence_card"] = build_evidence_card(merged)
                except ProxyEvidenceError as exc:
                    proxy_errors.append(f"evidence:{exc}")
                if not proxy_errors:
                    proxy_errors.extend(
                        validate_proxy_record(
                            merged,
                            stage="eligible",
                            intended_use=intended_use,
                        ).errors
                    )
                if proxy_errors:
                    bucket = "uncertain"
                    merged["decision_bucket"] = bucket
                    merged["proxy_contract_original_label"] = merged.get("label")
                    merged["label"] = None
                    merged["label_source"] = "needs_review"
                    merged["review_status"] = "needs_review"
                    merged["status"] = "needs_review_proxy_contract"
                    merged["proxy_eligibility_errors"] = proxy_errors
                    merged["consensus_evidence"]["proxy_contract_passed"] = False
                    merged["consensus_evidence"]["gate_status"] = merged["status"]
                else:
                    merged["consensus_evidence"]["proxy_contract_passed"] = True
            (gold_rows if bucket == "gold_candidate" else uncertain_rows).append(merged)
            journal.write(json.dumps(merged, ensure_ascii=False, sort_keys=True) + "\n")
            journal.flush()
            os.fsync(journal.fileno())
            completed_ids.append(str(candidate["doc_id"]))
            _atomic_write_json(
                progress_path,
                {
                    "run_id": run_id,
                    "status": "running",
                    "completed": len(completed_ids),
                    "input_count": len(records),
                    "gold_candidate": len(gold_rows),
                    "uncertain": len(uncertain_rows),
                    "last_doc_id": completed_ids[-1],
                    "updated_at": _utc_now(),
                },
                replace=True,
            )
    except KeyboardInterrupt:
        manifest.update({"status": "interrupted", "updated_at": _utc_now()})
        _atomic_write_json(manifest_path, manifest, replace=True)
        raise
    except Exception as exc:
        manifest.update(
            {
                "status": "failed",
                "updated_at": _utc_now(),
                "error_type": type(exc).__name__,
            }
        )
        _atomic_write_json(manifest_path, manifest, replace=True)
        raise
    finally:
        journal.close()

    gold_path = run_dir / "gold_candidate.jsonl"
    uncertain_path = run_dir / "uncertain.jsonl"
    stats_path = run_dir / "stats.json"
    _atomic_write_jsonl(gold_path, gold_rows)
    _atomic_write_jsonl(uncertain_path, uncertain_rows)
    gold_by_scenario = Counter(
        str(row.get("scenario_id") or "") for row in gold_rows
    )
    gold_by_factor_profile = Counter(
        str(row.get("factor_profile_id") or "") for row in gold_rows
    )
    uncertain_by_factor_profile = Counter(
        str(row.get("factor_profile_id") or "") for row in uncertain_rows
    )
    base_target_by_scenario = upstream_attestation.get(
        "base_final_target_by_scenario", {}
    )
    base_target_by_factor_profile = upstream_attestation.get(
        "base_final_target_by_factor_profile", {}
    )
    if not isinstance(base_target_by_scenario, Mapping) or not isinstance(
        base_target_by_factor_profile, Mapping
    ):
        raise ProxyJudgeContractError("upstream base profile targets are invalid")
    gold_shortfall_by_scenario = {
        str(scenario_id): int(target) - gold_by_scenario[str(scenario_id)]
        for scenario_id, target in sorted(base_target_by_scenario.items())
        if gold_by_scenario[str(scenario_id)] < int(target)
    }
    gold_shortfall_by_factor_profile = {
        str(profile): int(target) - gold_by_factor_profile[str(profile)]
        for profile, target in sorted(base_target_by_factor_profile.items())
        if gold_by_factor_profile[str(profile)] < int(target)
    }
    stats = {
        "run_id": run_id,
        "input": len(records),
        "completed": len(completed_ids),
        "gold_candidate": len(gold_rows),
        "uncertain": len(uncertain_rows),
        "gold_by_grade": dict(Counter(row.get("label") for row in gold_rows)),
        "gold_by_scenario": dict(sorted(gold_by_scenario.items())),
        "gold_by_factor_profile": dict(sorted(gold_by_factor_profile.items())),
        "uncertain_by_factor_profile": dict(
            sorted(uncertain_by_factor_profile.items())
        ),
        "base_target_by_scenario": dict(sorted(base_target_by_scenario.items())),
        "base_target_by_factor_profile": dict(
            sorted(base_target_by_factor_profile.items())
        ),
        "gold_shortfall_by_scenario": gold_shortfall_by_scenario,
        "gold_shortfall_by_factor_profile": gold_shortfall_by_factor_profile,
        "ready_for_exact_assembly": not (
            gold_shortfall_by_scenario or gold_shortfall_by_factor_profile
        ),
        "uncertain_by_status": dict(
            Counter(row.get("status") for row in uncertain_rows)
        ),
        "judge_errors": len(judge_errors),
        "rule_errors_advisory": len(rule_errors),
        "advisory_rule_disagreements": sum(
            "advisory_rule_disagreement" in row.get("flags", [])
            for row in gold_rows + uncertain_rows
        ),
        "judge_parse_failures": sum(
            row["consensus_evidence"]["primary_vote_state"] == "parse_failed"
            for row in gold_rows + uncertain_rows
        ),
        "input_sha256": input_digest,
        "intended_use": intended_use,
        "catalog_split_role": upstream_attestation["usage_contract"][
            "catalog_split_role"
        ],
        "claim_scope": "synthetic_proxy_candidate_only",
        "human_reviewed": False,
    }
    _atomic_write_json(stats_path, stats)
    _atomic_write_json(
        progress_path,
        {
            **stats,
            "status": "complete",
            "last_doc_id": completed_ids[-1] if completed_ids else None,
            "updated_at": _utc_now(),
        },
        replace=True,
    )
    manifest.update(
        {
            "status": "complete",
            "completed_at": _utc_now(),
            "gold_candidate_path": gold_path.name,
            "uncertain_path": uncertain_path.name,
            "journal_path": journal_path.name,
            "stats_path": stats_path.name,
            "stats": stats,
        }
    )
    _atomic_write_json(manifest_path, manifest, replace=True)
    complete_path = run_dir / "COMPLETE.json"
    _atomic_write_json(
        complete_path,
        {
            "schema_version": RUN_SCHEMA_VERSION,
            "run_id": run_id,
            "committed_at": _utc_now(),
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "gold_candidate_sha256": hashlib.sha256(gold_path.read_bytes()).hexdigest(),
            "uncertain_sha256": hashlib.sha256(uncertain_path.read_bytes()).hexdigest(),
            "stats_sha256": hashlib.sha256(stats_path.read_bytes()).hexdigest(),
            "input_sha256": input_digest,
            "intended_use": intended_use,
            "catalog_split_role": upstream_attestation["usage_contract"][
                "catalog_split_role"
            ],
            "upstream_generation": upstream_attestation,
            "primary_judge_runtime_attestation": manifest[
                "primary_judge_runtime_attestation"
            ],
            "shadow_judge_runtime_attestation": manifest[
                "shadow_judge_runtime_attestation"
            ],
        },
    )
    return {"run_dir": str(run_dir), **stats}


def _build_judge(
    *,
    base_url: str,
    judge_model: str,
    shadow_model: str | None,
    k_min: int,
    k_max: int,
    temperature: float,
) -> object:
    # Keep this production runner self-contained. Importing the helper from
    # ``build_synthetic_golden.py`` made the judge depend on an unrelated CLI
    # that is intentionally absent from the minimal proxy runtime release.
    from koipa.adapters.llm.local_openai_provider import (  # noqa: PLC0415
        LocalOpenAIProvider,
    )
    from koipa.modules.m3_labeling.judge import ConsensusJudge  # noqa: PLC0415
    from koipa.modules.m3_labeling.llm_labeler import LLMLabeler  # noqa: PLC0415

    def _provider(model: str, label: str) -> LocalOpenAIProvider:
        return LocalOpenAIProvider(
            base_url=base_url,
            api_key="ollama",
            model=model,
            enable_thinking=False,
            provider_label=label,
        )

    primary = LLMLabeler(provider=_provider(judge_model, "proxy-primary-judge"))
    shadow = None
    if shadow_model:
        shadow = LLMLabeler(provider=_provider(shadow_model, "proxy-shadow-judge"))
    return ConsensusJudge(
        primary=primary,
        shadow=shadow,
        airgap=False,
        k_min=k_min,
        k_max=k_max,
        temperature=temperature,
        require_document_quality=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Judge proxy candidates into gold_candidate/uncertain run artifacts"
    )
    parser.add_argument(
        "--input",
        required=True,
        help=(
            "committed generation-run candidates.jsonl; sibling COMPLETE/manifest/"
            "rejected/stats artifacts are verified by default"
        ),
    )
    parser.add_argument(
        "--out-root", default="datasets/proxy_gold/judged_runs", help="unique runs root"
    )
    parser.add_argument(
        "--run-id", help="optional unique run id; existing path is refused"
    )
    parser.add_argument(
        "--intended-use",
        choices=tuple(sorted(_INTENDED_USE_CONTRACTS)),
        default="evaluation",
        help=(
            "bind candidates to the matching catalog split and permission contract "
            "(default: evaluation)"
        ),
    )
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--judge-model", default="gemma3:12b")
    parser.add_argument(
        "--judge-model-manifest-sha256",
        required=True,
        help="pinned primary judge model revision as sha256:<64 hex>",
    )
    parser.add_argument("--shadow-model", default="qwen3:14b")
    parser.add_argument(
        "--shadow-model-manifest-sha256",
        help="pinned shadow model revision; required unless --no-shadow",
    )
    parser.add_argument("--no-shadow", action="store_true")
    parser.add_argument("--k-min", type=int, default=2)
    parser.add_argument("--k-max", type=int, default=3)
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--min-self-consistency", type=float, default=0.67)
    parser.add_argument(
        "--require-evidence",
        action="store_true",
        help=(
            "legacy audit flag only; keyword-rule evidence is advisory and exact "
            "semantic evidence cards are always required"
        ),
    )
    parser.add_argument(
        "--allow-unattested-legacy-input",
        action="store_true",
        help=(
            "migration/testing-only bypass for historical candidate JSONL that lacks "
            "a committed generation-run envelope; never use for new proxy runs"
        ),
    )
    args = parser.parse_args(argv)

    if args.k_min < 1 or args.k_max < args.k_min:
        parser.error("require 1 <= k-min <= k-max")
    if not 0.0 <= args.min_self_consistency <= 1.0:
        parser.error("min-self-consistency must be between 0 and 1")
    if not _SHA256_REVISION_RE.fullmatch(args.judge_model_manifest_sha256):
        parser.error("--judge-model-manifest-sha256 must be sha256:<64 hex>")
    if not args.no_shadow and not args.shadow_model_manifest_sha256:
        parser.error("--shadow-model-manifest-sha256 is required with a shadow model")
    if args.shadow_model_manifest_sha256 and not _SHA256_REVISION_RE.fullmatch(
        args.shadow_model_manifest_sha256
    ):
        parser.error("--shadow-model-manifest-sha256 must be sha256:<64 hex>")

    input_path = Path(args.input)
    shadow_model = None if args.no_shadow else args.shadow_model
    try:
        records = load_candidates(input_path)
        validate_candidates(records, intended_use=args.intended_use)
        if not args.allow_unattested_legacy_input:
            # Fail before allocating a judge run directory or loading a model.
            attest_generation_input(
                input_path,
                records=records,
                intended_use=args.intended_use,
            )
        validate_model_contract(
            records,
            judge_model=args.judge_model,
            shadow_model=shadow_model,
        )
        primary_runtime_attestation = verify_ollama_model(
            base_url=args.base_url,
            requested_model=args.judge_model,
            expected_manifest_sha256=args.judge_model_manifest_sha256,
        )
        shadow_runtime_attestation = (
            verify_ollama_model(
                base_url=args.base_url,
                requested_model=str(shadow_model),
                expected_manifest_sha256=str(
                    args.shadow_model_manifest_sha256
                ),
            )
            if shadow_model is not None
            else None
        )
        judge = _build_judge(
            base_url=args.base_url,
            judge_model=args.judge_model,
            shadow_model=shadow_model,
            k_min=args.k_min,
            k_max=args.k_max,
            temperature=args.temperature,
        )
        run_dir = create_unique_run_dir(Path(args.out_root), args.run_id)
        stats = judge_proxy_candidates(
            records,
            run_dir=run_dir,
            judge=judge,
            judge_model=args.judge_model,
            shadow_model=shadow_model,
            intended_use=args.intended_use,
            require_evidence=args.require_evidence,
            min_self_consistency=args.min_self_consistency,
            input_reference=str(input_path),
            input_path=input_path,
            allow_unattested_legacy_input=args.allow_unattested_legacy_input,
            judge_model_revision=args.judge_model_manifest_sha256,
            shadow_model_revision=(
                None if args.no_shadow else args.shadow_model_manifest_sha256
            ),
            primary_judge_runtime_attestation=primary_runtime_attestation,
            shadow_judge_runtime_attestation=shadow_runtime_attestation,
        )
    except (OSError, OllamaAttestationError, ProxyJudgeContractError) as exc:
        print(f"[BLOCK] {exc}", file=sys.stderr)
        return 2

    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
