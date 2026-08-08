"""Sequentially orchestrate the ten immutable proxy-generation shards.

This controller is intentionally thin.  It never generates documents itself;
it invokes :mod:`scripts.build_proxy_scenarios` once per deterministic family
shard and verifies every committed generation envelope before accepting it.
One failed shard does not prevent later shards from running, but the controller
returns non-zero unless all ten shards are verified.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping, Sequence

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
sys.path.insert(0, str(_POC))
sys.path.insert(0, str(_POC / "src"))

from scripts.build_proxy_scenarios import (  # noqa: E402
    ProxyGenerationRunError,
    describe_plan,
    generation_model_attestation,
    generation_plan,
    generation_target_maps,
    load_catalog,
    partition_generation_plan_by_family,
)
from lloydk.adapters.llm import build_provider  # noqa: E402
from lloydk.ollama_attestation import (  # noqa: E402
    OllamaAttestationError,
    validate_ollama_attestation,
)
from scripts.judge_proxy_candidates import (  # noqa: E402
    ProxyJudgeContractError,
    attest_generation_input,
)


CONTROLLER_SCHEMA_VERSION = "proxy-generation-shard-controller-v3"
SHARD_COUNT = 10
_SHARED_FILE_MODE = 0o640
_SHARED_DIRECTORY_MODE = 0o2750
_RUN_PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,91}$")
_MODEL_REVISION_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ProxyShardControllerError(ValueError):
    """The controller invocation or an immutable artifact is invalid."""


@dataclass(frozen=True)
class GenerationUseProfile:
    intended_use: str
    split_role: str
    expected_base_total: int
    expected_family_count: int
    expected_grade_totals: dict[str, int]


_USE_PROFILES = {
    "evaluation": GenerationUseProfile(
        intended_use="evaluation",
        split_role="frozen_proxy_eval_only",
        expected_base_total=1000,
        expected_family_count=10,
        expected_grade_totals={"TS": 200, "S1": 250, "S2": 250, "S3": 300},
    ),
    "training": GenerationUseProfile(
        intended_use="training",
        split_role="train_pool_only",
        expected_base_total=2700,
        expected_family_count=15,
        expected_grade_totals={"TS": 750, "S1": 750, "S2": 750, "S3": 450},
    ),
}


@dataclass(frozen=True)
class ShardSpec:
    index: int
    run_id: str
    generation_namespace: str
    plan_sha256: str
    planned: int
    planned_by_grade: dict[str, int]
    planned_by_factor_profile: dict[str, int]
    selection_targets: dict[str, int]
    selection_targets_by_scenario: dict[str, int]
    selection_targets_by_factor_profile: dict[str, int]
    base_final_targets: dict[str, int]
    base_final_targets_by_scenario: dict[str, int]
    base_final_targets_by_factor_profile: dict[str, int]
    factor_profile_by_scenario: dict[str, str]
    partition: dict[str, object]

    def contract_payload(self) -> dict[str, object]:
        return {
            "index": self.index,
            "run_id": self.run_id,
            "generation_namespace": self.generation_namespace,
            "plan_sha256": self.plan_sha256,
            "planned": self.planned,
            "planned_by_grade": dict(sorted(self.planned_by_grade.items())),
            "planned_by_factor_profile": dict(
                sorted(self.planned_by_factor_profile.items())
            ),
            "selection_targets": dict(sorted(self.selection_targets.items())),
            "selection_targets_by_scenario": dict(
                sorted(self.selection_targets_by_scenario.items())
            ),
            "selection_targets_by_factor_profile": dict(
                sorted(self.selection_targets_by_factor_profile.items())
            ),
            "base_final_targets": dict(sorted(self.base_final_targets.items())),
            "base_final_targets_by_scenario": dict(
                sorted(self.base_final_targets_by_scenario.items())
            ),
            "base_final_targets_by_factor_profile": dict(
                sorted(self.base_final_targets_by_factor_profile.items())
            ),
            "factor_profile_by_scenario": dict(
                sorted(self.factor_profile_by_scenario.items())
            ),
            "partition": self.partition,
        }


def _sum_count_maps(values: Sequence[Mapping[str, int]]) -> dict[str, int]:
    total: Counter[str] = Counter()
    for value in values:
        total.update({str(key): int(count) for key, count in value.items()})
    return dict(sorted(total.items()))


def _targets_by_factor_profile(
    targets_by_scenario: Mapping[str, int],
    factor_profile_by_scenario: Mapping[str, str],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    if set(targets_by_scenario) != set(factor_profile_by_scenario):
        raise ProxyShardControllerError(
            "scenario targets and factor-profile mapping keys disagree"
        )
    for scenario_id, count in targets_by_scenario.items():
        profile = str(factor_profile_by_scenario[scenario_id]).strip()
        if not profile:
            raise ProxyShardControllerError(
                f"scenario {scenario_id} has no factor-profile identity"
            )
        counts[profile] += int(count)
    return dict(sorted(counts.items()))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_bytes(path: Path, payload: bytes, *, replace: bool) -> None:
    if path.exists() and not replace:
        raise ProxyShardControllerError(f"refusing to overwrite artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.chmod(temporary, _SHARED_FILE_MODE)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists() and not replace:
            raise ProxyShardControllerError(f"refusing to overwrite artifact: {path}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, value: object, *, replace: bool) -> bytes:
    payload = _json_bytes(value)
    _atomic_write_bytes(path, payload, replace=replace)
    return payload


def _read_json_object(path: Path, *, purpose: str) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ProxyShardControllerError(f"missing or non-regular {purpose}: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProxyShardControllerError(f"invalid {purpose}: {path}") from exc
    if not isinstance(value, dict):
        raise ProxyShardControllerError(f"{purpose} must be a JSON object: {path}")
    return value


def _factor_text(value: float) -> str:
    return format(Decimal(str(value)).normalize(), "f")


def _validate_common_arguments(
    *,
    run_prefix: str,
    provider: str,
    model_manifest_sha256: str,
    candidate_buffer_factor: float,
    oversample_factor: float,
    max_quality_retries: int,
) -> None:
    if not _RUN_PREFIX_RE.fullmatch(run_prefix):
        raise ProxyShardControllerError(
            "run_prefix must be 1-92 safe filename characters"
        )
    compact_provider = re.sub(r"[^a-z0-9]+", "", provider.strip().lower())
    if (
        provider != provider.strip()
        or not compact_provider
        or any(
            compact_provider.startswith(prefix)
            for prefix in ("noop", "unknown", "fake", "mock", "test")
        )
    ):
        raise ProxyShardControllerError("provider must identify a real runtime")
    if not _MODEL_REVISION_RE.fullmatch(model_manifest_sha256):
        raise ProxyShardControllerError(
            "model_manifest_sha256 must be sha256:<64 lowercase hex>"
        )
    if (
        isinstance(candidate_buffer_factor, bool)
        or not math.isfinite(candidate_buffer_factor)
        or candidate_buffer_factor < 1.0
    ):
        raise ProxyShardControllerError("candidate_buffer_factor must be >= 1.0")
    if (
        isinstance(oversample_factor, bool)
        or not math.isfinite(oversample_factor)
        or oversample_factor < candidate_buffer_factor
    ):
        raise ProxyShardControllerError(
            "oversample_factor must be >= candidate_buffer_factor"
        )
    if isinstance(max_quality_retries, bool) or max_quality_retries < 0:
        raise ProxyShardControllerError("max_quality_retries must be >= 0")


def _runtime_model_attestation(
    *, provider: str, model_manifest_sha256: str, live: bool
) -> dict[str, object]:
    """Attest the exact local Ollama model resolved by the generation provider."""
    try:
        runtime_provider = build_provider(provider)
        return generation_model_attestation(
            runtime_provider,
            requested_name=provider,
            expected_model_revision=model_manifest_sha256,
            live=live,
        )
    except (OSError, ValueError, ProxyGenerationRunError) as exc:
        raise ProxyShardControllerError(
            f"generation runtime model attestation failed: {exc}"
        ) from exc


def _build_shard_specs(
    *,
    catalog_path: Path,
    run_prefix: str,
    intended_use: str,
    candidate_buffer_factor: float,
    oversample_factor: float,
) -> tuple[str, str, list[ShardSpec]]:
    profile = _USE_PROFILES.get(intended_use)
    if profile is None:
        raise ProxyShardControllerError(
            "intended_use must be either 'evaluation' or 'training'"
        )
    if catalog_path.is_symlink() or not catalog_path.is_file():
        raise ProxyShardControllerError(
            f"catalog must be a regular file: {catalog_path}"
        )
    try:
        catalog, scenarios = load_catalog(catalog_path)
    except (OSError, json.JSONDecodeError, ValueError, SystemExit) as exc:
        raise ProxyShardControllerError(
            f"invalid generation catalog: {catalog_path}"
        ) from exc
    family_profiles = [
        row for row in catalog.get("family_profiles", []) if isinstance(row, dict)
    ]
    instance_profiles = [
        row for row in catalog.get("instance_profiles", []) if isinstance(row, dict)
    ]
    try:
        full_plan = generation_plan(
            scenarios,
            instance_profiles,
            family_profiles,
            per_scenario=None,
            count_multiplier=oversample_factor,
        )
        full_base_by_scenario, full_base, _, _ = generation_target_maps(
            scenarios,
            per_scenario=None,
            candidate_buffer_factor=candidate_buffer_factor,
        )
    except (ArithmeticError, KeyError, TypeError, ValueError) as exc:
        raise ProxyShardControllerError(
            f"catalog cannot produce the target-count generation plan: {catalog_path}"
        ) from exc
    split_role = str(catalog.get("split_role") or "").strip()
    if split_role != profile.split_role:
        raise ProxyShardControllerError(
            f"{intended_use} controller requires catalog split_role "
            f"{profile.split_role!r}; found {split_role!r}"
        )
    if sum(full_base.values()) != profile.expected_base_total:
        raise ProxyShardControllerError(
            f"{intended_use} target-count catalog must define exactly "
            f"{profile.expected_base_total:,} base records"
        )
    if dict(sorted(full_base.items())) != dict(
        sorted(profile.expected_grade_totals.items())
    ):
        raise ProxyShardControllerError(
            f"{intended_use} catalog grade totals do not match the locked profile"
        )
    family_count = len({str(row[0]["document_family_id"]) for row in full_plan})
    if family_count != profile.expected_family_count:
        raise ProxyShardControllerError(
            f"{intended_use} target-count catalog must define exactly "
            f"{profile.expected_family_count} document families"
        )
    if set(full_base_by_scenario) != {
        str(scenario["scenario_id"]) for scenario in scenarios
    }:
        raise ProxyShardControllerError("catalog scenario target map is incomplete")
    catalog_factor_profiles = {
        str(scenario.get("factor_profile_id") or "").strip()
        for scenario in scenarios
    }
    if "" in catalog_factor_profiles or len(catalog_factor_profiles) != 21:
        raise ProxyShardControllerError(
            "target-count catalog must define exactly 21 factor profiles"
        )

    specs: list[ShardSpec] = []
    for index in range(SHARD_COUNT):
        try:
            shard_plan, partition = partition_generation_plan_by_family(
                full_plan,
                shard_count=SHARD_COUNT,
                shard_index=index,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProxyShardControllerError(
                f"catalog cannot produce deterministic shard {index}"
            ) from exc
        selected_scenario_ids = {str(item[0]["scenario_id"]) for item in shard_plan}
        shard_scenarios = [
            scenario
            for scenario in scenarios
            if str(scenario["scenario_id"]) in selected_scenario_ids
        ]
        (
            base_by_scenario,
            base_by_grade,
            selection_by_scenario,
            selection_by_grade,
        ) = generation_target_maps(
            shard_scenarios,
            per_scenario=None,
            candidate_buffer_factor=candidate_buffer_factor,
        )
        shard_base_total = sum(base_by_grade.values())
        if shard_base_total <= 0:
            raise ProxyShardControllerError(f"shard {index} has no base targets")
        if intended_use == "evaluation" and shard_base_total != 100:
            raise ProxyShardControllerError(
                f"evaluation shard {index} base target is not exactly 100"
            )
        generation_namespace = f"{run_prefix}-s{index:02d}"
        _, summary = describe_plan(
            shard_plan, generation_namespace=generation_namespace
        )
        if any(
            int(summary["by_scenario"].get(scenario_id, 0)) < target
            for scenario_id, target in selection_by_scenario.items()
        ):
            raise ProxyShardControllerError(
                f"shard {index} candidate buffer exceeds its generation plan"
            )
        factor_profile_by_scenario = {
            str(scenario["scenario_id"]): str(
                scenario.get("factor_profile_id") or ""
            ).strip()
            for scenario in shard_scenarios
        }
        if (
            set(factor_profile_by_scenario.values()) != catalog_factor_profiles
            or "" in factor_profile_by_scenario.values()
        ):
            raise ProxyShardControllerError(
                f"shard {index} does not cover the exact 21 factor-profile universe"
            )
        selection_by_factor_profile = _targets_by_factor_profile(
            selection_by_scenario, factor_profile_by_scenario
        )
        base_by_factor_profile = _targets_by_factor_profile(
            base_by_scenario, factor_profile_by_scenario
        )
        planned_by_factor_profile = {
            str(profile_id): int(count)
            for profile_id, count in summary["by_factor_profile"].items()
        }
        profile_maps = (
            planned_by_factor_profile,
            selection_by_factor_profile,
            base_by_factor_profile,
        )
        if (
            any(set(values) != catalog_factor_profiles for values in profile_maps)
            or sum(planned_by_factor_profile.values()) != int(summary["planned"])
            or sum(selection_by_factor_profile.values())
            != sum(selection_by_grade.values())
            or sum(base_by_factor_profile.values()) != sum(base_by_grade.values())
        ):
            raise ProxyShardControllerError(
                f"shard {index} factor-profile target maps are incomplete"
            )
        specs.append(
            ShardSpec(
                index=index,
                run_id=f"{run_prefix}-s{index:02d}",
                generation_namespace=generation_namespace,
                plan_sha256=str(summary["plan_sha256"]),
                planned=int(summary["planned"]),
                planned_by_grade={
                    str(grade): int(count)
                    for grade, count in summary["by_grade"].items()
                },
                planned_by_factor_profile=planned_by_factor_profile,
                selection_targets=selection_by_grade,
                selection_targets_by_scenario=selection_by_scenario,
                selection_targets_by_factor_profile=selection_by_factor_profile,
                base_final_targets=base_by_grade,
                base_final_targets_by_scenario=base_by_scenario,
                base_final_targets_by_factor_profile=base_by_factor_profile,
                factor_profile_by_scenario=factor_profile_by_scenario,
                partition=partition,
            )
        )
    return (
        str(catalog.get("version") or "unknown"),
        _sha256_file(catalog_path),
        specs,
    )


def _controller_contract(
    *,
    run_prefix: str,
    intended_use: str,
    catalog_path: Path,
    catalog_version: str,
    catalog_sha256: str,
    generation_out_root: Path,
    provider: str,
    model_manifest_sha256: str,
    runtime_model_attestation: Mapping[str, object],
    candidate_buffer_factor: float,
    oversample_factor: float,
    max_quality_retries: int,
    specs: Sequence[ShardSpec],
) -> dict[str, object]:
    profile = _USE_PROFILES[intended_use]
    builder_path = _HERE / "build_proxy_scenarios.py"
    generator_path = _POC / "src/lloydk/modules/m1_synthesis/generator.py"
    material: dict[str, object] = {
        "schema_version": CONTROLLER_SCHEMA_VERSION,
        "run_prefix": run_prefix,
        "intended_use": intended_use,
        "catalog_split_role": profile.split_role,
        "shard_count": SHARD_COUNT,
        "target_counts": True,
        "expected_base_total": profile.expected_base_total,
        "expected_grade_totals": dict(sorted(profile.expected_grade_totals.items())),
        "catalog_version": catalog_version,
        "catalog_sha256": catalog_sha256,
        "builder_code_sha256": _sha256_file(builder_path),
        "generator_code_sha256": _sha256_file(generator_path),
        "controller_code_sha256": _sha256_file(Path(__file__)),
        "generation_out_root_sha256": _sha256_bytes(
            str(generation_out_root.resolve()).encode("utf-8")
        ),
        "provider": provider,
        "model_manifest_sha256": model_manifest_sha256,
        "model_runtime_attestation_sha256": runtime_model_attestation[
            "binding_sha256"
        ],
        "model_endpoint_identity_sha256": runtime_model_attestation[
            "endpoint_identity_sha256"
        ],
        "model_requested_name": runtime_model_attestation["requested_model"],
        "candidate_buffer_factor": _factor_text(candidate_buffer_factor),
        "oversample_factor": _factor_text(oversample_factor),
        "max_quality_retries": max_quality_retries,
        "shards": [spec.contract_payload() for spec in specs],
    }
    return {
        **material,
        "run_contract_sha256": _sha256_bytes(_canonical_json_bytes(material)),
    }


def _verify_completed_shard(
    shard_dir: Path,
    *,
    spec: ShardSpec,
    contract: Mapping[str, object],
) -> dict[str, object]:
    if shard_dir.is_symlink() or not shard_dir.is_dir():
        raise ProxyShardControllerError(
            f"shard run is missing or non-regular: {shard_dir}"
        )
    try:
        attestation = attest_generation_input(
            shard_dir / "candidates.jsonl",
            intended_use=str(contract["intended_use"]),
        )
    except ProxyJudgeContractError as exc:
        raise ProxyShardControllerError(
            f"shard {spec.index} generation envelope failed verification: {exc}"
        ) from exc
    manifest = _read_json_object(
        shard_dir / "manifest.json", purpose=f"shard {spec.index} manifest"
    )
    provider = manifest.get("provider")
    if not isinstance(provider, Mapping):
        raise ProxyShardControllerError(f"shard {spec.index} provider is missing")
    expected = {
        "run_id": spec.run_id,
        "generation_namespace": spec.generation_namespace,
        "catalog_version": contract["catalog_version"],
        "catalog_sha256": contract["catalog_sha256"],
        "runner_code_sha256": contract["builder_code_sha256"],
        "generator_code_sha256": contract["generator_code_sha256"],
        "selection_targets": spec.selection_targets,
        "selection_targets_by_scenario": spec.selection_targets_by_scenario,
        "base_final_targets": spec.base_final_targets,
        "base_final_targets_by_scenario": spec.base_final_targets_by_scenario,
        "partition": spec.partition,
        "plan_sha256": spec.plan_sha256,
        "max_quality_retries": contract["max_quality_retries"],
    }
    mismatches = [
        field for field, value in expected.items() if manifest.get(field) != value
    ]
    if provider.get("requested") != contract["provider"]:
        mismatches.append("provider.requested")
    if provider.get("revision") != contract["model_manifest_sha256"]:
        mismatches.append("provider.revision")
    if provider.get("model") != contract["model_requested_name"]:
        mismatches.append("provider.model")
    if (
        provider.get("endpoint_identity_sha256")
        != contract["model_endpoint_identity_sha256"]
    ):
        mismatches.append("provider.endpoint_identity_sha256")
    if (
        provider.get("model_attestation_binding_sha256")
        != contract["model_runtime_attestation_sha256"]
    ):
        mismatches.append("provider.model_attestation_binding_sha256")
    attested_profile_fields = {
        "candidate_by_factor_profile": spec.selection_targets_by_factor_profile,
        "selection_target_by_factor_profile": (
            spec.selection_targets_by_factor_profile
        ),
        "base_final_target_by_factor_profile": (
            spec.base_final_targets_by_factor_profile
        ),
        "factor_profile_by_scenario": spec.factor_profile_by_scenario,
        "generation_model_attestation_binding_sha256": contract[
            "model_runtime_attestation_sha256"
        ],
    }
    if attestation.get("generation_namespace") != spec.generation_namespace:
        mismatches.append("attestation.generation_namespace")
    mismatches.extend(
        f"attestation.{field}"
        for field, value in attested_profile_fields.items()
        if attestation.get(field) != value
    )
    if mismatches:
        raise ProxyShardControllerError(
            f"shard {spec.index} does not match controller contract: "
            + ",".join(sorted(mismatches))
        )
    fresh_model_attestation = _runtime_model_attestation(
        provider=str(contract["provider"]),
        model_manifest_sha256=str(contract["model_manifest_sha256"]),
        live=True,
    )
    if (
        fresh_model_attestation.get("binding_sha256")
        != contract["model_runtime_attestation_sha256"]
        or fresh_model_attestation.get("endpoint_identity_sha256")
        != contract["model_endpoint_identity_sha256"]
        or fresh_model_attestation.get("requested_model")
        != contract["model_requested_name"]
    ):
        raise ProxyShardControllerError(
            f"shard {spec.index} live generation model binding changed"
        )
    complete = _read_json_object(
        shard_dir / "COMPLETE.json", purpose=f"shard {spec.index} COMPLETE"
    )
    if complete.get("target_met") is not True:
        raise ProxyShardControllerError(f"shard {spec.index} target_met is not true")
    return {
        "generation_run_contract_sha256": attestation["generation_run_contract_sha256"],
        "generation_namespace": attestation["generation_namespace"],
        "candidates_sha256": attestation["candidates_sha256"],
        "rejected_sha256": attestation["rejected_sha256"],
        "stats_sha256": attestation["stats_sha256"],
        "candidate_count": attestation["input_count"],
        "model_runtime_attestation_sha256": attestation[
            "generation_model_attestation_binding_sha256"
        ],
        "model_attestation_revalidated_at": fresh_model_attestation["checked_at"],
        "candidate_by_factor_profile": attestation[
            "candidate_by_factor_profile"
        ],
        "rejected_count": attestation["rejected_count"],
        "target_met": True,
    }


def _subprocess_command(
    *,
    spec: ShardSpec,
    catalog_path: Path,
    generation_out_root: Path,
    provider: str,
    model_manifest_sha256: str,
    candidate_buffer_factor: float,
    oversample_factor: float,
    max_quality_retries: int,
    resume_run: Path | None = None,
) -> list[str]:
    command = [
        sys.executable,
        str(_HERE / "build_proxy_scenarios.py"),
        "--catalog",
        str(catalog_path),
        "--out-root",
        str(generation_out_root),
        "--provider",
        provider,
        "--model-manifest-sha256",
        model_manifest_sha256,
        "--generation-namespace",
        spec.generation_namespace,
        "--target-counts",
        "--candidate-buffer-factor",
        _factor_text(candidate_buffer_factor),
        "--oversample-factor",
        _factor_text(oversample_factor),
        "--max-quality-retries",
        str(max_quality_retries),
        "--shard-count",
        str(SHARD_COUNT),
        "--shard-index",
        str(spec.index),
    ]
    if resume_run is None:
        command[6:6] = ["--run-id", spec.run_id]
    else:
        command[6:6] = ["--resume-run", str(resume_run.resolve())]
    return command


def _progress_payload(
    *,
    run_prefix: str,
    run_contract_sha256: str,
    results: Sequence[Mapping[str, object]],
    status: str,
    active_shard_index: int | None = None,
) -> dict[str, object]:
    return {
        "schema_version": CONTROLLER_SCHEMA_VERSION,
        "run_prefix": run_prefix,
        "run_contract_sha256": run_contract_sha256,
        "status": status,
        "updated_at": _utc_now(),
        "active_shard_index": active_shard_index,
        "completed_shard_indices": [int(row["index"]) for row in results],
        "successful_shards": sum(
            str(row.get("status"))
            in {"completed", "resumed_completed", "skipped_verified"}
            for row in results
        ),
        "failed_shards": sum(str(row.get("status")) == "failed" for row in results),
        "results": list(results),
    }


def _prepare_controller_directory(
    *,
    controller_out_root: Path,
    resume_controller: Path | None,
    run_prefix: str,
    contract: Mapping[str, object],
    runtime_model_attestation: Mapping[str, object],
) -> tuple[Path, dict[str, object]]:
    if resume_controller is None:
        controller_out_root.mkdir(parents=True, exist_ok=True)
        controller_dir = controller_out_root / run_prefix
        try:
            controller_dir.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise ProxyShardControllerError(
                "controller directory exists; use --resume-controller explicitly"
            ) from exc
        os.chmod(controller_dir, _SHARED_DIRECTORY_MODE)
        manifest: dict[str, object] = {
            **contract,
            "status": "running",
            "created_at": _utc_now(),
            "resume_count": 0,
            "runtime_model_attestation": dict(runtime_model_attestation),
            "runtime_model_attestation_revalidations": [],
        }
        _atomic_write_json(controller_dir / "manifest.json", manifest, replace=False)
        _atomic_write_json(
            controller_dir / "progress.json",
            _progress_payload(
                run_prefix=run_prefix,
                run_contract_sha256=str(contract["run_contract_sha256"]),
                results=[],
                status="running",
            ),
            replace=False,
        )
        return controller_dir, manifest

    if resume_controller.is_symlink():
        raise ProxyShardControllerError(
            f"resume controller must not be a symlink: {resume_controller}"
        )
    controller_dir = resume_controller.resolve()
    if not controller_dir.is_dir():
        raise ProxyShardControllerError(
            f"resume controller is missing or non-regular: {controller_dir}"
        )
    os.chmod(controller_dir, _SHARED_DIRECTORY_MODE)
    if (controller_dir / "COMPLETE.json").exists():
        raise ProxyShardControllerError("completed controller is immutable")
    manifest = _read_json_object(
        controller_dir / "manifest.json", purpose="controller manifest"
    )
    progress = _read_json_object(
        controller_dir / "progress.json", purpose="controller progress"
    )
    if manifest.get("schema_version") != CONTROLLER_SCHEMA_VERSION:
        raise ProxyShardControllerError("controller schema version mismatch")
    if manifest.get("run_prefix") != run_prefix:
        raise ProxyShardControllerError("controller run_prefix mismatch")
    if manifest.get("status") not in {"running", "interrupted"}:
        raise ProxyShardControllerError("controller is not in a resumable state")
    if manifest.get("run_contract_sha256") != contract["run_contract_sha256"]:
        raise ProxyShardControllerError("controller resume contract mismatch")
    if any(manifest.get(field) != value for field, value in contract.items()):
        raise ProxyShardControllerError("controller manifest contract fields mismatch")
    try:
        recorded_model_attestation = validate_ollama_attestation(
            manifest.get("runtime_model_attestation"), require_verified=True
        )
        validated_runtime_model_attestation = validate_ollama_attestation(
            runtime_model_attestation, require_verified=True
        )
    except OllamaAttestationError as exc:
        raise ProxyShardControllerError(
            "controller runtime model attestation is invalid"
        ) from exc
    if (
        recorded_model_attestation["binding_sha256"]
        != contract["model_runtime_attestation_sha256"]
        or validated_runtime_model_attestation["binding_sha256"]
        != contract["model_runtime_attestation_sha256"]
    ):
        raise ProxyShardControllerError(
            "controller runtime model attestation binding mismatch"
        )
    if progress.get("run_contract_sha256") != contract["run_contract_sha256"]:
        raise ProxyShardControllerError("controller progress contract mismatch")
    if (
        progress.get("schema_version") != CONTROLLER_SCHEMA_VERSION
        or progress.get("run_prefix") != run_prefix
        or progress.get("status") not in {"running", "interrupted"}
        or not isinstance(progress.get("results"), list)
        or not isinstance(progress.get("completed_shard_indices"), list)
    ):
        raise ProxyShardControllerError("controller progress structure mismatch")
    active_index = progress.get("active_shard_index")
    if active_index is not None and (
        isinstance(active_index, bool)
        or not isinstance(active_index, int)
        or not 0 <= active_index < SHARD_COUNT
    ):
        raise ProxyShardControllerError("controller active shard index is invalid")
    progress_indices = [
        row.get("index") for row in progress["results"] if isinstance(row, Mapping)
    ]
    if (
        len(progress_indices) != len(progress["results"])
        or progress_indices != progress["completed_shard_indices"]
        or len(progress_indices) != len(set(progress_indices))
        or any(
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < SHARD_COUNT
            for index in progress_indices
        )
    ):
        raise ProxyShardControllerError("controller progress shard indices are invalid")
    for forbidden in ("stats.json", "COMPLETE.json"):
        if (controller_dir / forbidden).exists():
            raise ProxyShardControllerError(
                f"incomplete controller contains unexpected final artifact: {forbidden}"
            )
    resume_count = manifest.get("resume_count", 0)
    resumed_at = manifest.get("resumed_at", [])
    if (
        isinstance(resume_count, bool)
        or not isinstance(resume_count, int)
        or resume_count < 0
        or not isinstance(resumed_at, list)
        or any(not isinstance(value, str) for value in resumed_at)
    ):
        raise ProxyShardControllerError("controller resume metadata is invalid")
    manifest["status"] = "running"
    manifest["resume_count"] = resume_count + 1
    manifest["resumed_at"] = [*resumed_at, _utc_now()]
    revalidations = manifest.get("runtime_model_attestation_revalidations", [])
    if not isinstance(revalidations, list):
        raise ProxyShardControllerError(
            "controller runtime model attestation revalidations are invalid"
        )
    manifest["runtime_model_attestation_revalidations"] = [
        *revalidations,
        dict(validated_runtime_model_attestation),
    ]
    _atomic_write_json(controller_dir / "manifest.json", manifest, replace=True)
    return controller_dir, manifest


def run_controller(
    *,
    catalog_path: Path,
    generation_out_root: Path,
    controller_out_root: Path,
    run_prefix: str,
    intended_use: str = "evaluation",
    provider: str,
    model_manifest_sha256: str,
    candidate_buffer_factor: float,
    oversample_factor: float,
    max_quality_retries: int,
    resume_controller: Path | None = None,
    subprocess_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> tuple[Path, dict[str, object], int]:
    """Run all shards sequentially and commit controller audit artifacts."""
    _validate_common_arguments(
        run_prefix=run_prefix,
        provider=provider,
        model_manifest_sha256=model_manifest_sha256,
        candidate_buffer_factor=candidate_buffer_factor,
        oversample_factor=oversample_factor,
        max_quality_retries=max_quality_retries,
    )
    runtime_model_attestation = _runtime_model_attestation(
        provider=provider,
        model_manifest_sha256=model_manifest_sha256,
        live=True,
    )
    catalog_path = catalog_path.resolve()
    generation_out_root = generation_out_root.resolve()
    controller_out_root = controller_out_root.resolve()
    catalog_version, catalog_sha256, specs = _build_shard_specs(
        catalog_path=catalog_path,
        run_prefix=run_prefix,
        intended_use=intended_use,
        candidate_buffer_factor=candidate_buffer_factor,
        oversample_factor=oversample_factor,
    )
    contract = _controller_contract(
        run_prefix=run_prefix,
        intended_use=intended_use,
        catalog_path=catalog_path,
        catalog_version=catalog_version,
        catalog_sha256=catalog_sha256,
        generation_out_root=generation_out_root,
        provider=provider,
        model_manifest_sha256=model_manifest_sha256,
        runtime_model_attestation=runtime_model_attestation,
        candidate_buffer_factor=candidate_buffer_factor,
        oversample_factor=oversample_factor,
        max_quality_retries=max_quality_retries,
        specs=specs,
    )
    controller_dir, manifest = _prepare_controller_directory(
        controller_out_root=controller_out_root,
        resume_controller=resume_controller,
        run_prefix=run_prefix,
        contract=contract,
        runtime_model_attestation=runtime_model_attestation,
    )
    generation_out_root.mkdir(parents=True, exist_ok=True)
    logs_dir = controller_dir / "logs"
    if logs_dir.exists():
        if logs_dir.is_symlink() or not logs_dir.is_dir():
            raise ProxyShardControllerError(
                f"controller logs path is not a regular directory: {logs_dir}"
            )
    else:
        logs_dir.mkdir()
    os.chmod(logs_dir, _SHARED_DIRECTORY_MODE)
    results: list[dict[str, object]] = []
    is_resume = resume_controller is not None
    attempt_index = int(manifest.get("resume_count", 0))
    active_shard_index: int | None = None
    launch = subprocess_runner or subprocess.run

    try:
        for spec in specs:
            active_shard_index = spec.index
            _atomic_write_json(
                controller_dir / "progress.json",
                _progress_payload(
                    run_prefix=run_prefix,
                    run_contract_sha256=str(contract["run_contract_sha256"]),
                    results=results,
                    status="running",
                    active_shard_index=active_shard_index,
                ),
                replace=True,
            )
            shard_dir = generation_out_root / spec.run_id
            result: dict[str, object] = {
                "index": spec.index,
                "run_id": spec.run_id,
                "shard_dir": str(shard_dir),
                "started_at": _utc_now(),
            }
            command: list[str] | None = None
            success_status = "completed"
            if shard_dir.exists() or shard_dir.is_symlink():
                if not is_resume:
                    result.update(
                        status="failed",
                        failure="existing_shard_requires_explicit_resume",
                    )
                elif shard_dir.is_symlink() or not shard_dir.is_dir():
                    result.update(
                        status="failed",
                        failure="resume_shard_is_not_a_regular_directory",
                    )
                elif (shard_dir / "COMPLETE.json").exists():
                    try:
                        verified = _verify_completed_shard(
                            shard_dir, spec=spec, contract=contract
                        )
                    except ProxyShardControllerError as exc:
                        result.update(status="failed", failure=str(exc))
                    else:
                        result.update(status="skipped_verified", **verified)
                else:
                    command = _subprocess_command(
                        spec=spec,
                        catalog_path=catalog_path,
                        generation_out_root=generation_out_root,
                        provider=provider,
                        model_manifest_sha256=model_manifest_sha256,
                        candidate_buffer_factor=candidate_buffer_factor,
                        oversample_factor=oversample_factor,
                        max_quality_retries=max_quality_retries,
                        resume_run=shard_dir,
                    )
                    success_status = "resumed_completed"
            else:
                command = _subprocess_command(
                    spec=spec,
                    catalog_path=catalog_path,
                    generation_out_root=generation_out_root,
                    provider=provider,
                    model_manifest_sha256=model_manifest_sha256,
                    candidate_buffer_factor=candidate_buffer_factor,
                    oversample_factor=oversample_factor,
                    max_quality_retries=max_quality_retries,
                )
            if command is not None:
                result["command"] = command
                try:
                    completed = launch(
                        command,
                        cwd=str(_POC),
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                except KeyboardInterrupt:
                    raise
                except OSError as exc:
                    result.update(status="failed", failure=f"launch failed: {exc}")
                else:
                    stdout = (completed.stdout or "").encode("utf-8")
                    stderr = (completed.stderr or "").encode("utf-8")
                    stdout_path = (
                        logs_dir
                        / f"shard-{spec.index:02d}.attempt-{attempt_index:02d}.stdout.log"
                    )
                    stderr_path = (
                        logs_dir
                        / f"shard-{spec.index:02d}.attempt-{attempt_index:02d}.stderr.log"
                    )
                    _atomic_write_bytes(stdout_path, stdout, replace=False)
                    _atomic_write_bytes(stderr_path, stderr, replace=False)
                    result.update(
                        returncode=int(completed.returncode),
                        stdout_sha256=_sha256_bytes(stdout),
                        stderr_sha256=_sha256_bytes(stderr),
                    )
                    if completed.returncode != 0:
                        result.update(
                            status="failed",
                            failure=f"generation subprocess exited {completed.returncode}",
                        )
                    else:
                        try:
                            verified = _verify_completed_shard(
                                shard_dir, spec=spec, contract=contract
                            )
                        except ProxyShardControllerError as exc:
                            result.update(status="failed", failure=str(exc))
                        else:
                            result.update(status=success_status, **verified)
            result["finished_at"] = _utc_now()
            results.append(result)
            active_shard_index = None
            _atomic_write_json(
                controller_dir / "progress.json",
                _progress_payload(
                    run_prefix=run_prefix,
                    run_contract_sha256=str(contract["run_contract_sha256"]),
                    results=results,
                    status="running",
                    active_shard_index=None,
                ),
                replace=True,
            )
    except KeyboardInterrupt:
        manifest["status"] = "interrupted"
        manifest["interrupted_at"] = _utc_now()
        manifest["completed_shards"] = len(results)
        _atomic_write_json(controller_dir / "manifest.json", manifest, replace=True)
        _atomic_write_json(
            controller_dir / "progress.json",
            _progress_payload(
                run_prefix=run_prefix,
                run_contract_sha256=str(contract["run_contract_sha256"]),
                results=results,
                status="interrupted",
                active_shard_index=active_shard_index,
            ),
            replace=True,
        )
        return controller_dir, {"status": "interrupted", "results": results}, 130

    successful_statuses = {"completed", "resumed_completed", "skipped_verified"}
    successful = sum(row.get("status") in successful_statuses for row in results)
    failed = sum(row.get("status") == "failed" for row in results)
    target_met = successful == SHARD_COUNT and failed == 0
    stats: dict[str, object] = {
        "schema_version": CONTROLLER_SCHEMA_VERSION,
        "run_prefix": run_prefix,
        "intended_use": intended_use,
        "catalog_split_role": contract["catalog_split_role"],
        "run_contract_sha256": contract["run_contract_sha256"],
        "model_runtime_attestation_sha256": contract[
            "model_runtime_attestation_sha256"
        ],
        "status": "complete" if target_met else "failed",
        "shard_count": SHARD_COUNT,
        "attempted_shards": len(results),
        "launched_shards": sum("command" in row for row in results),
        "skipped_verified_shards": sum(
            row.get("status") == "skipped_verified" for row in results
        ),
        "resumed_completed_shards": sum(
            row.get("status") == "resumed_completed" for row in results
        ),
        "successful_shards": successful,
        "failed_shards": failed,
        "planned_generation_attempts": sum(spec.planned for spec in specs),
        "planned_generation_attempts_by_factor_profile": _sum_count_maps(
            [spec.planned_by_factor_profile for spec in specs]
        ),
        "base_final_target_total": sum(
            sum(spec.base_final_targets.values()) for spec in specs
        ),
        "base_final_target_by_factor_profile": _sum_count_maps(
            [spec.base_final_targets_by_factor_profile for spec in specs]
        ),
        "prejudge_candidate_target_total": sum(
            sum(spec.selection_targets.values()) for spec in specs
        ),
        "prejudge_candidate_target_by_factor_profile": _sum_count_maps(
            [spec.selection_targets_by_factor_profile for spec in specs]
        ),
        "verified_candidate_count": sum(
            int(row.get("candidate_count", 0))
            for row in results
            if row.get("status") in successful_statuses
        ),
        "verified_rejected_count": sum(
            int(row.get("rejected_count", 0))
            for row in results
            if row.get("status") in successful_statuses
        ),
        "verified_candidate_by_factor_profile": _sum_count_maps(
            [
                row.get("candidate_by_factor_profile", {})
                for row in results
                if row.get("status") in successful_statuses
                and isinstance(row.get("candidate_by_factor_profile"), Mapping)
            ]
        ),
        "target_met": target_met,
        "results": results,
    }
    stats_payload = _atomic_write_json(
        controller_dir / "stats.json", stats, replace=False
    )
    progress_payload = _atomic_write_json(
        controller_dir / "progress.json",
        _progress_payload(
            run_prefix=run_prefix,
            run_contract_sha256=str(contract["run_contract_sha256"]),
            results=results,
            status="complete" if target_met else "failed",
        ),
        replace=True,
    )
    manifest.update(
        {
            "status": "complete",
            "completed_at": _utc_now(),
            "target_met": target_met,
            "stats": stats,
            "final_artifacts": {
                "stats": "stats.json",
                "stats_sha256": _sha256_bytes(stats_payload),
                "progress": "progress.json",
                "progress_sha256": _sha256_bytes(progress_payload),
            },
        }
    )
    manifest_payload = _atomic_write_json(
        controller_dir / "manifest.json", manifest, replace=True
    )
    complete = {
        "schema_version": CONTROLLER_SCHEMA_VERSION,
        "run_prefix": run_prefix,
        "run_contract_sha256": contract["run_contract_sha256"],
        "model_runtime_attestation_sha256": contract[
            "model_runtime_attestation_sha256"
        ],
        "manifest_sha256": _sha256_bytes(manifest_payload),
        "stats_sha256": _sha256_bytes(stats_payload),
        "progress_sha256": _sha256_bytes(progress_payload),
        "target_met": target_met,
        "exit_code": 0 if target_met else 1,
        "runtime_model_attestation": dict(runtime_model_attestation),
    }
    _atomic_write_json(controller_dir / "COMPLETE.json", complete, replace=False)
    return controller_dir, stats, 0 if target_met else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog", default="datasets/proxy_gold/scenario_catalog.v1.json"
    )
    parser.add_argument(
        "--generation-out-root",
        default="datasets/proxy_gold/generation_runs",
    )
    parser.add_argument(
        "--controller-out-root",
        default="datasets/proxy_gold/generation_controllers",
    )
    parser.add_argument("--run-prefix", required=True)
    parser.add_argument(
        "--intended-use",
        choices=sorted(_USE_PROFILES),
        required=True,
        help="bind the controller to the matching catalog split and usage contract",
    )
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model-manifest-sha256", required=True)
    parser.add_argument("--target-counts", action="store_true")
    parser.add_argument("--candidate-buffer-factor", type=float, default=1.0)
    parser.add_argument("--oversample-factor", type=float, default=1.0)
    parser.add_argument("--max-quality-retries", type=int, default=1)
    parser.add_argument("--resume-controller", type=Path)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print the immutable ten-shard plan without writing artifacts",
    )
    parser.add_argument("--allow-partial", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.target_counts:
        parser.error(
            "--target-counts is required; pilot/per-scenario mode is forbidden"
        )
    if args.allow_partial:
        parser.error("--allow-partial is forbidden for promotable shard runs")
    if args.dry_run and args.resume_controller is not None:
        parser.error("--dry-run cannot be combined with --resume-controller")
    try:
        if args.dry_run:
            _validate_common_arguments(
                run_prefix=args.run_prefix,
                provider=args.provider,
                model_manifest_sha256=args.model_manifest_sha256,
                candidate_buffer_factor=args.candidate_buffer_factor,
                oversample_factor=args.oversample_factor,
                max_quality_retries=args.max_quality_retries,
            )
            runtime_model_attestation = _runtime_model_attestation(
                provider=args.provider,
                model_manifest_sha256=args.model_manifest_sha256,
                live=False,
            )
            catalog_path = Path(args.catalog).resolve()
            generation_out_root = Path(args.generation_out_root).resolve()
            catalog_version, catalog_sha256, specs = _build_shard_specs(
                catalog_path=catalog_path,
                run_prefix=args.run_prefix,
                intended_use=args.intended_use,
                candidate_buffer_factor=args.candidate_buffer_factor,
                oversample_factor=args.oversample_factor,
            )
            contract = _controller_contract(
                run_prefix=args.run_prefix,
                intended_use=args.intended_use,
                catalog_path=catalog_path,
                catalog_version=catalog_version,
                catalog_sha256=catalog_sha256,
                generation_out_root=generation_out_root,
                provider=args.provider,
                model_manifest_sha256=args.model_manifest_sha256,
                runtime_model_attestation=runtime_model_attestation,
                candidate_buffer_factor=args.candidate_buffer_factor,
                oversample_factor=args.oversample_factor,
                max_quality_retries=args.max_quality_retries,
                specs=specs,
            )
            commands = [
                _subprocess_command(
                    spec=spec,
                    catalog_path=catalog_path,
                    generation_out_root=generation_out_root,
                    provider=args.provider,
                    model_manifest_sha256=args.model_manifest_sha256,
                    candidate_buffer_factor=args.candidate_buffer_factor,
                    oversample_factor=args.oversample_factor,
                    max_quality_retries=args.max_quality_retries,
                )
                for spec in specs
            ]
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "controller_contract": contract,
                        "runtime_model_attestation": runtime_model_attestation,
                        "commands": commands,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        controller_dir, stats, exit_code = run_controller(
            catalog_path=Path(args.catalog),
            generation_out_root=Path(args.generation_out_root),
            controller_out_root=Path(args.controller_out_root),
            run_prefix=args.run_prefix,
            intended_use=args.intended_use,
            provider=args.provider,
            model_manifest_sha256=args.model_manifest_sha256,
            candidate_buffer_factor=args.candidate_buffer_factor,
            oversample_factor=args.oversample_factor,
            max_quality_retries=args.max_quality_retries,
            resume_controller=args.resume_controller,
        )
    except ProxyShardControllerError as exc:
        print(f"proxy shard controller failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "controller_dir": str(controller_dir),
                "stats": stats,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
