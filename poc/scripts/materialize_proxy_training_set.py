"""Materialize an immutable, leakage-safe proxy training run.

The frozen proxy-gold corpus is an exclusion boundary, never an input source.
Only the family-separated training split is expanded into model chunks;
validation and calibration stay at document level.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
import uuid

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
sys.path.insert(0, str(_POC))
sys.path.insert(0, str(_POC / "src"))

from scripts.assemble_proxy_gold import CorpusLoadError, _load_corpus  # noqa: E402
from scripts.assemble_public_s3_challenge import (  # noqa: E402
    ChallengeAssemblyError,
    SCHEMA as PUBLIC_HOLDOUT_MANIFEST_SCHEMA,
    load_blocked_corpora,
)
from lloydk.hygiene import text_hash  # noqa: E402
from lloydk.modules.m4_training.chunk_expand import (  # noqa: E402
    expand_records_evidence_aware,
)
from lloydk.proxy_corpus import (  # noqa: E402
    GRADE_CODES,
    IntendedUse,
    validate_proxy_record,
)


SCHEMA_VERSION = "proxy-training-run-v1"
TRAINING_POOL_SCHEMA_VERSION = "proxy-training-pool-run-v1"
EXPECTED_TRAINING_DOCUMENTS = 3_000
EXPECTED_FROZEN_DOCUMENTS = 1_000
EXPECTED_PUBLIC_TRAINING_DOCUMENTS = 300
MINIMUM_PUBLIC_HOLDOUT_ARTIFACTS = 2
MINIMUM_PUBLIC_HOLDOUT_DOCUMENTS = 600
EXPECTED_ORIGIN_LABEL_COUNTS = {
    ("synthetic", "TS"): 750,
    ("synthetic", "S1"): 750,
    ("synthetic", "S2"): 750,
    ("synthetic", "S3"): 450,
    ("public_real", "S3"): 300,
}
SPLIT_RATIOS = {"train": 0.80, "validation": 0.10, "calibration": 0.10}
SPLIT_SEED = "proxy-training-family-split-v1"
_GRADE_ORDER = ("TS", "S1", "S2", "S3")
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class TrainingMaterializationError(ValueError):
    """The requested training run violates an immutable data contract."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_record_bytes(records: Sequence[Mapping[str, object]]) -> bytes:
    ordered = sorted(
        records,
        key=lambda row: (
            str(row.get("document_family_id") or ""),
            str(row.get("doc_id") or ""),
            text_hash(str(row.get("text") or "")),
        ),
    )
    return b"".join(
        (
            json.dumps(
                dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        ).encode("utf-8")
        for row in ordered
    )


def _jsonl_bytes(records: Sequence[Mapping[str, object]]) -> bytes:
    return b"".join(
        (
            json.dumps(
                dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        ).encode("utf-8")
        for row in records
    )


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> bytes:
    encoded = (
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _atomic_write(path, encoded)
    return encoded


def _source_stats_with_hashes(stats: Mapping[str, object]) -> dict[str, object]:
    enriched = dict(stats)
    files: list[dict[str, object]] = []
    for value in stats.get("loaded_files", []):
        if not isinstance(value, Mapping):
            raise TrainingMaterializationError("invalid loader file statistics")
        path = Path(str(value.get("path") or ""))
        if not path.is_file():
            raise TrainingMaterializationError(f"loaded source disappeared: {path}")
        files.append({**dict(value), "sha256": _sha256_bytes(path.read_bytes())})
    enriched["loaded_files"] = files
    return enriched


def _load_strict_json_object(path: Path) -> tuple[dict[str, object], bytes]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant {value}")

    try:
        payload = path.read_bytes()
        parsed = json.loads(payload.decode("utf-8"), parse_constant=reject_constant)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise TrainingMaterializationError(
            f"cannot read training-pool envelope {path}: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise TrainingMaterializationError(
            f"training-pool envelope must be an object: {path}"
        )
    return parsed, payload


def _require_manifest_sha256(value: object, *, field: str) -> str:
    digest = str(value or "").strip()
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise TrainingMaterializationError(
            f"training-pool manifest has invalid {field}"
        )
    return digest


def _public_holdout_fingerprints(value: object) -> list[dict[str, object]]:
    if not isinstance(value, (list, tuple)) or not value:
        raise TrainingMaterializationError(
            "production training-pool manifest lacks public holdout artifacts"
        )
    fingerprints: list[dict[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise TrainingMaterializationError(
                f"invalid public holdout artifact attestation at index {index}"
            )
        records = item.get("records")
        if isinstance(records, bool) or not isinstance(records, int) or records < 1:
            raise TrainingMaterializationError(
                f"invalid public holdout artifact record count at index {index}"
            )
        fingerprint = {
            "manifest_schema": str(item.get("manifest_schema") or ""),
            "manifest_sha256": _require_manifest_sha256(
                item.get("manifest_sha256"),
                field=f"public holdout[{index}] manifest_sha256",
            ),
            "records_sha256": _require_manifest_sha256(
                item.get("records_sha256"),
                field=f"public holdout[{index}] records_sha256",
            ),
            "records": records,
        }
        if fingerprint["manifest_schema"] != PUBLIC_HOLDOUT_MANIFEST_SCHEMA:
            raise TrainingMaterializationError(
                f"invalid public holdout manifest schema at index {index}"
            )
        fingerprints.append(fingerprint)
    return sorted(
        fingerprints,
        key=lambda item: (
            str(item["records_sha256"]),
            str(item["manifest_sha256"]),
        ),
    )


def _production_exclusion_contract(
    manifest: Mapping[str, object],
) -> dict[str, object]:
    """Extract the exact four-way exclusion boundary from an assembler manifest."""
    inputs = manifest.get("inputs")
    leakage = manifest.get("leakage_checks")
    contract = manifest.get("contract")
    if not all(isinstance(value, Mapping) for value in (inputs, leakage, contract)):
        raise TrainingMaterializationError(
            "production training-pool manifest lacks exclusion-boundary metadata"
        )
    frozen = inputs.get("frozen_primary")
    public_holdouts = inputs.get("blocked_public_holdouts")
    public_training = inputs.get("public_s3_training")
    if not all(
        isinstance(value, Mapping)
        for value in (frozen, public_holdouts, public_training)
    ):
        raise TrainingMaterializationError(
            "production training-pool manifest lacks attested corpus inputs"
        )
    union = public_holdouts.get("union_uniqueness")
    if not isinstance(union, Mapping):
        raise TrainingMaterializationError(
            "production training-pool manifest lacks public holdout uniqueness"
        )
    holdout_fingerprints = _public_holdout_fingerprints(
        public_holdouts.get("artifacts")
    )
    expected_zero_overlap = {
        "doc_id_overlap": 0,
        "document_family_id_overlap": 0,
        "normalized_text_hash_overlap": 0,
    }
    required_checks = (
        "frozen_primary_vs_attested_public_holdouts",
        "selected_vs_frozen_primary",
        "selected_vs_attested_public_holdouts",
    )
    if any(leakage.get(name) != expected_zero_overlap for name in required_checks):
        raise TrainingMaterializationError(
            "production training-pool manifest has missing or non-zero leakage checks"
        )
    if (
        frozen.get("row_count") != EXPECTED_FROZEN_DOCUMENTS
        or public_training.get("row_count") != EXPECTED_PUBLIC_TRAINING_DOCUMENTS
        or not isinstance(public_holdouts.get("artifact_count"), int)
        or isinstance(public_holdouts.get("artifact_count"), bool)
        or int(public_holdouts["artifact_count"]) < MINIMUM_PUBLIC_HOLDOUT_ARTIFACTS
        or int(public_holdouts["artifact_count"]) != len(holdout_fingerprints)
        or not isinstance(public_holdouts.get("row_count"), int)
        or isinstance(public_holdouts.get("row_count"), bool)
        or int(public_holdouts["row_count"]) < MINIMUM_PUBLIC_HOLDOUT_DOCUMENTS
        or any(
            union.get(field) != public_holdouts.get("row_count")
            for field in (
                "unique_doc_ids",
                "unique_document_family_ids",
                "unique_normalized_text_hashes",
            )
        )
        or contract.get("public_real_s3_target") != EXPECTED_PUBLIC_TRAINING_DOCUMENTS
        or contract.get("frozen_primary_role") != "evaluation_exclusion_only"
    ):
        raise TrainingMaterializationError(
            "production training-pool exclusion-boundary counts are invalid"
        )
    return {
        "expected_frozen_records": EXPECTED_FROZEN_DOCUMENTS,
        "expected_frozen_records_sha256": _require_manifest_sha256(
            frozen.get("records_sha256"), field="frozen primary records_sha256"
        ),
        "expected_blocked_public_records": int(public_holdouts["row_count"]),
        "expected_blocked_public_records_sha256": _require_manifest_sha256(
            public_holdouts.get("records_sha256"),
            field="blocked public holdout records_sha256",
        ),
        "expected_blocked_public_artifacts": int(public_holdouts["artifact_count"]),
        "expected_blocked_public_artifact_fingerprints": holdout_fingerprints,
    }


def _production_factor_profile_contract(
    manifest: Mapping[str, object],
) -> dict[str, object]:
    """Verify exact catalog-bound synthetic scenario/profile selection."""
    contract = manifest.get("contract")
    audit = manifest.get("selection_audit")
    if not isinstance(contract, Mapping) or not isinstance(audit, Mapping):
        raise TrainingMaterializationError(
            "production training-pool manifest lacks factor-profile metadata"
        )
    catalog = contract.get("factor_profile_catalog")
    if (
        contract.get("scenario_quota_contract") is not True
        or not isinstance(catalog, Mapping)
        or catalog.get("factor_profile_schema_id") != "svm-boundary-profile-v1"
        or audit.get("scenario_quota_contract") is not True
    ):
        raise TrainingMaterializationError(
            "production training-pool factor-profile contract is not active"
        )
    catalog_sha256 = _require_manifest_sha256(
        catalog.get("sha256"), field="factor-profile catalog sha256"
    )
    target_by_scenario = audit.get("synthetic_target_by_scenario")
    selected_by_scenario = audit.get("synthetic_selected_by_scenario")
    target_by_profile = audit.get("synthetic_target_by_factor_profile")
    selected_by_profile = audit.get("synthetic_selected_by_factor_profile")
    if not all(
        isinstance(value, Mapping)
        for value in (
            target_by_scenario,
            selected_by_scenario,
            target_by_profile,
            selected_by_profile,
        )
    ):
        raise TrainingMaterializationError(
            "production training-pool factor-profile quota maps are missing"
        )
    normalized_scenarios = {str(key): int(value) for key, value in target_by_scenario.items()}
    normalized_selected_scenarios = {
        str(key): int(value) for key, value in selected_by_scenario.items()
    }
    normalized_profiles = {str(key): int(value) for key, value in target_by_profile.items()}
    normalized_selected_profiles = {
        str(key): int(value) for key, value in selected_by_profile.items()
    }
    if (
        normalized_scenarios != normalized_selected_scenarios
        or normalized_profiles != normalized_selected_profiles
        or len(normalized_profiles) != 21
        or sum(normalized_scenarios.values()) != 2_700
        or sum(normalized_profiles.values()) != 2_700
        or len(normalized_scenarios) != 315
    ):
        raise TrainingMaterializationError(
            "production training-pool did not preserve exact scenario/profile quotas"
        )
    return {
        "factor_profile_catalog_sha256": catalog_sha256,
        "factor_profile_schema_id": "svm-boundary-profile-v1",
        "expected_synthetic_by_scenario": normalized_scenarios,
        "expected_synthetic_by_factor_profile": normalized_profiles,
    }


def _verify_training_pool_envelope(
    input_paths: Sequence[Path], *, expected_count: int
) -> dict[str, object]:
    """Verify the immutable assembler handoff before production materialization."""
    if len(input_paths) != 1:
        raise TrainingMaterializationError(
            "production materialization requires exactly one assembled training_pool.jsonl"
        )
    requested_path = input_paths[0]
    if requested_path.is_symlink():
        raise TrainingMaterializationError(
            f"training-pool input must not be a symlink: {requested_path}"
        )
    records_path = requested_path.resolve()
    manifest_path = records_path.with_name("manifest.json")
    complete_path = records_path.with_name("COMPLETE")
    if records_path.name != "training_pool.jsonl":
        raise TrainingMaterializationError(
            "production input must be the canonical training_pool.jsonl artifact"
        )
    for path in (records_path, manifest_path, complete_path):
        if path.is_symlink() or not path.is_file():
            raise TrainingMaterializationError(
                f"training-pool envelope is incomplete: {path}"
            )

    manifest, manifest_payload = _load_strict_json_object(manifest_path)
    complete, complete_payload = _load_strict_json_object(complete_path)
    try:
        records_payload = records_path.read_bytes()
    except OSError as exc:
        raise TrainingMaterializationError(
            f"cannot read assembled training pool {records_path}: {exc}"
        ) from exc
    records_sha256 = _sha256_bytes(records_payload)
    manifest_sha256 = _sha256_bytes(manifest_payload)
    artifact = manifest.get("artifact")
    code = manifest.get("code")
    if (
        manifest.get("schema_version") != TRAINING_POOL_SCHEMA_VERSION
        or manifest.get("status") != "complete"
        or not isinstance(artifact, Mapping)
        or artifact.get("path") != "training_pool.jsonl"
        or artifact.get("records") != expected_count
        or artifact.get("bytes") != len(records_payload)
        or artifact.get("sha256") != records_sha256
        or not isinstance(code, Mapping)
        or not str(code.get("contract_sha256") or "")
    ):
        raise TrainingMaterializationError(
            "training-pool manifest does not attest the exact input artifact"
        )
    run_id = str(manifest.get("run_id") or "")
    if (
        not run_id
        or complete.get("schema_version") != TRAINING_POOL_SCHEMA_VERSION
        or complete.get("run_id") != run_id
        or complete.get("manifest_sha256") != manifest_sha256
        or complete.get("training_pool_sha256") != records_sha256
        or complete.get("training_pool_records") != expected_count
        or complete.get("code_contract_sha256") != code.get("contract_sha256")
    ):
        raise TrainingMaterializationError(
            "training-pool COMPLETE marker does not attest the exact input artifact"
        )
    production_boundary = {}
    if expected_count == EXPECTED_TRAINING_DOCUMENTS:
        production_boundary = {
            **_production_exclusion_contract(manifest),
            **_production_factor_profile_contract(manifest),
        }
    return {
        "required": True,
        "schema_version": TRAINING_POOL_SCHEMA_VERSION,
        "run_id": run_id,
        "records_path": str(records_path),
        "records": expected_count,
        "records_bytes": len(records_payload),
        "records_sha256": records_sha256,
        "manifest_path": str(manifest_path),
        "manifest_bytes": len(manifest_payload),
        "manifest_sha256": manifest_sha256,
        "complete_path": str(complete_path),
        "complete_bytes": len(complete_payload),
        "complete_sha256": _sha256_bytes(complete_payload),
        "assembler_code_contract_sha256": code["contract_sha256"],
        **production_boundary,
    }


def _verify_envelope_exclusion_inputs(
    envelope_audit: Mapping[str, object],
    *,
    frozen: Sequence[Mapping[str, object]],
    blocked_public: Sequence[Mapping[str, object]],
) -> None:
    """Bind materialization inputs to the exact exclusions used by assembly."""
    actual = {
        "frozen_records": len(frozen),
        "frozen_records_sha256": _sha256_bytes(_canonical_record_bytes(frozen)),
        "blocked_public_records": len(blocked_public),
        "blocked_public_records_sha256": _sha256_bytes(
            _canonical_record_bytes(blocked_public)
        ),
    }
    expected = {
        "frozen_records": envelope_audit.get("expected_frozen_records"),
        "frozen_records_sha256": envelope_audit.get("expected_frozen_records_sha256"),
        "blocked_public_records": envelope_audit.get("expected_blocked_public_records"),
        "blocked_public_records_sha256": envelope_audit.get(
            "expected_blocked_public_records_sha256"
        ),
    }
    mismatches = {
        key: {"expected": expected[key], "actual": actual[key]}
        for key in expected
        if expected[key] != actual[key]
    }
    if mismatches:
        raise TrainingMaterializationError(
            "materialization exclusions differ from the assembled training pool: "
            + json.dumps(mismatches, sort_keys=True)
        )


def _verify_loaded_training_matches_envelope(
    envelope_audit: Mapping[str, object],
    records: Sequence[Mapping[str, object]],
) -> None:
    """Bind parsed in-memory rows to the exact bytes verified in the envelope."""
    expected = str(envelope_audit.get("records_sha256") or "")
    actual = _sha256_bytes(_jsonl_bytes(records))
    if actual != expected:
        raise TrainingMaterializationError(
            "assembled training pool changed while its envelope was being loaded"
        )
    expected_scenarios = envelope_audit.get("expected_synthetic_by_scenario")
    expected_profiles = envelope_audit.get("expected_synthetic_by_factor_profile")
    if expected_scenarios is None and expected_profiles is None:
        return
    if not isinstance(expected_scenarios, Mapping) or not isinstance(
        expected_profiles, Mapping
    ):
        raise TrainingMaterializationError(
            "assembled training pool factor-profile envelope is invalid"
        )
    synthetic = [
        row for row in records if row.get("document_origin") == "synthetic"
    ]
    actual_scenarios = Counter(
        str(row.get("scenario_id") or "") for row in synthetic
    )
    actual_profiles = Counter(
        str(row.get("factor_profile_id") or "") for row in synthetic
    )
    if actual_scenarios != Counter(expected_scenarios) or actual_profiles != Counter(
        expected_profiles
    ):
        raise TrainingMaterializationError(
            "assembled training rows violate the attested scenario/profile quotas"
        )
    for index, row in enumerate(synthetic):
        scores = row.get("expected_factor_scores")
        if (
            not str(row.get("factor_profile_id") or "").strip()
            or not isinstance(scores, Mapping)
            or set(scores) != {"secrecy", "value", "management"}
        ):
            raise TrainingMaterializationError(
                f"synthetic training row {index} lost its factor-profile contract"
            )


def _load_production_public_holdouts(
    paths: Sequence[Path],
    *,
    envelope_audit: Mapping[str, object],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Re-open the exact immutable dev/blind artifacts used by assembly."""
    try:
        attested = load_blocked_corpora(paths)
    except ChallengeAssemblyError as exc:
        raise TrainingMaterializationError(str(exc)) from exc
    expected_artifacts = envelope_audit.get("expected_blocked_public_artifacts")
    if len(attested.files) != expected_artifacts:
        raise TrainingMaterializationError(
            "materialization public holdout artifact count differs from assembly: "
            f"expected={expected_artifacts}, actual={len(attested.files)}"
        )
    expected_fingerprints = envelope_audit.get(
        "expected_blocked_public_artifact_fingerprints"
    )
    actual_fingerprints = _public_holdout_fingerprints(attested.files)
    if actual_fingerprints != expected_fingerprints:
        raise TrainingMaterializationError(
            "materialization public holdout artifact seals differ from assembly"
        )
    records_paths = [Path(str(item["records_path"])) for item in attested.files]
    rows, stats = _load_corpus(records_paths, purpose="attested blocked public corpus")
    return rows, {
        **stats,
        "attested_artifact_count": len(attested.files),
        "attested_artifacts": [dict(item) for item in attested.files],
        "attested_artifact_fingerprints": actual_fingerprints,
    }


def _validate_eligible_records(
    records: Sequence[Mapping[str, object]],
    *,
    purpose: str,
    intended_use: IntendedUse,
) -> None:
    failures: list[dict[str, object]] = []
    for index, record in enumerate(records):
        check = validate_proxy_record(
            record, stage="eligible", intended_use=intended_use
        )
        if not check.ok:
            failures.append(
                {
                    "index": index,
                    "doc_id": str(record.get("doc_id") or ""),
                    "errors": list(check.errors),
                }
            )
            if len(failures) == 20:
                break
    if failures:
        raise TrainingMaterializationError(
            f"{purpose} contains ineligible records: "
            + json.dumps(failures, ensure_ascii=False, sort_keys=True)
        )


def _validate_unique_records(
    records: Sequence[Mapping[str, object]], *, purpose: str
) -> None:
    doc_ids: set[str] = set()
    hashes: set[str] = set()
    for index, record in enumerate(records):
        doc_id = str(record.get("doc_id") or "").strip()
        if not doc_id:
            raise TrainingMaterializationError(
                f"{purpose} missing doc_id at row {index}"
            )
        if doc_id in doc_ids:
            raise TrainingMaterializationError(f"{purpose} duplicate doc_id: {doc_id}")
        digest = text_hash(str(record.get("text") or ""))
        if digest in hashes:
            raise TrainingMaterializationError(
                f"{purpose} duplicate normalized text hash at row {index}: {digest}"
            )
        doc_ids.add(doc_id)
        hashes.add(digest)


def _family_groups(
    records: Sequence[Mapping[str, object]],
) -> dict[str, list[dict[str, object]]]:
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        family = str(record.get("document_family_id") or "").strip()
        if not family:
            raise TrainingMaterializationError(
                "training record is missing document_family_id"
            )
        groups[family].append(dict(record))
    for rows in groups.values():
        rows.sort(key=lambda row: str(row.get("doc_id") or ""))
    return dict(groups)


def _assign_family(
    family: str,
    split_name: str,
    *,
    groups: Mapping[str, Sequence[Mapping[str, object]]],
    assignments: dict[str, str],
    row_counts: Counter,
    grade_counts: Mapping[str, Counter],
    profile_counts: Mapping[str, Counter],
    family_counts: Counter,
) -> None:
    if family in assignments:
        raise TrainingMaterializationError(f"family assigned twice: {family}")
    assignments[family] = split_name
    rows = groups[family]
    row_counts[split_name] += len(rows)
    family_counts[split_name] += 1
    grade_counts[split_name].update(str(row.get("label") or "") for row in rows)
    profile_counts[split_name].update(
        str(row.get("factor_profile_id") or "")
        for row in rows
        if str(row.get("factor_profile_id") or "").strip()
    )


def deterministic_family_split(
    records: Sequence[Mapping[str, object]],
    *,
    ratios: Mapping[str, float] = SPLIT_RATIOS,
    seed: str = SPLIT_SEED,
) -> dict[str, list[dict[str, object]]]:
    """Deterministically split whole families while retaining every grade."""
    if tuple(ratios) != tuple(SPLIT_RATIOS):
        raise TrainingMaterializationError(
            f"split names/order must be {tuple(SPLIT_RATIOS)}"
        )
    if (
        any(float(value) <= 0 for value in ratios.values())
        or abs(sum(ratios.values()) - 1) > 1e-9
    ):
        raise TrainingMaterializationError("split ratios must be positive and sum to 1")
    groups = _family_groups(records)
    if not groups:
        raise TrainingMaterializationError("training corpus is empty")

    families_by_grade: dict[str, set[str]] = {grade: set() for grade in _GRADE_ORDER}
    families_by_profile: dict[str, set[str]] = defaultdict(set)
    total_grades = Counter(str(row.get("label") or "") for row in records)
    unknown = set(total_grades) - GRADE_CODES
    if unknown:
        raise TrainingMaterializationError(
            f"unknown training labels: {sorted(unknown)}"
        )
    for family, rows in groups.items():
        for grade in {str(row.get("label") or "") for row in rows}:
            families_by_grade[grade].add(family)
        for profile in {
            str(row.get("factor_profile_id") or "").strip() for row in rows
        } - {""}:
            families_by_profile[profile].add(family)
    for grade in _GRADE_ORDER:
        if len(families_by_grade[grade]) < len(ratios):
            raise TrainingMaterializationError(
                f"grade {grade} needs at least {len(ratios)} independent families; "
                f"found {len(families_by_grade[grade])}"
            )
    for profile, profile_families in sorted(families_by_profile.items()):
        if len(profile_families) < len(ratios):
            raise TrainingMaterializationError(
                f"factor profile {profile} needs at least {len(ratios)} independent "
                f"families; found {len(profile_families)}"
            )

    assignments: dict[str, str] = {}
    row_counts: Counter = Counter()
    grade_counts: dict[str, Counter] = {name: Counter() for name in ratios}
    profile_counts: dict[str, Counter] = {name: Counter() for name in ratios}
    family_counts: Counter = Counter()
    split_coverage_order = sorted(ratios, key=lambda name: (ratios[name], name))
    grade_coverage_order = sorted(
        _GRADE_ORDER,
        key=lambda grade: (len(families_by_grade[grade]), _GRADE_ORDER.index(grade)),
    )

    # Seed every split with every grade before balancing.  A multi-grade family
    # can satisfy several coverage requirements but is still assigned only once.
    for grade in grade_coverage_order:
        for split_name in split_coverage_order:
            if grade_counts[split_name][grade]:
                continue
            candidates = [
                family
                for family in families_by_grade[grade]
                if family not in assignments
            ]
            if not candidates:
                raise TrainingMaterializationError(
                    f"cannot place grade {grade} in {split_name} without family leakage"
                )
            missing = {
                value for value in _GRADE_ORDER if not grade_counts[split_name][value]
            }

            def coverage_key(family: str) -> tuple[int, int, str]:
                family_grades = {str(row.get("label") or "") for row in groups[family]}
                return (
                    -len(family_grades & missing),
                    len(groups[family]),
                    _stable_hash(f"{seed}:coverage:{split_name}:{family}"),
                )

            selected = min(candidates, key=coverage_key)
            _assign_family(
                selected,
                split_name,
                groups=groups,
                assignments=assignments,
                row_counts=row_counts,
                grade_counts=grade_counts,
                profile_counts=profile_counts,
                family_counts=family_counts,
            )

    # Rare boundary profiles receive explicit family-disjoint coverage before
    # proportional balancing.  This makes validation/calibration useful for
    # all 21 S/V/M cells instead of only preserving the four final grades.
    for profile in sorted(
        families_by_profile,
        key=lambda value: (len(families_by_profile[value]), value),
    ):
        for split_name in split_coverage_order:
            if profile_counts[split_name][profile]:
                continue
            candidates = [
                family
                for family in families_by_profile[profile]
                if family not in assignments
            ]
            if not candidates:
                raise TrainingMaterializationError(
                    f"cannot place factor profile {profile} in {split_name} "
                    "without family leakage"
                )
            missing_profiles = {
                value
                for value in families_by_profile
                if not profile_counts[split_name][value]
            }

            def profile_coverage_key(family: str) -> tuple[int, int, str]:
                family_profiles = {
                    str(row.get("factor_profile_id") or "").strip()
                    for row in groups[family]
                } - {""}
                return (
                    -len(family_profiles & missing_profiles),
                    len(groups[family]),
                    _stable_hash(
                        f"{seed}:profile-coverage:{split_name}:{family}"
                    ),
                )

            selected = min(candidates, key=profile_coverage_key)
            _assign_family(
                selected,
                split_name,
                groups=groups,
                assignments=assignments,
                row_counts=row_counts,
                grade_counts=grade_counts,
                profile_counts=profile_counts,
                family_counts=family_counts,
            )

    target_rows = {name: len(records) * float(ratio) for name, ratio in ratios.items()}
    target_families = {
        name: len(groups) * float(ratio) for name, ratio in ratios.items()
    }
    target_grades = {
        name: {grade: total_grades[grade] * float(ratio) for grade in _GRADE_ORDER}
        for name, ratio in ratios.items()
    }
    total_profiles = Counter(
        str(row.get("factor_profile_id") or "")
        for row in records
        if str(row.get("factor_profile_id") or "").strip()
    )
    target_profiles = {
        name: {
            profile: total_profiles[profile] * float(ratio)
            for profile in total_profiles
        }
        for name, ratio in ratios.items()
    }

    def objective(split_name: str, family: str) -> tuple[float, str]:
        hypothetical_rows = Counter(row_counts)
        hypothetical_families = Counter(family_counts)
        hypothetical_grades = {
            name: Counter(values) for name, values in grade_counts.items()
        }
        hypothetical_profiles = {
            name: Counter(values) for name, values in profile_counts.items()
        }
        hypothetical_rows[split_name] += len(groups[family])
        hypothetical_families[split_name] += 1
        hypothetical_grades[split_name].update(
            str(row.get("label") or "") for row in groups[family]
        )
        hypothetical_profiles[split_name].update(
            str(row.get("factor_profile_id") or "")
            for row in groups[family]
            if str(row.get("factor_profile_id") or "").strip()
        )
        row_error = sum(
            (
                (hypothetical_rows[name] - target_rows[name])
                / max(target_rows[name], 1.0)
            )
            ** 2
            for name in ratios
        )
        family_error = sum(
            (
                (hypothetical_families[name] - target_families[name])
                / max(target_families[name], 1.0)
            )
            ** 2
            for name in ratios
        )
        grade_error = sum(
            (
                (hypothetical_grades[name][grade] - target_grades[name][grade])
                / max(target_grades[name][grade], 1.0)
            )
            ** 2
            for name in ratios
            for grade in _GRADE_ORDER
        )
        profile_error = sum(
            (
                (
                    hypothetical_profiles[name][profile]
                    - target_profiles[name][profile]
                )
                / max(target_profiles[name][profile], 1.0)
            )
            ** 2
            for name in ratios
            for profile in total_profiles
        )
        score = (
            (2.0 * row_error)
            + grade_error
            + profile_error
            + (0.25 * family_error)
        )
        return score, _stable_hash(f"{seed}:tie:{family}:{split_name}")

    remaining = sorted(
        (family for family in groups if family not in assignments),
        key=lambda family: (
            -len(groups[family]),
            _stable_hash(f"{seed}:order:{family}"),
        ),
    )
    for family in remaining:
        selected_split = min(ratios, key=lambda name: objective(name, family))
        _assign_family(
            family,
            selected_split,
            groups=groups,
            assignments=assignments,
            row_counts=row_counts,
            grade_counts=grade_counts,
            profile_counts=profile_counts,
            family_counts=family_counts,
        )

    result = {name: [] for name in ratios}
    for family, split_name in assignments.items():
        result[split_name].extend(groups[family])
    for split_name, rows in result.items():
        rows.sort(
            key=lambda row: (
                str(row.get("document_family_id") or ""),
                str(row.get("doc_id") or ""),
            )
        )
        missing_grades = GRADE_CODES - {str(row.get("label") or "") for row in rows}
        if missing_grades:
            raise TrainingMaterializationError(
                f"{split_name} is missing grades: {sorted(missing_grades)}"
            )
        missing_profiles = set(families_by_profile) - {
            str(row.get("factor_profile_id") or "").strip() for row in rows
        }
        if missing_profiles:
            raise TrainingMaterializationError(
                f"{split_name} is missing factor profiles: {sorted(missing_profiles)}"
            )

    family_sets = {
        name: {str(row["document_family_id"]) for row in rows}
        for name, rows in result.items()
    }
    for index, left in enumerate(ratios):
        for right in tuple(ratios)[index + 1 :]:
            overlap = family_sets[left] & family_sets[right]
            if overlap:
                raise AssertionError(
                    f"family overlap across {left}/{right}: {sorted(overlap)}"
                )
    return result


def _distribution(records: Sequence[Mapping[str, object]]) -> dict[str, object]:
    family_counts = Counter(
        str(record.get("document_family_id") or "") for record in records
    )
    origin_counts = Counter(
        str(record.get("document_origin") or "") for record in records
    )
    origin_label_counts = Counter(
        (
            str(record.get("document_origin") or ""),
            str(record.get("label") or ""),
        )
        for record in records
    )
    factor_profile_counts = Counter(
        str(record.get("factor_profile_id") or "not_applicable")
        for record in records
    )
    scenario_counts = Counter(
        str(record.get("scenario_id") or "not_applicable") for record in records
    )
    return {
        "records": len(records),
        "families": len(family_counts),
        "by_grade": {
            grade: sum(str(record.get("label") or "") == grade for record in records)
            for grade in _GRADE_ORDER
        },
        "by_origin": dict(sorted(origin_counts.items())),
        "by_origin_and_grade": {
            f"{origin}:{grade}": count
            for (origin, grade), count in sorted(origin_label_counts.items())
        },
        "by_factor_profile": dict(sorted(factor_profile_counts.items())),
        "by_scenario": dict(sorted(scenario_counts.items())),
        "by_family": dict(sorted(family_counts.items())),
        "family_size_histogram": dict(
            sorted(Counter(family_counts.values()).items(), key=lambda item: item[0])
        ),
    }


def _normalize_required_training_mix(
    *,
    expected_count: int,
    required_origin_label_counts: Mapping[tuple[str, str], int] | None,
) -> dict[tuple[str, str], int] | None:
    """Resolve the immutable production mix while allowing small unit fixtures."""
    if expected_count == EXPECTED_TRAINING_DOCUMENTS:
        if (
            required_origin_label_counts is not None
            and dict(required_origin_label_counts) != EXPECTED_ORIGIN_LABEL_COUNTS
        ):
            raise TrainingMaterializationError(
                "the 3,000-document production mix cannot be overridden"
            )
        return dict(EXPECTED_ORIGIN_LABEL_COUNTS)
    if required_origin_label_counts is None:
        return None
    normalized: dict[tuple[str, str], int] = {}
    for key, count in required_origin_label_counts.items():
        if (
            not isinstance(key, tuple)
            or len(key) != 2
            or not all(isinstance(value, str) and value for value in key)
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
        ):
            raise TrainingMaterializationError(
                "required_origin_label_counts contains an invalid entry"
            )
        normalized[(key[0], key[1])] = count
    if sum(normalized.values()) != expected_count:
        raise TrainingMaterializationError(
            "required_origin_label_counts must sum to expected_count"
        )
    return normalized


def _validate_training_mix(
    records: Sequence[Mapping[str, object]],
    *,
    required: Mapping[tuple[str, str], int] | None,
) -> None:
    if required is None:
        return
    actual = Counter(
        (
            str(record.get("document_origin") or ""),
            str(record.get("label") or ""),
        )
        for record in records
    )
    if actual != Counter(required):
        raise TrainingMaterializationError(
            "training origin/label mix violates the immutable contract: "
            + json.dumps(
                {
                    "expected": {
                        f"{origin}:{grade}": count
                        for (origin, grade), count in sorted(required.items())
                    },
                    "actual": {
                        f"{origin}:{grade}": count
                        for (origin, grade), count in sorted(actual.items())
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )


def _ensure_no_blocked_overlap(
    training: Sequence[Mapping[str, object]],
    blocked: Sequence[Mapping[str, object]],
) -> None:
    blocked_families = {
        str(record.get("document_family_id") or "").strip() for record in blocked
    }
    blocked_hashes = {text_hash(str(record.get("text") or "")) for record in blocked}
    blocked_doc_ids = {
        str(record.get("doc_id") or "").strip()
        for record in blocked
        if str(record.get("doc_id") or "").strip()
    }
    training_families = {
        str(record.get("document_family_id") or "").strip() for record in training
    }
    training_hashes = {text_hash(str(record.get("text") or "")) for record in training}
    training_doc_ids = {str(record.get("doc_id") or "").strip() for record in training}
    family_overlap = training_families & blocked_families
    text_overlap = training_hashes & blocked_hashes
    doc_id_overlap = training_doc_ids & blocked_doc_ids
    if family_overlap or text_overlap or doc_id_overlap:
        details = {
            "family_overlap_count": len(family_overlap),
            "family_overlap_sample": sorted(family_overlap)[:20],
            "text_hash_overlap_count": len(text_overlap),
            "text_hash_overlap_sample": sorted(text_overlap)[:20],
            "doc_id_overlap_count": len(doc_id_overlap),
            "doc_id_overlap_sample": sorted(doc_id_overlap)[:20],
        }
        raise TrainingMaterializationError(
            "training corpus overlaps frozen/blocked corpus: "
            + json.dumps(details, ensure_ascii=False, sort_keys=True)
        )


def _validate_train_chunks(
    train_documents: Sequence[Mapping[str, object]],
    chunks: Sequence[Mapping[str, object]],
) -> None:
    source_contract = {
        str(row.get("doc_id") or ""): (
            str(row.get("document_family_id") or ""),
            str(row.get("label") or ""),
            str(row.get("scenario_id") or ""),
            str(row.get("factor_profile_id") or ""),
            row.get("expected_factor_scores"),
        )
        for row in train_documents
    }
    seen_source_ids: set[str] = set()
    seen_chunk_ids: set[str] = set()
    for index, chunk in enumerate(chunks):
        source_id = str(chunk.get("source_doc_id") or "").strip()
        chunk_id = str(chunk.get("chunk_id") or "").strip()
        if source_id not in source_contract:
            raise TrainingMaterializationError(
                f"train chunk row {index} references a non-train document: {source_id!r}"
            )
        if not chunk_id or chunk_id in seen_chunk_ids:
            raise TrainingMaterializationError(
                f"train chunk row {index} has missing/duplicate chunk_id: {chunk_id!r}"
            )
        (
            expected_family,
            expected_label,
            expected_scenario,
            expected_profile,
            expected_scores,
        ) = source_contract[source_id]
        if str(chunk.get("doc_id") or "") != source_id:
            raise TrainingMaterializationError(
                f"train chunk row {index} changed source doc_id: {source_id}"
            )
        if str(chunk.get("document_family_id") or "") != expected_family:
            raise TrainingMaterializationError(
                f"train chunk row {index} changed source family: {source_id}"
            )
        if str(chunk.get("label") or "") != expected_label:
            raise TrainingMaterializationError(
                f"train chunk row {index} changed source label: {source_id}"
            )
        if str(chunk.get("scenario_id") or "") != expected_scenario:
            raise TrainingMaterializationError(
                f"train chunk row {index} changed source scenario: {source_id}"
            )
        if str(chunk.get("factor_profile_id") or "") != expected_profile:
            raise TrainingMaterializationError(
                f"train chunk row {index} changed source factor profile: {source_id}"
            )
        if chunk.get("expected_factor_scores") != expected_scores:
            raise TrainingMaterializationError(
                f"train chunk row {index} changed source factor scores: {source_id}"
            )
        seen_source_ids.add(source_id)
        seen_chunk_ids.add(chunk_id)
    missing_sources = set(source_contract) - seen_source_ids
    if missing_sources:
        raise TrainingMaterializationError(
            "train chunk expansion omitted source documents: "
            f"{sorted(missing_sources)[:20]} (count={len(missing_sources)})"
        )


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"proxy-train-{stamp}-{uuid.uuid4().hex[:10]}"


def materialize_proxy_training_set(
    *,
    input_paths: Sequence[Path],
    frozen_paths: Sequence[Path],
    blocked_paths: Sequence[Path] = (),
    out_root: Path,
    run_id: str | None = None,
    expected_count: int = EXPECTED_TRAINING_DOCUMENTS,
    expected_frozen_count: int = EXPECTED_FROZEN_DOCUMENTS,
    required_origin_label_counts: Mapping[tuple[str, str], int] | None = None,
    require_training_pool_envelope: bool | None = None,
    seed: str = SPLIT_SEED,
) -> tuple[Path, dict[str, object]]:
    """Validate, split, expand, and atomically publish one immutable run."""
    if not input_paths:
        raise TrainingMaterializationError("at least one input path is required")
    if not frozen_paths:
        raise TrainingMaterializationError(
            "at least one frozen corpus path is required"
        )
    production_envelope_required = expected_count == EXPECTED_TRAINING_DOCUMENTS
    if production_envelope_required and require_training_pool_envelope is False:
        raise TrainingMaterializationError(
            "the 3,000-document production envelope cannot be bypassed"
        )
    envelope_required = (
        production_envelope_required
        if require_training_pool_envelope is None
        else require_training_pool_envelope
    )
    envelope_audit = (
        _verify_training_pool_envelope(input_paths, expected_count=expected_count)
        if envelope_required
        else {"required": False}
    )
    training, input_stats = _load_corpus(list(input_paths), purpose="training input")
    frozen, frozen_stats = _load_corpus(list(frozen_paths), purpose="frozen corpus")
    if envelope_required:
        _verify_loaded_training_matches_envelope(envelope_audit, training)
    if production_envelope_required:
        additionally_blocked, blocked_stats = _load_production_public_holdouts(
            blocked_paths,
            envelope_audit=envelope_audit,
        )
    else:
        additionally_blocked, blocked_stats = _load_corpus(
            list(blocked_paths), purpose="blocked corpus"
        )
    if production_envelope_required:
        _verify_envelope_exclusion_inputs(
            envelope_audit,
            frozen=frozen,
            blocked_public=additionally_blocked,
        )
    if len(training) != expected_count:
        raise TrainingMaterializationError(
            f"training input must contain exactly {expected_count} records; found {len(training)}"
        )
    if len(frozen) != expected_frozen_count:
        raise TrainingMaterializationError(
            f"frozen corpus must contain exactly {expected_frozen_count} records; "
            f"found {len(frozen)}"
        )
    required_mix = _normalize_required_training_mix(
        expected_count=expected_count,
        required_origin_label_counts=required_origin_label_counts,
    )
    _validate_eligible_records(
        training, purpose="training input", intended_use="training"
    )
    _validate_eligible_records(
        frozen, purpose="frozen corpus", intended_use="evaluation"
    )
    _validate_unique_records(training, purpose="training input")
    _validate_unique_records(frozen, purpose="frozen corpus")
    _validate_training_mix(training, required=required_mix)
    _ensure_no_blocked_overlap(training, [*frozen, *additionally_blocked])

    splits = deterministic_family_split(training, seed=seed)
    train_chunks = expand_records_evidence_aware(splits["train"])
    if not train_chunks:
        raise TrainingMaterializationError("train chunk expansion produced no rows")
    _validate_train_chunks(splits["train"], train_chunks)

    split_family_sets = {
        name: {str(row["document_family_id"]) for row in rows}
        for name, rows in splits.items()
    }
    frozen_families = {str(row["document_family_id"]) for row in frozen}
    if any(families & frozen_families for families in split_family_sets.values()):
        raise AssertionError("frozen family entered a training split")
    frozen_hashes = {text_hash(str(row["text"])) for row in frozen}
    if any(
        text_hash(str(row["text"])) in frozen_hashes
        for rows in splits.values()
        for row in rows
    ):
        raise AssertionError("frozen text entered a training split")

    run_id_value = run_id or _new_run_id()
    if not _SAFE_RUN_ID.fullmatch(run_id_value):
        raise TrainingMaterializationError(f"unsafe run id: {run_id_value!r}")
    out_root.mkdir(parents=True, exist_ok=True)
    final_dir = out_root / run_id_value
    if final_dir.exists():
        raise TrainingMaterializationError(f"run directory already exists: {final_dir}")
    staging_dir = out_root / f".{run_id_value}.staging-{uuid.uuid4().hex}"
    staging_dir.mkdir(exist_ok=False)

    artifacts: dict[str, dict[str, object]] = {}
    output_specs: tuple[tuple[str, str, Sequence[Mapping[str, object]]], ...] = (
        ("train_documents", "train_documents.jsonl", splits["train"]),
        ("validation_documents", "validation_documents.jsonl", splits["validation"]),
        ("calibration_documents", "calibration_documents.jsonl", splits["calibration"]),
        ("train_chunks", "train_chunks.jsonl", train_chunks),
    )
    for key, filename, records in output_specs:
        payload = _jsonl_bytes(records)
        _atomic_write(staging_dir / filename, payload)
        artifacts[key] = {
            "path": filename,
            "sha256": _sha256_bytes(payload),
            **_distribution(records),
        }

    split_distributions = {name: _distribution(rows) for name, rows in splits.items()}
    # Recompute the serialized train/document boundaries instead of merely
    # relying on the family splitter's in-memory invariant.  The finalizer
    # repeats this check from disk; recording the same numbers makes a manifest
    # disagreement a hard failure at either boundary.
    from lloydk.proxy_training_finalization import (  # noqa: PLC0415
        assert_materialized_split_isolation,
    )

    split_leakage = assert_materialized_split_isolation(
        splits["train"],
        splits["validation"],
        splits["calibration"],
        train_chunks,
    )
    if any(value != 0 for value in split_leakage.values()):
        raise TrainingMaterializationError(
            "materialized split leakage detected: "
            + json.dumps(split_leakage, ensure_ascii=False, sort_keys=True)
        )
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id_value,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "claim_scope": "proxy_training_only_not_customer_accuracy",
        "human_reviewed": False,
        "contract": {
            "expected_training_documents": expected_count,
            "expected_frozen_documents": expected_frozen_count,
            "split_seed": seed,
            "target_ratios": dict(SPLIT_RATIOS),
            "split_unit": "document_family_id",
            "normalized_text_hash": "lloydk.hygiene.text_hash",
            "chunk_policy": "train_only_evidence_aware",
            "frozen_membership_allowed": False,
            "training_validation_intended_use": "training",
            "frozen_validation_intended_use": "evaluation",
            "required_origin_label_counts": (
                {
                    f"{origin}:{grade}": count
                    for (origin, grade), count in sorted(required_mix.items())
                }
                if required_mix is not None
                else None
            ),
        },
        "inputs": {
            "training": {
                **_source_stats_with_hashes(input_stats),
                "records_sha256": _sha256_bytes(_canonical_record_bytes(training)),
                "distribution": _distribution(training),
                "assembly_envelope": envelope_audit,
            },
            "frozen": {
                **_source_stats_with_hashes(frozen_stats),
                "records_sha256": _sha256_bytes(_canonical_record_bytes(frozen)),
                "distribution": _distribution(frozen),
            },
            "additional_blocked": {
                **_source_stats_with_hashes(blocked_stats),
                "records_sha256": _sha256_bytes(
                    _canonical_record_bytes(additionally_blocked)
                ),
                "distribution": _distribution(additionally_blocked),
            },
        },
        "leakage_checks": {
            "family_overlap_with_frozen_or_blocked": 0,
            "normalized_text_hash_overlap_with_frozen_or_blocked": 0,
            "doc_id_overlap_with_frozen_or_blocked": 0,
            "family_overlap_across_splits": split_leakage[
                "document_family_id_overlap"
            ],
            "doc_id_overlap_across_splits": split_leakage["doc_id_overlap"],
            "normalized_text_hash_overlap_across_splits": split_leakage[
                "normalized_text_hash_overlap"
            ],
            "train_chunk_source_doc_id_overlap_with_validation_or_calibration": (
                split_leakage["train_chunk_source_doc_id_overlap"]
            ),
            "train_chunk_family_overlap_with_validation_or_calibration": (
                split_leakage["train_chunk_document_family_id_overlap"]
            ),
            "train_chunk_text_hash_overlap_with_validation_or_calibration": (
                split_leakage["train_chunk_normalized_text_hash_overlap"]
            ),
            "frozen_records_in_splits": 0,
        },
        "splits": split_distributions,
        "actual_ratios": {
            name: round(len(rows) / len(training), 8) for name, rows in splits.items()
        },
        "artifacts": artifacts,
    }
    manifest_bytes = _atomic_write_json(staging_dir / "manifest.json", manifest)
    complete = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id_value,
        "committed_at": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "artifacts": {
            key: {"sha256": value["sha256"], "records": value["records"]}
            for key, value in artifacts.items()
        },
    }
    _atomic_write_json(staging_dir / "COMPLETE", complete)
    try:
        staging_dir.rename(final_dir)
    except OSError as exc:
        raise TrainingMaterializationError(
            f"cannot atomically publish run {run_id_value}: {exc}"
        ) from exc
    return final_dir, manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize the exact 2,700 synthetic + 300 public-real training pool "
            "without frozen leakage"
        )
    )
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        help="assembled immutable training_pool.jsonl; repeatable only if disjoint",
    )
    parser.add_argument(
        "--frozen-corpus",
        action="append",
        required=True,
        help="frozen 1,000-record corpus; exclusion-only",
    )
    parser.add_argument(
        "--blocked-corpus",
        action="append",
        default=[],
        help=(
            "immutable public dev/blind challenge directory or records.jsonl; "
            "both exact artifacts are required and hash-bound for production"
        ),
    )
    parser.add_argument("--out-root", default="datasets/proxy_gold/training_runs")
    parser.add_argument("--run-id", help="optional unique immutable run id")
    args = parser.parse_args(argv)
    try:
        run_dir, manifest = materialize_proxy_training_set(
            input_paths=[Path(value) for value in args.input],
            frozen_paths=[Path(value) for value in args.frozen_corpus],
            blocked_paths=[Path(value) for value in args.blocked_corpus],
            out_root=Path(args.out_root),
            run_id=args.run_id,
        )
    except (CorpusLoadError, TrainingMaterializationError, ValueError) as exc:
        raise SystemExit(f"training materialization failed: {exc}") from exc
    print(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "run_id": manifest["run_id"],
                "documents": manifest["inputs"]["training"]["row_count"],
                "train_chunks": manifest["artifacts"]["train_chunks"]["records"],
                "complete": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
