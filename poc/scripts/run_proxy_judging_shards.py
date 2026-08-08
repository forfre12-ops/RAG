"""Safely judge every shard from one committed generation controller.

The controller deliberately runs one judge process at a time.  A single local
GPU cannot safely host multiple long-running consensus judges, and correctness
is more important than speculative throughput here.  Each generation shard is
independently attested before launch and each completed judge envelope is
verified before it is accepted.

Existing judge directories are never reused by a new controller invocation.
Only an explicit controller resume may skip an existing shard, and then only
after its COMPLETE marker, manifest, input binding, model revisions, usage
contract, artifact hashes, and record counts have all been revalidated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping, Sequence
from urllib.parse import urlsplit

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
sys.path.insert(0, str(_POC))
sys.path.insert(0, str(_POC / "src"))

from scripts.judge_proxy_candidates import (  # noqa: E402
    PROXY_GATE_VERSION,
    RUN_SCHEMA_VERSION as JUDGE_RUN_SCHEMA_VERSION,
    ProxyJudgeContractError,
    _canonical_model_id,
    _identity,
    _record_digest,
    attest_generation_input,
    load_candidates,
    validate_model_contract,
)
from scripts.run_proxy_generation_shards import (  # noqa: E402
    CONTROLLER_SCHEMA_VERSION as GENERATION_CONTROLLER_SCHEMA_VERSION,
    SHARD_COUNT,
)
from lloydk.ollama_attestation import (  # noqa: E402
    OllamaAttestationError,
    pending_ollama_model_attestation,
    validate_ollama_attestation,
    verify_ollama_model,
)


CONTROLLER_SCHEMA_VERSION = "proxy-judge-shard-controller-v3"
_ARTIFACT_FILE_MODE = 0o640
_ARTIFACT_DIRECTORY_MODE = 0o2750
_RUN_PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,91}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA256_REVISION_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$")
_BLOCKED_PYTHON_ENV = frozenset(
    {
        "PYTHONBREAKPOINT",
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONUSERBASE",
        "PYTHONWARNINGS",
    }
)
_GENERATION_CONTRACT_FIELDS = (
    "schema_version",
    "run_prefix",
    "intended_use",
    "catalog_split_role",
    "shard_count",
    "target_counts",
    "expected_base_total",
    "expected_grade_totals",
    "catalog_version",
    "catalog_sha256",
    "builder_code_sha256",
    "generator_code_sha256",
    "controller_code_sha256",
    "generation_out_root_sha256",
    "provider",
    "model_manifest_sha256",
    "model_runtime_attestation_sha256",
    "model_endpoint_identity_sha256",
    "model_requested_name",
    "candidate_buffer_factor",
    "oversample_factor",
    "max_quality_retries",
    "shards",
)
_USE_SPLITS = {
    "evaluation": "frozen_proxy_eval_only",
    "training": "train_pool_only",
}
_SUCCESSFUL_JUDGE_STATUSES = frozenset(
    {
        "completed",
        "recovered_completed",
        "skipped_verified",
        "skipped_recovery_verified",
    }
)


class ProxyJudgingShardControllerError(ValueError):
    """The judging controller invocation or an artifact is invalid."""


@dataclass(frozen=True)
class JudgeShardSpec:
    index: int
    generation_run_id: str
    judge_run_id: str
    input_path: Path
    upstream_generation: dict[str, object]

    def contract_payload(self) -> dict[str, object]:
        return {
            "index": self.index,
            "generation_run_id": self.generation_run_id,
            "judge_run_id": self.judge_run_id,
            "input_path_sha256": _path_digest(self.input_path),
            "generation_run_contract_sha256": self.upstream_generation[
                "generation_run_contract_sha256"
            ],
            "generation_attestation_sha256": self.upstream_generation[
                "attestation_sha256"
            ],
            "candidates_sha256": self.upstream_generation["candidates_sha256"],
            "input_count": self.upstream_generation["input_count"],
            "candidate_by_grade": self.upstream_generation["candidate_by_grade"],
            "candidate_by_factor_profile": self.upstream_generation[
                "candidate_by_factor_profile"
            ],
            "selection_target_by_factor_profile": self.upstream_generation[
                "selection_target_by_factor_profile"
            ],
            "base_final_target_by_factor_profile": self.upstream_generation[
                "base_final_target_by_factor_profile"
            ],
            "factor_profile_by_scenario": self.upstream_generation[
                "factor_profile_by_scenario"
            ],
        }


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


def _normalized_count_map(value: object, *, field: str) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise ProxyJudgingShardControllerError(f"{field} must be a count map")
    normalized = {
        str(key): _strict_nonnegative_int(count, field=f"{field}:{key}")
        for key, count in value.items()
    }
    if "" in normalized:
        raise ProxyJudgingShardControllerError(f"{field} contains an empty key")
    return dict(sorted(normalized.items()))


def _sum_count_maps(values: Sequence[Mapping[str, object]]) -> dict[str, int]:
    total: Counter[str] = Counter()
    for value in values:
        total.update(
            {
                str(key): _strict_nonnegative_int(count, field=f"count:{key}")
                for key, count in value.items()
            }
        )
    return dict(sorted(total.items()))


def _path_digest(path: Path) -> str:
    return _sha256_bytes(str(Path(os.path.abspath(path))).encode("utf-8"))


def _is_linklike(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction()) if callable(is_junction) else False


def _checked_path(
    path: Path,
    *,
    purpose: str,
    require_directory: bool = False,
) -> Path:
    """Normalize a path without silently traversing a symlink component."""
    absolute = Path(os.path.abspath(path))
    current = absolute
    while True:
        if _is_linklike(current):
            raise ProxyJudgingShardControllerError(
                f"{purpose} must not traverse a symlink or junction: {current}"
            )
        parent = current.parent
        if parent == current:
            break
        current = parent
    if require_directory and not absolute.is_dir():
        raise ProxyJudgingShardControllerError(
            f"{purpose} is missing or not a directory: {absolute}"
        )
    resolved = absolute.resolve()
    if resolved != absolute:
        raise ProxyJudgingShardControllerError(
            f"{purpose} resolves outside its lexical path: {absolute} -> {resolved}"
        )
    return absolute


def _subprocess_environment() -> dict[str, str]:
    """Inherit runtime essentials while blocking Python import/startup injection."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in _BLOCKED_PYTHON_ENV
    }
    environment.update(
        {
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return environment


def _atomic_write_bytes(path: Path, payload: bytes, *, replace: bool) -> None:
    if path.exists() and not replace:
        raise ProxyJudgingShardControllerError(
            f"refusing to overwrite controller artifact: {path}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, _ARTIFACT_FILE_MODE)
        if path.exists() and not replace:
            raise ProxyJudgingShardControllerError(
                f"refusing to overwrite controller artifact: {path}"
            )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _mkdir_artifact(path: Path, *, parents: bool = False) -> None:
    """Create a controller-owned directory with the shared-operator mode."""
    path.mkdir(mode=_ARTIFACT_DIRECTORY_MODE, parents=parents, exist_ok=False)
    # mkdir's mode is filtered by umask and Windows ignores setgid.  chmod is
    # intentional so the Linux runtime contract is deterministic.
    os.chmod(path, _ARTIFACT_DIRECTORY_MODE)


def _atomic_write_json(path: Path, value: object, *, replace: bool) -> bytes:
    payload = _json_bytes(value)
    _atomic_write_bytes(path, payload, replace=replace)
    return payload


def _require_regular_file(path: Path, *, purpose: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ProxyJudgingShardControllerError(
            f"missing or non-regular {purpose}: {path}"
        )


def _read_json_object(path: Path, *, purpose: str) -> dict[str, object]:
    _require_regular_file(path, purpose=purpose)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProxyJudgingShardControllerError(f"invalid {purpose}: {path}") from exc
    if not isinstance(value, dict):
        raise ProxyJudgingShardControllerError(
            f"{purpose} must be a JSON object: {path}"
        )
    return value


def _strict_nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProxyJudgingShardControllerError(f"invalid nonnegative count: {field}")
    return value


def _read_jsonl_objects(path: Path, *, purpose: str) -> list[dict[str, object]]:
    _require_regular_file(path, purpose=purpose)
    rows: list[dict[str, object]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ProxyJudgingShardControllerError(
                        f"{purpose} row {line_no} must be a JSON object"
                    )
                rows.append(value)
    except json.JSONDecodeError as exc:
        raise ProxyJudgingShardControllerError(
            f"invalid {purpose} JSONL at line {exc.lineno}"
        ) from exc
    except OSError as exc:
        raise ProxyJudgingShardControllerError(
            f"cannot read {purpose}: {path}"
        ) from exc
    return rows


def _factor_text(value: float) -> str:
    return format(Decimal(str(value)).normalize(), "f")


def _validate_common_arguments(
    *,
    run_prefix: str,
    intended_use: str,
    base_url: str,
    judge_model: str,
    judge_model_manifest_sha256: str,
    shadow_model: str | None,
    shadow_model_manifest_sha256: str | None,
    k_min: int,
    k_max: int,
    temperature: float,
    min_self_consistency: float,
) -> str:
    if not _RUN_PREFIX_RE.fullmatch(run_prefix):
        raise ProxyJudgingShardControllerError(
            "run_prefix must be 1-92 safe filename characters"
        )
    if intended_use not in _USE_SPLITS:
        raise ProxyJudgingShardControllerError(
            "intended_use must be either 'evaluation' or 'training'"
        )
    if base_url != base_url.strip() or any(
        ord(character) < 0x20 or ord(character) == 0x7F for character in base_url
    ):
        raise ProxyJudgingShardControllerError(
            "base_url must not contain surrounding whitespace or control characters"
        )
    normalized_url = base_url.rstrip("/")
    try:
        parsed = urlsplit(normalized_url)
        _ = parsed.port
    except ValueError as exc:
        raise ProxyJudgingShardControllerError("base_url is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProxyJudgingShardControllerError(
            "base_url must be an HTTP(S) endpoint without credentials, query, or fragment"
        )
    if not _MODEL_ID_RE.fullmatch(str(judge_model)):
        raise ProxyJudgingShardControllerError(
            "judge_model contains unsafe or unsupported characters"
        )
    try:
        _identity("local_openai", judge_model, context="primary judge")
    except ProxyJudgeContractError as exc:
        raise ProxyJudgingShardControllerError(str(exc)) from exc
    if not _SHA256_REVISION_RE.fullmatch(judge_model_manifest_sha256):
        raise ProxyJudgingShardControllerError(
            "judge_model_manifest_sha256 must be sha256:<64 lowercase hex>"
        )
    if shadow_model is None:
        if shadow_model_manifest_sha256 is not None:
            raise ProxyJudgingShardControllerError(
                "shadow model revision cannot be supplied with --no-shadow"
            )
    else:
        if not _MODEL_ID_RE.fullmatch(str(shadow_model)):
            raise ProxyJudgingShardControllerError(
                "shadow_model contains unsafe or unsupported characters"
            )
        try:
            _identity("local_openai", shadow_model, context="shadow judge")
        except ProxyJudgeContractError as exc:
            raise ProxyJudgingShardControllerError(str(exc)) from exc
        if not _SHA256_REVISION_RE.fullmatch(str(shadow_model_manifest_sha256 or "")):
            raise ProxyJudgingShardControllerError(
                "shadow_model_manifest_sha256 is required and must be pinned"
            )
        if _canonical_model_id(shadow_model) == _canonical_model_id(judge_model):
            raise ProxyJudgingShardControllerError(
                "primary and shadow judge must be independent models"
            )
    if isinstance(k_min, bool) or isinstance(k_max, bool) or k_min < 1 or k_max < k_min:
        raise ProxyJudgingShardControllerError("require 1 <= k_min <= k_max")
    if (
        isinstance(temperature, bool)
        or not math.isfinite(temperature)
        or temperature < 0.0
    ):
        raise ProxyJudgingShardControllerError(
            "temperature must be a finite nonnegative number"
        )
    if (
        isinstance(min_self_consistency, bool)
        or not math.isfinite(min_self_consistency)
        or not 0.0 <= min_self_consistency <= 1.0
    ):
        raise ProxyJudgingShardControllerError(
            "min_self_consistency must be between 0 and 1"
        )
    return normalized_url


def _preflight_judge_runtime_models(
    *,
    base_url: str,
    judge_model: str,
    judge_model_manifest_sha256: str,
    shadow_model: str | None,
    shadow_model_manifest_sha256: str | None,
    live: bool,
) -> dict[str, dict[str, object] | None]:
    def attest(model: str, digest: str) -> dict[str, object]:
        try:
            return (
                verify_ollama_model(
                    base_url=base_url,
                    requested_model=model,
                    expected_manifest_sha256=digest,
                )
                if live
                else pending_ollama_model_attestation(
                    base_url=base_url,
                    requested_model=model,
                    expected_manifest_sha256=digest,
                )
            )
        except OllamaAttestationError as exc:
            raise ProxyJudgingShardControllerError(
                f"judge runtime model preflight failed: {exc}"
            ) from exc

    primary = attest(judge_model, judge_model_manifest_sha256)
    shadow = (
        attest(shadow_model, str(shadow_model_manifest_sha256))
        if shadow_model is not None
        else None
    )
    return {"primary": primary, "shadow": shadow}


def _attest_generation_controller(
    controller_dir: Path,
    *,
    generation_out_root: Path,
    intended_use: str,
    runtime_base_url: str | None = None,
    revalidate_runtime: bool = False,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Revalidate a committed generation controller and all ten inputs."""
    controller_dir = _checked_path(
        controller_dir,
        purpose="generation controller",
        require_directory=True,
    )
    generation_out_root = _checked_path(
        generation_out_root,
        purpose="generation output root",
        require_directory=True,
    )
    artifacts = {
        "manifest": controller_dir / "manifest.json",
        "stats": controller_dir / "stats.json",
        "progress": controller_dir / "progress.json",
        "complete": controller_dir / "COMPLETE.json",
    }
    manifest = _read_json_object(
        artifacts["manifest"], purpose="generation controller manifest"
    )
    stats = _read_json_object(artifacts["stats"], purpose="generation controller stats")
    progress = _read_json_object(
        artifacts["progress"], purpose="generation controller progress"
    )
    complete = _read_json_object(
        artifacts["complete"], purpose="generation controller COMPLETE"
    )
    actual_hashes = {name: _sha256_file(path) for name, path in artifacts.items()}

    if any(
        row.get("schema_version") != GENERATION_CONTROLLER_SCHEMA_VERSION
        for row in (manifest, stats, progress, complete)
    ):
        raise ProxyJudgingShardControllerError(
            "unsupported generation controller schema"
        )
    exit_code = complete.get("exit_code")
    if (
        manifest.get("status") != "complete"
        or stats.get("status") != "complete"
        or progress.get("status") != "complete"
        or manifest.get("target_met") is not True
        or stats.get("target_met") is not True
        or complete.get("target_met") is not True
        or isinstance(exit_code, bool)
        or exit_code != 0
    ):
        raise ProxyJudgingShardControllerError(
            "generation controller is not a successful committed run"
        )
    run_prefix = str(manifest.get("run_prefix") or "")
    run_contract_sha256 = str(manifest.get("run_contract_sha256") or "")
    if (
        not _RUN_PREFIX_RE.fullmatch(run_prefix)
        or controller_dir.name != run_prefix
        or not _SHA256_RE.fullmatch(run_contract_sha256)
    ):
        raise ProxyJudgingShardControllerError(
            "generation controller identity or contract digest is invalid"
        )
    if any(
        row.get("run_prefix") != run_prefix
        or row.get("run_contract_sha256") != run_contract_sha256
        for row in (stats, progress, complete)
    ):
        raise ProxyJudgingShardControllerError(
            "generation controller identity/contract fields disagree"
        )
    if (
        manifest.get("intended_use") != intended_use
        or stats.get("intended_use") != intended_use
    ):
        raise ProxyJudgingShardControllerError(
            "generation controller intended_use mismatch"
        )
    expected_split = _USE_SPLITS[intended_use]
    if (
        manifest.get("catalog_split_role") != expected_split
        or stats.get("catalog_split_role") != expected_split
    ):
        raise ProxyJudgingShardControllerError(
            "generation controller catalog split mismatch"
        )
    if (
        manifest.get("shard_count") != SHARD_COUNT
        or stats.get("shard_count") != SHARD_COUNT
        or stats.get("attempted_shards") != SHARD_COUNT
        or stats.get("successful_shards") != SHARD_COUNT
        or stats.get("failed_shards") != 0
        or progress.get("successful_shards") != SHARD_COUNT
        or progress.get("failed_shards") != 0
    ):
        raise ProxyJudgingShardControllerError(
            "generation controller does not attest exactly ten successful shards"
        )
    if manifest.get("generation_out_root_sha256") != _path_digest(generation_out_root):
        raise ProxyJudgingShardControllerError(
            "generation output root does not match controller contract"
        )

    recorded_generation_model_attestations = {
        "manifest": manifest.get("runtime_model_attestation"),
        "COMPLETE": complete.get("runtime_model_attestation"),
    }
    validated_generation_model_attestations: dict[
        str, dict[str, object]
    ] = {}
    for context, value in recorded_generation_model_attestations.items():
        try:
            validated_generation_model_attestations[context] = (
                validate_ollama_attestation(value, require_verified=True)
            )
        except OllamaAttestationError as exc:
            raise ProxyJudgingShardControllerError(
                f"generation controller {context} runtime model attestation is invalid"
            ) from exc
    generation_model_attestation_binding = str(
        validated_generation_model_attestations["manifest"]["binding_sha256"]
    )
    if any(
        value["binding_sha256"] != generation_model_attestation_binding
        for value in validated_generation_model_attestations.values()
    ):
        raise ProxyJudgingShardControllerError(
            "generation controller runtime model attestation bindings disagree"
        )
    if (
        manifest.get("model_runtime_attestation_sha256")
        != generation_model_attestation_binding
        or stats.get("model_runtime_attestation_sha256")
        != generation_model_attestation_binding
        or complete.get("model_runtime_attestation_sha256")
        != generation_model_attestation_binding
        or validated_generation_model_attestations["manifest"].get(
            "endpoint_identity_sha256"
        )
        != manifest.get("model_endpoint_identity_sha256")
        or validated_generation_model_attestations["manifest"].get(
            "requested_model"
        )
        != manifest.get("model_requested_name")
        or validated_generation_model_attestations["manifest"].get(
            "live_model_digest"
        )
        != manifest.get("model_manifest_sha256")
    ):
        raise ProxyJudgingShardControllerError(
            "generation controller runtime model attestation envelope mismatch"
        )
    runtime_revalidation: dict[str, object] | None = None
    if revalidate_runtime and runtime_base_url is None:
        raise ProxyJudgingShardControllerError(
            "generation runtime revalidation requires an Ollama base URL"
        )
    if runtime_base_url is not None:
        try:
            current_generation_model_attestation = (
                verify_ollama_model(
                    base_url=runtime_base_url,
                    requested_model=str(manifest["model_requested_name"]),
                    expected_manifest_sha256=str(
                        manifest["model_manifest_sha256"]
                    ),
                )
                if revalidate_runtime
                else pending_ollama_model_attestation(
                    base_url=runtime_base_url,
                    requested_model=str(manifest["model_requested_name"]),
                    expected_manifest_sha256=str(
                        manifest["model_manifest_sha256"]
                    ),
                )
            )
        except OllamaAttestationError as exc:
            raise ProxyJudgingShardControllerError(
                f"generation runtime model live revalidation failed: {exc}"
            ) from exc
        if revalidate_runtime and (
            current_generation_model_attestation.get("binding_sha256")
            != generation_model_attestation_binding
            or current_generation_model_attestation.get(
                "endpoint_identity_sha256"
            )
            != manifest.get("model_endpoint_identity_sha256")
        ):
            raise ProxyJudgingShardControllerError(
                "generation runtime model live binding changed"
            )
        runtime_revalidation = {
            "status": current_generation_model_attestation["status"],
            "binding_sha256": current_generation_model_attestation[
                "binding_sha256"
            ],
            "endpoint_identity_sha256": current_generation_model_attestation[
                "endpoint_identity_sha256"
            ],
            "requested_model": current_generation_model_attestation[
                "requested_model"
            ],
            "live_model_digest": current_generation_model_attestation[
                "live_model_digest"
            ],
            "expected_model_digest": current_generation_model_attestation[
                "expected_model_digest"
            ],
        }

    missing_contract = [
        field for field in _GENERATION_CONTRACT_FIELDS if field not in manifest
    ]
    if missing_contract:
        raise ProxyJudgingShardControllerError(
            "generation controller contract fields are missing: "
            + ",".join(missing_contract)
        )
    contract_material = {
        field: manifest[field] for field in _GENERATION_CONTRACT_FIELDS
    }
    if _sha256_bytes(_canonical_json_bytes(contract_material)) != run_contract_sha256:
        raise ProxyJudgingShardControllerError(
            "generation controller run contract digest mismatch"
        )
    if manifest.get("stats") != stats:
        raise ProxyJudgingShardControllerError(
            "generation controller embedded stats mismatch"
        )
    final_artifacts = manifest.get("final_artifacts")
    if not isinstance(final_artifacts, Mapping):
        raise ProxyJudgingShardControllerError(
            "generation controller final_artifacts are missing"
        )
    expected_final = {
        "stats": "stats.json",
        "stats_sha256": actual_hashes["stats"],
        "progress": "progress.json",
        "progress_sha256": actual_hashes["progress"],
    }
    if any(final_artifacts.get(key) != value for key, value in expected_final.items()):
        raise ProxyJudgingShardControllerError(
            "generation controller manifest artifact hashes mismatch"
        )
    expected_complete = {
        "manifest_sha256": actual_hashes["manifest"],
        "stats_sha256": actual_hashes["stats"],
        "progress_sha256": actual_hashes["progress"],
    }
    if any(complete.get(key) != value for key, value in expected_complete.items()):
        raise ProxyJudgingShardControllerError(
            "generation controller COMPLETE artifact hashes mismatch"
        )

    manifest_shards = manifest.get("shards")
    stats_results = stats.get("results")
    progress_results = progress.get("results")
    if (
        not isinstance(manifest_shards, list)
        or len(manifest_shards) != SHARD_COUNT
        or not isinstance(stats_results, list)
        or len(stats_results) != SHARD_COUNT
        or progress_results != stats_results
        or progress.get("completed_shard_indices") != list(range(SHARD_COUNT))
    ):
        raise ProxyJudgingShardControllerError(
            "generation controller shard lists are incomplete or inconsistent"
        )
    by_index: dict[int, Mapping[str, object]] = {}
    for result in stats_results:
        if not isinstance(result, Mapping):
            raise ProxyJudgingShardControllerError(
                "generation controller result must be an object"
            )
        index = result.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index in by_index:
            raise ProxyJudgingShardControllerError(
                "generation controller result indices are invalid"
            )
        by_index[index] = result
    if sorted(by_index) != list(range(SHARD_COUNT)):
        raise ProxyJudgingShardControllerError(
            "generation controller result indices are incomplete"
        )

    shard_attestations: list[dict[str, object]] = []
    total_input_count = 0
    planned_profile_maps: list[dict[str, int]] = []
    base_profile_maps: list[dict[str, int]] = []
    candidate_profile_maps: list[dict[str, int]] = []
    for index, shard_contract in enumerate(manifest_shards):
        if not isinstance(shard_contract, Mapping):
            raise ProxyJudgingShardControllerError(
                f"generation shard contract {index} must be an object"
            )
        generation_run_id = f"{run_prefix}-s{index:02d}"
        generation_namespace = str(
            shard_contract.get("generation_namespace") or ""
        )
        if (
            shard_contract.get("index") != index
            or shard_contract.get("run_id") != generation_run_id
            or generation_namespace != generation_run_id
        ):
            raise ProxyJudgingShardControllerError(
                f"generation shard contract {index} identity mismatch"
            )
        result = by_index[index]
        expected_shard_dir = _checked_path(
            generation_out_root / generation_run_id,
            purpose=f"generation shard {index} directory",
            require_directory=True,
        )
        recorded_value = str(result.get("shard_dir") or "")
        recorded_path = Path(recorded_value)
        if not recorded_path.is_absolute():
            raise ProxyJudgingShardControllerError(
                f"generation shard {index} recorded path is not absolute"
            )
        recorded_shard_dir = Path(os.path.abspath(recorded_path))
        if (
            result.get("run_id") != generation_run_id
            or result.get("status")
            not in {"completed", "resumed_completed", "skipped_verified"}
            or recorded_shard_dir != expected_shard_dir
            or result.get("target_met") is not True
        ):
            raise ProxyJudgingShardControllerError(
                f"generation shard {index} controller result mismatch"
            )
        input_path = expected_shard_dir / "candidates.jsonl"
        try:
            attestation = attest_generation_input(input_path, intended_use=intended_use)
        except ProxyJudgeContractError as exc:
            raise ProxyJudgingShardControllerError(
                f"generation shard {index} input attestation failed: {exc}"
            ) from exc
        planned_by_profile = _normalized_count_map(
            shard_contract.get("planned_by_factor_profile"),
            field=f"generation shard {index} planned_by_factor_profile",
        )
        selection_by_profile = _normalized_count_map(
            shard_contract.get("selection_targets_by_factor_profile"),
            field=f"generation shard {index} selection_targets_by_factor_profile",
        )
        base_by_profile = _normalized_count_map(
            shard_contract.get("base_final_targets_by_factor_profile"),
            field=f"generation shard {index} base_final_targets_by_factor_profile",
        )
        factor_profile_by_scenario = shard_contract.get(
            "factor_profile_by_scenario"
        )
        if not isinstance(factor_profile_by_scenario, Mapping) or any(
            not str(scenario_id).strip() or not str(profile).strip()
            for scenario_id, profile in factor_profile_by_scenario.items()
        ):
            raise ProxyJudgingShardControllerError(
                f"generation shard {index} factor-profile scenario map is invalid"
            )
        normalized_factor_profile_by_scenario = dict(
            sorted(
                (str(scenario_id), str(profile))
                for scenario_id, profile in factor_profile_by_scenario.items()
            )
        )
        profile_universe = set(base_by_profile)
        if (
            len(profile_universe) != 21
            or set(planned_by_profile) != profile_universe
            or set(selection_by_profile) != profile_universe
            or set(normalized_factor_profile_by_scenario.values())
            != profile_universe
            or sum(planned_by_profile.values())
            != _strict_nonnegative_int(
                shard_contract.get("planned"),
                field=f"generation shard {index} planned",
            )
            or sum(selection_by_profile.values()) != int(attestation["input_count"])
        ):
            raise ProxyJudgingShardControllerError(
                f"generation shard {index} factor-profile contract is incomplete"
            )
        expected_attested_profiles = {
            "candidate_by_factor_profile": selection_by_profile,
            "selection_target_by_factor_profile": selection_by_profile,
            "base_final_target_by_factor_profile": base_by_profile,
            "factor_profile_by_scenario": normalized_factor_profile_by_scenario,
        }
        profile_mismatches = [
            field
            for field, expected in expected_attested_profiles.items()
            if attestation.get(field) != expected
        ]
        if profile_mismatches:
            raise ProxyJudgingShardControllerError(
                f"generation shard {index} factor-profile attestation mismatch: "
                + ",".join(profile_mismatches)
            )
        if attestation.get("generation_namespace") != generation_namespace:
            raise ProxyJudgingShardControllerError(
                f"generation shard {index} namespace attestation mismatch"
            )
        expected_result_fields = {
            "generation_run_contract_sha256": attestation[
                "generation_run_contract_sha256"
            ],
            "candidates_sha256": attestation["candidates_sha256"],
            "rejected_sha256": attestation["rejected_sha256"],
            "stats_sha256": attestation["stats_sha256"],
            "candidate_count": attestation["input_count"],
            "generation_namespace": generation_namespace,
            "candidate_by_factor_profile": attestation[
                "candidate_by_factor_profile"
            ],
            "rejected_count": attestation["rejected_count"],
            "model_runtime_attestation_sha256": (
                generation_model_attestation_binding
            ),
        }
        if (
            attestation.get("generation_model_attestation_binding_sha256")
            != generation_model_attestation_binding
        ):
            raise ProxyJudgingShardControllerError(
                f"generation shard {index} runtime model attestation mismatch"
            )
        mismatches = [
            field
            for field, expected in expected_result_fields.items()
            if result.get(field) != expected
        ]
        if mismatches:
            raise ProxyJudgingShardControllerError(
                f"generation shard {index} differs from committed controller result: "
                + ",".join(mismatches)
            )
        if attestation.get("generation_run_id") != generation_run_id:
            raise ProxyJudgingShardControllerError(
                f"generation shard {index} run id mismatch"
            )
        total_input_count += int(attestation["input_count"])
        planned_profile_maps.append(planned_by_profile)
        base_profile_maps.append(base_by_profile)
        candidate_profile_maps.append(selection_by_profile)
        shard_attestations.append(
            {
                "index": index,
                "generation_run_id": generation_run_id,
                "input_path": str(input_path),
                "upstream_generation": attestation,
            }
        )
    if stats.get("verified_candidate_count") != total_input_count:
        raise ProxyJudgingShardControllerError(
            "generation controller verified candidate total mismatch"
        )
    planned_by_factor_profile = _sum_count_maps(planned_profile_maps)
    base_by_factor_profile = _sum_count_maps(base_profile_maps)
    candidate_by_factor_profile = _sum_count_maps(candidate_profile_maps)
    expected_controller_profile_stats = {
        "planned_generation_attempts_by_factor_profile": (
            planned_by_factor_profile
        ),
        "base_final_target_by_factor_profile": base_by_factor_profile,
        "prejudge_candidate_target_by_factor_profile": (
            candidate_by_factor_profile
        ),
        "verified_candidate_by_factor_profile": candidate_by_factor_profile,
    }
    if any(
        stats.get(field) != expected
        for field, expected in expected_controller_profile_stats.items()
    ):
        raise ProxyJudgingShardControllerError(
            "generation controller factor-profile stats mismatch"
        )
    if (
        len(base_by_factor_profile) != 21
        or sum(base_by_factor_profile.values()) != int(manifest["expected_base_total"])
        or sum(candidate_by_factor_profile.values()) != total_input_count
    ):
        raise ProxyJudgingShardControllerError(
            "generation controller factor-profile totals are invalid"
        )

    attestation: dict[str, object] = {
        "schema": "proxy-generation-controller-input-attestation-v2",
        "status": "verified",
        "generation_controller_run_prefix": run_prefix,
        "generation_controller_run_contract_sha256": run_contract_sha256,
        "generation_controller_dir_sha256": _path_digest(controller_dir),
        "generation_out_root_sha256": _path_digest(generation_out_root),
        "manifest_sha256": actual_hashes["manifest"],
        "stats_sha256": actual_hashes["stats"],
        "progress_sha256": actual_hashes["progress"],
        "complete_sha256": actual_hashes["complete"],
        "intended_use": intended_use,
        "catalog_split_role": expected_split,
        "shard_count": SHARD_COUNT,
        "input_count": total_input_count,
        "planned_by_factor_profile": planned_by_factor_profile,
        "base_final_target_by_factor_profile": base_by_factor_profile,
        "candidate_by_factor_profile": candidate_by_factor_profile,
        "generation_model_attestation": dict(
            validated_generation_model_attestations["COMPLETE"]
        ),
        "generation_model_attestation_binding_sha256": (
            generation_model_attestation_binding
        ),
        "generation_model_runtime_revalidation": runtime_revalidation,
        "shards": [
            {
                "index": row["index"],
                "generation_run_id": row["generation_run_id"],
                "generation_namespace": row["upstream_generation"][
                    "generation_namespace"
                ],
                "generation_attestation_sha256": row["upstream_generation"][
                    "attestation_sha256"
                ],
                "generation_run_contract_sha256": row["upstream_generation"][
                    "generation_run_contract_sha256"
                ],
                "candidates_sha256": row["upstream_generation"]["candidates_sha256"],
                "input_count": row["upstream_generation"]["input_count"],
                "candidate_by_factor_profile": row["upstream_generation"][
                    "candidate_by_factor_profile"
                ],
                "base_final_target_by_factor_profile": row[
                    "upstream_generation"
                ]["base_final_target_by_factor_profile"],
            }
            for row in shard_attestations
        ],
    }
    attestation["attestation_sha256"] = _sha256_bytes(
        _canonical_json_bytes(attestation)
    )
    return attestation, shard_attestations


def _build_specs(
    *, run_prefix: str, shard_attestations: Sequence[Mapping[str, object]]
) -> list[JudgeShardSpec]:
    specs: list[JudgeShardSpec] = []
    if len(shard_attestations) != SHARD_COUNT:
        raise ProxyJudgingShardControllerError(
            "generation controller must expose exactly ten shard attestations"
        )
    for expected_index, row in enumerate(shard_attestations):
        if row.get("index") != expected_index:
            raise ProxyJudgingShardControllerError(
                "generation shard attestation order mismatch"
            )
        upstream = row.get("upstream_generation")
        if not isinstance(upstream, dict):
            raise ProxyJudgingShardControllerError(
                f"generation shard {expected_index} attestation is missing"
            )
        profile_maps = [
            _normalized_count_map(
                upstream.get(field),
                field=f"generation shard {expected_index} {field}",
            )
            for field in (
                "candidate_by_factor_profile",
                "selection_target_by_factor_profile",
                "base_final_target_by_factor_profile",
            )
        ]
        factor_profile_by_scenario = upstream.get("factor_profile_by_scenario")
        if (
            any(len(values) != 21 for values in profile_maps)
            or any(set(values) != set(profile_maps[0]) for values in profile_maps[1:])
            or not isinstance(factor_profile_by_scenario, Mapping)
            or set(str(value) for value in factor_profile_by_scenario.values())
            != set(profile_maps[0])
        ):
            raise ProxyJudgingShardControllerError(
                f"generation shard {expected_index} factor-profile contract is invalid"
            )
        specs.append(
            JudgeShardSpec(
                index=expected_index,
                generation_run_id=str(row["generation_run_id"]),
                judge_run_id=f"{run_prefix}-s{expected_index:02d}",
                input_path=Path(os.path.abspath(str(row["input_path"]))),
                upstream_generation=upstream,
            )
        )
    return specs


def _validate_generator_independence(
    specs: Sequence[JudgeShardSpec], *, judge_model: str
) -> None:
    for spec in specs:
        generator = spec.upstream_generation.get("generation_provider")
        if not isinstance(generator, Mapping):
            raise ProxyJudgingShardControllerError(
                f"shard {spec.index} generator identity is missing"
            )
        try:
            identity = _identity(
                generator.get("runtime"),
                generator.get("model"),
                context=f"shard {spec.index} generator",
            )
        except ProxyJudgeContractError as exc:
            raise ProxyJudgingShardControllerError(str(exc)) from exc
        if identity.canonical_model == _canonical_model_id(judge_model):
            raise ProxyJudgingShardControllerError(
                f"shard {spec.index} generator and primary judge are not independent"
            )


def _controller_contract(
    *,
    run_prefix: str,
    intended_use: str,
    generation_controller_attestation: Mapping[str, object],
    generation_controller_dir: Path,
    generation_out_root: Path,
    judging_out_root: Path,
    controller_out_root: Path,
    base_url: str,
    judge_model: str,
    judge_model_manifest_sha256: str,
    shadow_model: str | None,
    shadow_model_manifest_sha256: str | None,
    k_min: int,
    k_max: int,
    temperature: float,
    min_self_consistency: float,
    require_evidence: bool,
    runtime_model_attestations: Mapping[str, object],
    specs: Sequence[JudgeShardSpec],
) -> dict[str, object]:
    material: dict[str, object] = {
        "schema_version": CONTROLLER_SCHEMA_VERSION,
        "run_prefix": run_prefix,
        "intended_use": intended_use,
        "catalog_split_role": _USE_SPLITS[intended_use],
        "shard_count": SHARD_COUNT,
        "concurrency": 1,
        "subprocess_python_isolated": True,
        "subprocess_environment_policy": "block-python-injection-v1",
        "generation_controller_attestation": dict(generation_controller_attestation),
        "generation_controller_dir_sha256": _path_digest(generation_controller_dir),
        "generation_out_root_sha256": _path_digest(generation_out_root),
        "judging_out_root_sha256": _path_digest(judging_out_root),
        "controller_out_root_sha256": _path_digest(controller_out_root),
        "judge_code_sha256": _sha256_file(_HERE / "judge_proxy_candidates.py"),
        "controller_code_sha256": _sha256_file(Path(__file__)),
        "base_url": base_url,
        "judge_model": judge_model,
        "judge_model_manifest_sha256": judge_model_manifest_sha256,
        "shadow_model": shadow_model,
        "shadow_model_manifest_sha256": shadow_model_manifest_sha256,
        "k_min": k_min,
        "k_max": k_max,
        "temperature": _factor_text(temperature),
        "min_self_consistency": _factor_text(min_self_consistency),
        "require_evidence": require_evidence,
        "primary_judge_runtime_attestation_sha256": (
            runtime_model_attestations["primary"]["binding_sha256"]
        ),
        "shadow_judge_runtime_attestation_sha256": (
            runtime_model_attestations["shadow"]["binding_sha256"]
            if runtime_model_attestations.get("shadow") is not None
            else None
        ),
        "allow_unattested_legacy_input": False,
        "shards": [spec.contract_payload() for spec in specs],
    }
    return {
        **material,
        "run_contract_sha256": _sha256_bytes(_canonical_json_bytes(material)),
    }


def _subprocess_command(
    *,
    spec: JudgeShardSpec,
    judging_out_root: Path,
    intended_use: str,
    base_url: str,
    judge_model: str,
    judge_model_manifest_sha256: str,
    shadow_model: str | None,
    shadow_model_manifest_sha256: str | None,
    k_min: int,
    k_max: int,
    temperature: float,
    min_self_consistency: float,
    require_evidence: bool,
) -> list[str]:
    command = [
        sys.executable,
        "-I",
        str(_HERE / "judge_proxy_candidates.py"),
        "--input",
        str(spec.input_path),
        "--out-root",
        str(judging_out_root),
        "--run-id",
        spec.judge_run_id,
        "--intended-use",
        intended_use,
        "--base-url",
        base_url,
        "--judge-model",
        judge_model,
        "--judge-model-manifest-sha256",
        judge_model_manifest_sha256,
        "--k-min",
        str(k_min),
        "--k-max",
        str(k_max),
        "--temperature",
        _factor_text(temperature),
        "--min-self-consistency",
        _factor_text(min_self_consistency),
    ]
    if shadow_model is None:
        command.append("--no-shadow")
    else:
        command.extend(
            [
                "--shadow-model",
                shadow_model,
                "--shadow-model-manifest-sha256",
                str(shadow_model_manifest_sha256),
            ]
        )
    if require_evidence:
        command.append("--require-evidence")
    return command


def _recovery_judge_run_id(
    *, spec: JudgeShardSpec, run_contract_sha256: str, recovery_index: int
) -> str:
    if recovery_index < 1:
        raise ProxyJudgingShardControllerError(
            "judge recovery index must be positive"
        )
    material = {
        "generation_run_id": spec.generation_run_id,
        "contract_judge_run_id": spec.judge_run_id,
        "run_contract_sha256": run_contract_sha256,
        "recovery_index": recovery_index,
    }
    suffix = _sha256_bytes(_canonical_json_bytes(material))[:24]
    return f"judge-recovery-{suffix}-s{spec.index:02d}"


def _reverify_runtime_model_attestation(
    *,
    manifest: Mapping[str, object],
    complete: Mapping[str, object],
    field: str,
    model: object,
    expected_digest: object,
    base_url: object,
    context: str,
) -> dict[str, object] | None:
    recorded = manifest.get(field)
    if complete.get(field) != recorded:
        raise ProxyJudgingShardControllerError(
            f"{context} runtime model attestation envelope mismatch"
        )
    if model is None:
        if recorded is not None:
            raise ProxyJudgingShardControllerError(
                f"{context} runtime model attestation exists without a model"
            )
        return None
    if not isinstance(recorded, Mapping):
        raise ProxyJudgingShardControllerError(
            f"{context} runtime model attestation is missing"
        )
    try:
        validated_recorded = validate_ollama_attestation(recorded)
    except OllamaAttestationError as exc:
        raise ProxyJudgingShardControllerError(
            f"{context} recorded runtime model attestation is invalid: {exc}"
        ) from exc
    if (
        validated_recorded.get("requested_model") != model
        or validated_recorded.get("expected_model_digest") != expected_digest
        or validated_recorded.get("live_model_digest") != expected_digest
    ):
        raise ProxyJudgingShardControllerError(
            f"{context} recorded runtime model digest is invalid"
        )
    try:
        fresh = verify_ollama_model(
            base_url=base_url,
            requested_model=str(model),
            expected_manifest_sha256=str(expected_digest),
        )
    except (OSError, OllamaAttestationError) as exc:
        raise ProxyJudgingShardControllerError(
            f"{context} live runtime model revalidation failed: {exc}"
        ) from exc
    if fresh.get("binding_sha256") != validated_recorded.get("binding_sha256"):
        raise ProxyJudgingShardControllerError(
            f"{context} live runtime model binding changed"
        )
    return validated_recorded


def _verify_completed_judging_shard(
    shard_dir: Path,
    *,
    spec: JudgeShardSpec,
    contract: Mapping[str, object],
) -> dict[str, object]:
    shard_dir = _checked_path(
        shard_dir,
        purpose=f"judge shard {spec.index} directory",
        require_directory=True,
    )
    artifact_paths = {
        "manifest": shard_dir / "run_manifest.json",
        "gold": shard_dir / "gold_candidate.jsonl",
        "uncertain": shard_dir / "uncertain.jsonl",
        "journal": shard_dir / "decisions.journal.jsonl",
        "stats": shard_dir / "stats.json",
        "progress": shard_dir / "progress.json",
        "complete": shard_dir / "COMPLETE.json",
    }
    manifest = _read_json_object(
        artifact_paths["manifest"], purpose=f"judge shard {spec.index} manifest"
    )
    stats = _read_json_object(
        artifact_paths["stats"], purpose=f"judge shard {spec.index} stats"
    )
    progress = _read_json_object(
        artifact_paths["progress"], purpose=f"judge shard {spec.index} progress"
    )
    complete = _read_json_object(
        artifact_paths["complete"], purpose=f"judge shard {spec.index} COMPLETE"
    )
    for name in ("gold", "uncertain", "journal"):
        _require_regular_file(
            artifact_paths[name], purpose=f"judge shard {spec.index} {name}"
        )
    actual_hashes = {name: _sha256_file(path) for name, path in artifact_paths.items()}

    if (
        manifest.get("schema_version") != JUDGE_RUN_SCHEMA_VERSION
        or complete.get("schema_version") != JUDGE_RUN_SCHEMA_VERSION
        or manifest.get("status") != "complete"
        or manifest.get("run_id") != spec.judge_run_id
        or complete.get("run_id") != spec.judge_run_id
    ):
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} completion identity mismatch"
        )
    if manifest.get("stats") != stats:
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} embedded stats mismatch"
        )
    expected_paths = {
        "gold_candidate_path": "gold_candidate.jsonl",
        "uncertain_path": "uncertain.jsonl",
        "journal_path": "decisions.journal.jsonl",
        "stats_path": "stats.json",
    }
    if any(manifest.get(key) != value for key, value in expected_paths.items()):
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} manifest artifact path mismatch"
        )
    expected_complete_hashes = {
        "manifest_sha256": actual_hashes["manifest"],
        "gold_candidate_sha256": actual_hashes["gold"],
        "uncertain_sha256": actual_hashes["uncertain"],
        "stats_sha256": actual_hashes["stats"],
    }
    if any(
        complete.get(field) != expected
        for field, expected in expected_complete_hashes.items()
    ):
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} COMPLETE artifact hash mismatch"
        )

    checked_input_path = _checked_path(
        spec.input_path,
        purpose=f"judge shard {spec.index} generation input",
    )
    _require_regular_file(
        checked_input_path,
        purpose=f"judge shard {spec.index} generation input",
    )
    if checked_input_path != spec.input_path:
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} generation input path changed"
        )
    try:
        fresh_upstream = attest_generation_input(
            checked_input_path, intended_use=str(contract["intended_use"])
        )
    except ProxyJudgeContractError as exc:
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} upstream input re-attestation failed: {exc}"
        ) from exc
    if fresh_upstream != spec.upstream_generation:
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} upstream input changed"
        )
    if (
        manifest.get("upstream_generation") != fresh_upstream
        or complete.get("upstream_generation") != fresh_upstream
    ):
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} upstream attestation mismatch"
        )
    input_count = int(fresh_upstream["input_count"])
    input_sha256 = str(fresh_upstream["candidates_sha256"])
    if any(
        value != input_sha256
        for value in (
            manifest.get("input_sha256"),
            stats.get("input_sha256"),
            complete.get("input_sha256"),
        )
    ):
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} input hash mismatch"
        )
    if manifest.get("input_reference") != str(spec.input_path):
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} input reference mismatch"
        )
    intended_use = str(contract["intended_use"])
    expected_split = str(contract["catalog_split_role"])
    if any(
        row.get("intended_use") != intended_use
        or row.get("catalog_split_role") != expected_split
        for row in (manifest, stats, complete)
    ):
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} intended-use contract mismatch"
        )

    try:
        records = load_candidates(checked_input_path)
    except (OSError, ProxyJudgeContractError) as exc:
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} candidate reload failed: {exc}"
        ) from exc
    if _sha256_file(checked_input_path) != input_sha256:
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} input changed during verification"
        )
    shadow_model = contract.get("shadow_model")
    try:
        primary_identity, generator_identities, shadow_identity = (
            validate_model_contract(
                records,
                judge_model=str(contract["judge_model"]),
                shadow_model=(str(shadow_model) if shadow_model is not None else None),
            )
        )
    except ProxyJudgeContractError as exc:
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} model contract failed: {exc}"
        ) from exc
    if (
        manifest.get("primary_judge") != asdict(primary_identity)
        or manifest.get("primary_judge_model_revision")
        != contract["judge_model_manifest_sha256"]
        or manifest.get("generator_models")
        != [asdict(identity) for identity in generator_identities]
        or manifest.get("shadow_judge")
        != (asdict(shadow_identity) if shadow_identity is not None else None)
        or manifest.get("shadow_judge_model_revision")
        != contract["shadow_model_manifest_sha256"]
    ):
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} model identity/revision mismatch"
        )
    primary_runtime_attestation = _reverify_runtime_model_attestation(
        manifest=manifest,
        complete=complete,
        field="primary_judge_runtime_attestation",
        model=contract["judge_model"],
        expected_digest=contract["judge_model_manifest_sha256"],
        base_url=contract["base_url"],
        context=f"judge shard {spec.index} primary judge",
    )
    shadow_runtime_attestation = _reverify_runtime_model_attestation(
        manifest=manifest,
        complete=complete,
        field="shadow_judge_runtime_attestation",
        model=contract.get("shadow_model"),
        expected_digest=contract.get("shadow_model_manifest_sha256"),
        base_url=contract["base_url"],
        context=f"judge shard {spec.index} shadow judge",
    )
    if (
        manifest.get("gate_version") != PROXY_GATE_VERSION
        or manifest.get("claim_scope") != "synthetic_proxy_candidate_only"
        or manifest.get("human_reviewed") is not False
        or manifest.get("min_self_consistency")
        != float(str(contract["min_self_consistency"]))
        or manifest.get("legacy_require_rule_evidence_requested")
        is not contract["require_evidence"]
        or manifest.get("rule_source") != "static_keyword_seeds"
    ):
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} gate contract mismatch"
        )

    gold_rows = _read_jsonl_objects(
        artifact_paths["gold"], purpose=f"judge shard {spec.index} gold"
    )
    uncertain_rows = _read_jsonl_objects(
        artifact_paths["uncertain"], purpose=f"judge shard {spec.index} uncertain"
    )
    journal_rows = _read_jsonl_objects(
        artifact_paths["journal"], purpose=f"judge shard {spec.index} journal"
    )
    gold_count = len(gold_rows)
    uncertain_count = len(uncertain_rows)
    if gold_count + uncertain_count != input_count or len(journal_rows) != input_count:
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} output row counts do not match input"
        )
    source_by_id = {str(record.get("doc_id")): record for record in records}
    expected_source_hashes = {
        doc_id: _record_digest(record) for doc_id, record in source_by_id.items()
    }
    output_by_id: dict[str, dict[str, object]] = {}
    for bucket, rows in (("gold_candidate", gold_rows), ("uncertain", uncertain_rows)):
        for row in rows:
            doc_id = str(row.get("doc_id") or "")
            lineage = row.get("judging_lineage")
            primary_lineage = f"primary_judge:local_openai:{contract['judge_model']}"
            lineage_ok = (
                isinstance(lineage, list)
                and all(isinstance(item, str) for item in lineage)
                and primary_lineage in lineage
            )
            expected_shadow = contract.get("shadow_model")
            shadow_ok = expected_shadow is None or (
                isinstance(lineage, list)
                and f"shadow_judge:local_openai:{expected_shadow}" in lineage
                and row.get("shadow_judge_model_revision")
                == contract["shadow_model_manifest_sha256"]
            )
            source = source_by_id.get(doc_id, {})
            source_identity_ok = all(
                row.get(field) == source.get(field)
                for field in (
                    "label",
                    "scenario_id",
                    "factor_profile_id",
                    "expected_factor_scores",
                )
            )
            if (
                not doc_id
                or doc_id in output_by_id
                or expected_source_hashes.get(doc_id) != row.get("source_record_sha256")
                or row.get("decision_bucket") != bucket
                or row.get("primary_judge_model") != contract["judge_model"]
                or row.get("primary_judge_model_revision")
                != contract["judge_model_manifest_sha256"]
                or not lineage_ok
                or not shadow_ok
                or not source_identity_ok
            ):
                raise ProxyJudgingShardControllerError(
                    f"judge shard {spec.index} output/source binding mismatch"
                )
            output_by_id[doc_id] = row
    if set(output_by_id) != set(expected_source_hashes):
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} output document ids mismatch"
        )
    input_ids = [str(record.get("doc_id") or "") for record in records]
    journal_ids = [str(row.get("doc_id") or "") for row in journal_rows]
    expected_gold_ids = [
        doc_id
        for doc_id in journal_ids
        if output_by_id.get(doc_id, {}).get("decision_bucket") == "gold_candidate"
    ]
    expected_uncertain_ids = [
        doc_id
        for doc_id in journal_ids
        if output_by_id.get(doc_id, {}).get("decision_bucket") == "uncertain"
    ]
    if (
        journal_ids != input_ids
        or len(set(journal_ids)) != input_count
        or any(
            output_by_id.get(doc_id) != row
            for doc_id, row in zip(journal_ids, journal_rows)
        )
        or [str(row.get("doc_id") or "") for row in gold_rows] != expected_gold_ids
        or [str(row.get("doc_id") or "") for row in uncertain_rows]
        != expected_uncertain_ids
    ):
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} decision journal mismatch"
        )

    try:
        expected_gold_by_grade = dict(Counter(row.get("label") for row in gold_rows))
        expected_gold_by_scenario = dict(
            sorted(
                Counter(str(row.get("scenario_id") or "") for row in gold_rows).items()
            )
        )
        expected_gold_by_factor_profile = dict(
            sorted(
                Counter(
                    str(row.get("factor_profile_id") or "") for row in gold_rows
                ).items()
            )
        )
        expected_uncertain_by_factor_profile = dict(
            sorted(
                Counter(
                    str(row.get("factor_profile_id") or "")
                    for row in uncertain_rows
                ).items()
            )
        )
        expected_uncertain_by_status = dict(
            Counter(row.get("status") for row in uncertain_rows)
        )
    except TypeError as exc:
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} contains non-scalar label/status values"
        ) from exc
    count_expectations = {
        "input": input_count,
        "completed": input_count,
        "gold_candidate": gold_count,
        "uncertain": uncertain_count,
    }
    if any(stats.get(field) != value for field, value in count_expectations.items()):
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} stats count mismatch"
        )
    base_target_by_scenario = _normalized_count_map(
        fresh_upstream.get("base_final_target_by_scenario"),
        field=f"judge shard {spec.index} base_target_by_scenario",
    )
    base_target_by_factor_profile = _normalized_count_map(
        fresh_upstream.get("base_final_target_by_factor_profile"),
        field=f"judge shard {spec.index} base_target_by_factor_profile",
    )
    expected_shortfall_by_scenario = {
        scenario_id: target - expected_gold_by_scenario.get(scenario_id, 0)
        for scenario_id, target in base_target_by_scenario.items()
        if expected_gold_by_scenario.get(scenario_id, 0) < target
    }
    expected_shortfall_by_factor_profile = {
        profile: target - expected_gold_by_factor_profile.get(profile, 0)
        for profile, target in base_target_by_factor_profile.items()
        if expected_gold_by_factor_profile.get(profile, 0) < target
    }
    expected_ready_for_exact_assembly = not (
        expected_shortfall_by_scenario or expected_shortfall_by_factor_profile
    )
    if (
        stats.get("run_id") != spec.judge_run_id
        or manifest.get("input_count") != input_count
        or stats.get("gold_by_grade") != expected_gold_by_grade
        or stats.get("gold_by_scenario") != expected_gold_by_scenario
        or stats.get("gold_by_factor_profile")
        != expected_gold_by_factor_profile
        or stats.get("uncertain_by_factor_profile")
        != expected_uncertain_by_factor_profile
        or stats.get("base_target_by_scenario") != base_target_by_scenario
        or stats.get("base_target_by_factor_profile")
        != base_target_by_factor_profile
        or stats.get("gold_shortfall_by_scenario")
        != expected_shortfall_by_scenario
        or stats.get("gold_shortfall_by_factor_profile")
        != expected_shortfall_by_factor_profile
        or stats.get("ready_for_exact_assembly")
        is not expected_ready_for_exact_assembly
        or stats.get("uncertain_by_status") != expected_uncertain_by_status
        or progress.get("status") != "complete"
        or any(progress.get(field) != value for field, value in stats.items())
    ):
        raise ProxyJudgingShardControllerError(
            f"judge shard {spec.index} stats/progress envelope mismatch"
        )
    for field in (
        "judge_errors",
        "rule_errors_advisory",
        "advisory_rule_disagreements",
        "judge_parse_failures",
    ):
        value = _strict_nonnegative_int(stats.get(field), field=field)
        if value > input_count:
            raise ProxyJudgingShardControllerError(
                f"judge shard {spec.index} {field} exceeds input count"
            )

    return {
        "judge_manifest_sha256": actual_hashes["manifest"],
        "gold_candidate_sha256": actual_hashes["gold"],
        "uncertain_sha256": actual_hashes["uncertain"],
        "journal_sha256": actual_hashes["journal"],
        "stats_sha256": actual_hashes["stats"],
        "progress_sha256": actual_hashes["progress"],
        "complete_sha256": actual_hashes["complete"],
        "primary_judge_runtime_binding_sha256": (
            primary_runtime_attestation["binding_sha256"]
            if primary_runtime_attestation is not None
            else None
        ),
        "shadow_judge_runtime_binding_sha256": (
            shadow_runtime_attestation["binding_sha256"]
            if shadow_runtime_attestation is not None
            else None
        ),
        "input_sha256": input_sha256,
        "input_count": input_count,
        "completed_count": input_count,
        "gold_candidate_count": gold_count,
        "uncertain_count": uncertain_count,
        "gold_by_scenario": expected_gold_by_scenario,
        "gold_by_factor_profile": expected_gold_by_factor_profile,
        "uncertain_by_factor_profile": expected_uncertain_by_factor_profile,
        "base_target_by_scenario": base_target_by_scenario,
        "base_target_by_factor_profile": base_target_by_factor_profile,
        "gold_shortfall_by_scenario": expected_shortfall_by_scenario,
        "gold_shortfall_by_factor_profile": (
            expected_shortfall_by_factor_profile
        ),
        "ready_for_exact_assembly": expected_ready_for_exact_assembly,
        "target_met": True,
    }


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
            row.get("status") in _SUCCESSFUL_JUDGE_STATUSES for row in results
        ),
        "failed_shards": sum(row.get("status") == "failed" for row in results),
        "results": list(results),
    }


def _prepare_controller_directory(
    *,
    controller_out_root: Path,
    resume_controller: Path | None,
    run_prefix: str,
    contract: Mapping[str, object],
    runtime_model_attestations: Mapping[str, object],
) -> tuple[Path, dict[str, object]]:
    expected_dir = _checked_path(
        controller_out_root / run_prefix,
        purpose="judge controller run directory",
    )
    if resume_controller is None:
        if not controller_out_root.exists():
            _mkdir_artifact(controller_out_root, parents=True)
        elif _is_linklike(controller_out_root) or not controller_out_root.is_dir():
            raise ProxyJudgingShardControllerError(
                f"controller output root is not a regular directory: {controller_out_root}"
            )
        try:
            _mkdir_artifact(expected_dir)
        except FileExistsError as exc:
            raise ProxyJudgingShardControllerError(
                "controller directory exists; use --resume-controller explicitly"
            ) from exc
        manifest: dict[str, object] = {
            **contract,
            "status": "running",
            "created_at": _utc_now(),
            "resume_count": 0,
            "runtime_model_attestations": dict(runtime_model_attestations),
            "runtime_model_attestation_revalidations": [],
        }
        _atomic_write_json(expected_dir / "manifest.json", manifest, replace=False)
        _atomic_write_json(
            expected_dir / "progress.json",
            _progress_payload(
                run_prefix=run_prefix,
                run_contract_sha256=str(contract["run_contract_sha256"]),
                results=[],
                status="running",
            ),
            replace=False,
        )
        return expected_dir, manifest

    controller_dir = _checked_path(
        resume_controller,
        purpose="resume controller",
        require_directory=True,
    )
    if controller_dir != expected_dir:
        raise ProxyJudgingShardControllerError(
            "resume controller path does not match controller_out_root/run_prefix"
        )
    if (controller_dir / "COMPLETE.json").exists():
        raise ProxyJudgingShardControllerError("completed controller is immutable")
    manifest = _read_json_object(
        controller_dir / "manifest.json", purpose="judge controller manifest"
    )
    progress = _read_json_object(
        controller_dir / "progress.json", purpose="judge controller progress"
    )
    if (
        manifest.get("schema_version") != CONTROLLER_SCHEMA_VERSION
        or manifest.get("run_prefix") != run_prefix
        or manifest.get("status") not in {"running", "interrupted"}
        or manifest.get("run_contract_sha256") != contract["run_contract_sha256"]
        or any(manifest.get(field) != value for field, value in contract.items())
    ):
        raise ProxyJudgingShardControllerError(
            "judge controller resume manifest/contract mismatch"
        )
    recorded_runtime_model_attestations = manifest.get(
        "runtime_model_attestations"
    )
    if not isinstance(recorded_runtime_model_attestations, Mapping):
        raise ProxyJudgingShardControllerError(
            "judge controller runtime model attestations are missing"
        )
    for role, contract_field in (
        ("primary", "primary_judge_runtime_attestation_sha256"),
        ("shadow", "shadow_judge_runtime_attestation_sha256"),
    ):
        recorded = recorded_runtime_model_attestations.get(role)
        current = runtime_model_attestations.get(role)
        expected_binding = contract.get(contract_field)
        if expected_binding is None:
            if recorded is not None or current is not None:
                raise ProxyJudgingShardControllerError(
                    f"judge controller {role} runtime model attestation mismatch"
                )
            continue
        try:
            validated_recorded = validate_ollama_attestation(
                recorded, require_verified=True
            )
            validated_current = validate_ollama_attestation(
                current, require_verified=True
            )
        except OllamaAttestationError as exc:
            raise ProxyJudgingShardControllerError(
                f"judge controller {role} runtime model attestation is invalid"
            ) from exc
        if (
            validated_recorded["binding_sha256"] != expected_binding
            or validated_current["binding_sha256"] != expected_binding
        ):
            raise ProxyJudgingShardControllerError(
                f"judge controller {role} runtime model binding changed"
            )
    if (
        progress.get("schema_version") != CONTROLLER_SCHEMA_VERSION
        or progress.get("run_prefix") != run_prefix
        or progress.get("run_contract_sha256") != contract["run_contract_sha256"]
        or progress.get("status") not in {"running", "interrupted"}
        or not isinstance(progress.get("results"), list)
        or not isinstance(progress.get("completed_shard_indices"), list)
    ):
        raise ProxyJudgingShardControllerError(
            "judge controller resume progress mismatch"
        )
    indices = [
        row.get("index") for row in progress["results"] if isinstance(row, Mapping)
    ]
    if (
        len(indices) != len(progress["results"])
        or indices != progress["completed_shard_indices"]
        or len(indices) != len(set(indices))
        or any(
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < SHARD_COUNT
            for index in indices
        )
    ):
        raise ProxyJudgingShardControllerError(
            "judge controller progress shard indices are invalid"
        )
    active_index = progress.get("active_shard_index")
    if active_index is not None and (
        isinstance(active_index, bool)
        or not isinstance(active_index, int)
        or not 0 <= active_index < SHARD_COUNT
    ):
        raise ProxyJudgingShardControllerError(
            "judge controller active shard index is invalid"
        )
    for forbidden in ("stats.json", "COMPLETE.json"):
        forbidden_path = controller_dir / forbidden
        if forbidden_path.exists() or _is_linklike(forbidden_path):
            raise ProxyJudgingShardControllerError(
                f"incomplete controller contains unexpected {forbidden}"
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
        raise ProxyJudgingShardControllerError(
            "judge controller resume metadata is invalid"
        )
    manifest["status"] = "running"
    manifest["resume_count"] = resume_count + 1
    manifest["resumed_at"] = [*resumed_at, _utc_now()]
    revalidations = manifest.get("runtime_model_attestation_revalidations", [])
    if not isinstance(revalidations, list):
        raise ProxyJudgingShardControllerError(
            "judge controller runtime model revalidations are invalid"
        )
    manifest["runtime_model_attestation_revalidations"] = [
        *revalidations,
        dict(runtime_model_attestations),
    ]
    _atomic_write_json(controller_dir / "manifest.json", manifest, replace=True)
    return controller_dir, manifest


def _final_revalidate(
    *,
    generation_controller_dir: Path,
    generation_out_root: Path,
    intended_use: str,
    initial_generation_attestation: Mapping[str, object],
    specs: Sequence[JudgeShardSpec],
    judging_out_root: Path,
    contract: Mapping[str, object],
    results: list[dict[str, object]],
) -> dict[str, object]:
    """Reopen every promotable dependency immediately before final commit."""

    def fail_successful_results(message: str) -> None:
        for result in results:
            if result.get("status") in _SUCCESSFUL_JUDGE_STATUSES:
                result["pre_final_revalidation_status"] = result["status"]
                result["status"] = "failed"
                result["failure"] = message

    try:
        final_generation_attestation, final_shard_attestations = (
            _attest_generation_controller(
                generation_controller_dir,
                generation_out_root=generation_out_root,
                intended_use=intended_use,
                runtime_base_url=str(contract["base_url"]),
                revalidate_runtime=True,
            )
        )
        final_specs = _build_specs(
            run_prefix=str(contract["run_prefix"]),
            shard_attestations=final_shard_attestations,
        )
        _validate_generator_independence(
            final_specs, judge_model=str(contract["judge_model"])
        )
    except (OSError, OllamaAttestationError, ProxyJudgingShardControllerError) as exc:
        message = f"final generation revalidation failed: {exc}"
        fail_successful_results(message)
        return {
            "status": "failed",
            "failure": message,
            "revalidated_at": _utc_now(),
            "revalidated_judge_shards": 0,
            "complete_set_revalidated": False,
        }
    if final_generation_attestation != initial_generation_attestation or [
        spec.contract_payload() for spec in final_specs
    ] != [spec.contract_payload() for spec in specs]:
        message = "final generation controller/input attestation changed"
        fail_successful_results(message)
        return {
            "status": "failed",
            "failure": message,
            "revalidated_at": _utc_now(),
            "revalidated_judge_shards": 0,
            "complete_set_revalidated": False,
        }

    try:
        final_runtime_model_attestations = _preflight_judge_runtime_models(
            base_url=str(contract["base_url"]),
            judge_model=str(contract["judge_model"]),
            judge_model_manifest_sha256=str(
                contract["judge_model_manifest_sha256"]
            ),
            shadow_model=(
                str(contract["shadow_model"])
                if contract.get("shadow_model") is not None
                else None
            ),
            shadow_model_manifest_sha256=(
                str(contract["shadow_model_manifest_sha256"])
                if contract.get("shadow_model_manifest_sha256") is not None
                else None
            ),
            live=True,
        )
    except ProxyJudgingShardControllerError as exc:
        message = f"final judge runtime model revalidation failed: {exc}"
        fail_successful_results(message)
        return {
            "status": "failed",
            "failure": message,
            "revalidated_at": _utc_now(),
            "revalidated_judge_shards": 0,
            "complete_set_revalidated": False,
        }
    for role, field in (
        ("primary", "primary_judge_runtime_attestation_sha256"),
        ("shadow", "shadow_judge_runtime_attestation_sha256"),
    ):
        attestation = final_runtime_model_attestations.get(role)
        binding = (
            attestation.get("binding_sha256")
            if isinstance(attestation, Mapping)
            else None
        )
        if binding != contract.get(field):
            message = f"final {role} judge runtime model binding changed"
            fail_successful_results(message)
            return {
                "status": "failed",
                "failure": message,
                "revalidated_at": _utc_now(),
                "revalidated_judge_shards": 0,
                "complete_set_revalidated": False,
            }

    specs_by_index = {spec.index: spec for spec in specs}
    revalidated = 0
    revalidation_failures = 0
    for result in results:
        if result.get("status") not in _SUCCESSFUL_JUDGE_STATUSES:
            continue
        index = int(result["index"])
        contract_spec = specs_by_index[index]
        spec = replace(
            contract_spec,
            judge_run_id=str(
                result.get("judge_run_id") or contract_spec.judge_run_id
            ),
        )
        try:
            verified = _verify_completed_judging_shard(
                Path(
                    str(
                        result.get("shard_dir")
                        or judging_out_root / spec.judge_run_id
                    )
                ),
                spec=spec,
                contract=contract,
            )
        except (OSError, ProxyJudgingShardControllerError) as exc:
            result["pre_final_revalidation_status"] = result["status"]
            result["status"] = "failed"
            result["failure"] = f"final judge revalidation failed: {exc}"
            revalidation_failures += 1
            continue
        mismatches = [
            field
            for field, expected in verified.items()
            if result.get(field) != expected
        ]
        if mismatches:
            result["pre_final_revalidation_status"] = result["status"]
            result["status"] = "failed"
            result["failure"] = (
                "final judge revalidation changed verified fields: "
                + ",".join(sorted(mismatches))
            )
            revalidation_failures += 1
            continue
        result["final_revalidated_at"] = _utc_now()
        result["final_revalidation_sha256"] = _sha256_bytes(
            _canonical_json_bytes(verified)
        )
        revalidated += 1

    complete_set = revalidated == SHARD_COUNT and revalidation_failures == 0
    return {
        "status": "verified" if complete_set else "incomplete",
        "generation_attestation_sha256": final_generation_attestation[
            "attestation_sha256"
        ],
        "primary_judge_runtime_attestation_sha256": contract[
            "primary_judge_runtime_attestation_sha256"
        ],
        "shadow_judge_runtime_attestation_sha256": contract[
            "shadow_judge_runtime_attestation_sha256"
        ],
        "revalidated_at": _utc_now(),
        "revalidated_judge_shards": revalidated,
        "judge_revalidation_failures": revalidation_failures,
        "complete_set_revalidated": complete_set,
    }


def run_controller(
    *,
    generation_controller_dir: Path,
    generation_out_root: Path,
    judging_out_root: Path,
    controller_out_root: Path,
    run_prefix: str,
    intended_use: str,
    base_url: str,
    judge_model: str,
    judge_model_manifest_sha256: str,
    shadow_model: str | None,
    shadow_model_manifest_sha256: str | None,
    k_min: int = 2,
    k_max: int = 3,
    temperature: float = 0.6,
    min_self_consistency: float = 0.67,
    require_evidence: bool = False,
    resume_controller: Path | None = None,
    subprocess_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> tuple[Path, dict[str, object], int]:
    """Run the ten judge shards sequentially and commit controller artifacts."""
    base_url = _validate_common_arguments(
        run_prefix=run_prefix,
        intended_use=intended_use,
        base_url=base_url,
        judge_model=judge_model,
        judge_model_manifest_sha256=judge_model_manifest_sha256,
        shadow_model=shadow_model,
        shadow_model_manifest_sha256=shadow_model_manifest_sha256,
        k_min=k_min,
        k_max=k_max,
        temperature=temperature,
        min_self_consistency=min_self_consistency,
    )
    runtime_model_attestations = _preflight_judge_runtime_models(
        base_url=base_url,
        judge_model=judge_model,
        judge_model_manifest_sha256=judge_model_manifest_sha256,
        shadow_model=shadow_model,
        shadow_model_manifest_sha256=shadow_model_manifest_sha256,
        live=True,
    )
    generation_controller_dir = _checked_path(
        generation_controller_dir, purpose="generation controller"
    )
    generation_out_root = _checked_path(
        generation_out_root, purpose="generation output root"
    )
    judging_out_root = _checked_path(judging_out_root, purpose="judging output root")
    controller_out_root = _checked_path(
        controller_out_root, purpose="judge controller output root"
    )
    generation_controller_attestation, shard_attestations = (
        _attest_generation_controller(
            generation_controller_dir,
            generation_out_root=generation_out_root,
            intended_use=intended_use,
            runtime_base_url=base_url,
            revalidate_runtime=True,
        )
    )
    specs = _build_specs(run_prefix=run_prefix, shard_attestations=shard_attestations)
    _validate_generator_independence(specs, judge_model=judge_model)
    contract = _controller_contract(
        run_prefix=run_prefix,
        intended_use=intended_use,
        generation_controller_attestation=generation_controller_attestation,
        generation_controller_dir=generation_controller_dir,
        generation_out_root=generation_out_root,
        judging_out_root=judging_out_root,
        controller_out_root=controller_out_root,
        base_url=base_url,
        judge_model=judge_model,
        judge_model_manifest_sha256=judge_model_manifest_sha256,
        shadow_model=shadow_model,
        shadow_model_manifest_sha256=shadow_model_manifest_sha256,
        k_min=k_min,
        k_max=k_max,
        temperature=temperature,
        min_self_consistency=min_self_consistency,
        require_evidence=require_evidence,
        runtime_model_attestations=runtime_model_attestations,
        specs=specs,
    )
    controller_dir, manifest = _prepare_controller_directory(
        controller_out_root=controller_out_root,
        resume_controller=resume_controller,
        run_prefix=run_prefix,
        contract=contract,
        runtime_model_attestations=runtime_model_attestations,
    )
    if not judging_out_root.exists():
        judging_out_root.mkdir(parents=True)
    elif _is_linklike(judging_out_root) or not judging_out_root.is_dir():
        raise ProxyJudgingShardControllerError(
            f"judging output root is not a regular directory: {judging_out_root}"
        )
    logs_dir = controller_dir / "logs"
    if logs_dir.exists():
        if _is_linklike(logs_dir) or not logs_dir.is_dir():
            raise ProxyJudgingShardControllerError(
                f"controller logs path is not a regular directory: {logs_dir}"
            )
    else:
        _mkdir_artifact(logs_dir)
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
                    active_shard_index=spec.index,
                ),
                replace=True,
            )
            shard_dir = judging_out_root / spec.judge_run_id
            result: dict[str, object] = {
                "index": spec.index,
                "generation_run_id": spec.generation_run_id,
                "judge_run_id": spec.judge_run_id,
                "input_path": str(spec.input_path),
                "input_sha256": spec.upstream_generation["candidates_sha256"],
                "input_count": spec.upstream_generation["input_count"],
                "shard_dir": str(shard_dir),
                "started_at": _utc_now(),
            }
            command: list[str] | None = None
            execution_spec = spec
            success_status = "completed"
            if shard_dir.exists() or _is_linklike(shard_dir):
                if not is_resume:
                    result.update(
                        status="failed",
                        failure="existing_judge_shard_requires_explicit_resume",
                    )
                elif _is_linklike(shard_dir) or not shard_dir.is_dir():
                    result.update(
                        status="failed",
                        failure="resume_judge_shard_is_not_a_regular_directory",
                    )
                elif (shard_dir / "COMPLETE.json").exists():
                    try:
                        verified = _verify_completed_judging_shard(
                            shard_dir, spec=spec, contract=contract
                        )
                    except (OSError, ProxyJudgingShardControllerError) as exc:
                        result.update(status="failed", failure=str(exc))
                    else:
                        result.update(status="skipped_verified", **verified)
                else:
                    result["abandoned_incomplete_shard_dir"] = str(shard_dir)
                    for recovery_index in range(1, attempt_index + 2):
                        recovery_run_id = _recovery_judge_run_id(
                            spec=spec,
                            run_contract_sha256=str(
                                contract["run_contract_sha256"]
                            ),
                            recovery_index=recovery_index,
                        )
                        recovery_spec = replace(
                            spec, judge_run_id=recovery_run_id
                        )
                        recovery_dir = judging_out_root / recovery_run_id
                        if recovery_dir.exists() or _is_linklike(recovery_dir):
                            if _is_linklike(recovery_dir) or not recovery_dir.is_dir():
                                result.update(
                                    status="failed",
                                    failure=(
                                        "judge recovery path is not a regular "
                                        "directory"
                                    ),
                                )
                                break
                            if not (recovery_dir / "COMPLETE.json").exists():
                                continue
                            try:
                                verified = _verify_completed_judging_shard(
                                    recovery_dir,
                                    spec=recovery_spec,
                                    contract=contract,
                                )
                            except (
                                OSError,
                                ProxyJudgingShardControllerError,
                            ) as exc:
                                result.update(status="failed", failure=str(exc))
                            else:
                                result.update(
                                    status="skipped_recovery_verified",
                                    judge_run_id=recovery_run_id,
                                    shard_dir=str(recovery_dir),
                                    recovery_index=recovery_index,
                                    **verified,
                                )
                            break
                        execution_spec = recovery_spec
                        shard_dir = recovery_dir
                        result.update(
                            judge_run_id=recovery_run_id,
                            shard_dir=str(recovery_dir),
                            recovery_index=recovery_index,
                            recovery_of=spec.judge_run_id,
                        )
                        command = _subprocess_command(
                            spec=execution_spec,
                            judging_out_root=judging_out_root,
                            intended_use=intended_use,
                            base_url=base_url,
                            judge_model=judge_model,
                            judge_model_manifest_sha256=(
                                judge_model_manifest_sha256
                            ),
                            shadow_model=shadow_model,
                            shadow_model_manifest_sha256=(
                                shadow_model_manifest_sha256
                            ),
                            k_min=k_min,
                            k_max=k_max,
                            temperature=temperature,
                            min_self_consistency=min_self_consistency,
                            require_evidence=require_evidence,
                        )
                        success_status = "recovered_completed"
                        break
            else:
                command = _subprocess_command(
                    spec=execution_spec,
                    judging_out_root=judging_out_root,
                    intended_use=intended_use,
                    base_url=base_url,
                    judge_model=judge_model,
                    judge_model_manifest_sha256=judge_model_manifest_sha256,
                    shadow_model=shadow_model,
                    shadow_model_manifest_sha256=shadow_model_manifest_sha256,
                    k_min=k_min,
                    k_max=k_max,
                    temperature=temperature,
                    min_self_consistency=min_self_consistency,
                    require_evidence=require_evidence,
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
                        env=_subprocess_environment(),
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
                    try:
                        _atomic_write_bytes(stdout_path, stdout, replace=False)
                        _atomic_write_bytes(stderr_path, stderr, replace=False)
                    except (OSError, ProxyJudgingShardControllerError) as exc:
                        result.update(
                            status="failed", failure=f"log commit failed: {exc}"
                        )
                    else:
                        result.update(
                            returncode=int(completed.returncode),
                            stdout_path=stdout_path.name,
                            stderr_path=stderr_path.name,
                            stdout_sha256=_sha256_bytes(stdout),
                            stderr_sha256=_sha256_bytes(stderr),
                        )
                        if completed.returncode != 0:
                            result.update(
                                status="failed",
                                failure=(
                                    "judge subprocess exited "
                                    f"{int(completed.returncode)}"
                                ),
                            )
                        else:
                            try:
                                verified = _verify_completed_judging_shard(
                                    shard_dir,
                                    spec=execution_spec,
                                    contract=contract,
                                )
                            except (OSError, ProxyJudgingShardControllerError) as exc:
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
                ),
                replace=True,
            )
    except KeyboardInterrupt:
        manifest.update(
            {
                "status": "interrupted",
                "interrupted_at": _utc_now(),
                "completed_shards": len(results),
            }
        )
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

    final_revalidation = _final_revalidate(
        generation_controller_dir=generation_controller_dir,
        generation_out_root=generation_out_root,
        intended_use=intended_use,
        initial_generation_attestation=generation_controller_attestation,
        specs=specs,
        judging_out_root=judging_out_root,
        contract=contract,
        results=results,
    )
    successful = sum(
        row.get("status") in _SUCCESSFUL_JUDGE_STATUSES for row in results
    )
    failed = sum(row.get("status") == "failed" for row in results)
    processing_complete = successful == SHARD_COUNT and failed == 0
    verified_results = [
        row for row in results if row.get("status") in _SUCCESSFUL_JUDGE_STATUSES
    ]
    gold_by_factor_profile = _sum_count_maps(
        [
            row["gold_by_factor_profile"]
            for row in verified_results
            if isinstance(row.get("gold_by_factor_profile"), Mapping)
        ]
    )
    uncertain_by_factor_profile = _sum_count_maps(
        [
            row["uncertain_by_factor_profile"]
            for row in verified_results
            if isinstance(row.get("uncertain_by_factor_profile"), Mapping)
        ]
    )
    gold_shortfall_by_scenario = _sum_count_maps(
        [
            row["gold_shortfall_by_scenario"]
            for row in verified_results
            if isinstance(row.get("gold_shortfall_by_scenario"), Mapping)
        ]
    )
    # Sum each shard's positive shortfall.  Excess gold in one independent
    # shard must never hide a missing scenario/profile in another shard.
    gold_shortfall_by_factor_profile = _sum_count_maps(
        [
            row["gold_shortfall_by_factor_profile"]
            for row in verified_results
            if isinstance(row.get("gold_shortfall_by_factor_profile"), Mapping)
        ]
    )
    ready_for_exact_assembly = (
        len(verified_results) == SHARD_COUNT
        and all(row.get("ready_for_exact_assembly") is True for row in verified_results)
        and not gold_shortfall_by_scenario
        and not gold_shortfall_by_factor_profile
    )
    target_met = processing_complete and ready_for_exact_assembly
    stats: dict[str, object] = {
        "schema_version": CONTROLLER_SCHEMA_VERSION,
        "run_prefix": run_prefix,
        "intended_use": intended_use,
        "catalog_split_role": contract["catalog_split_role"],
        "run_contract_sha256": contract["run_contract_sha256"],
        "primary_judge_runtime_attestation_sha256": contract[
            "primary_judge_runtime_attestation_sha256"
        ],
        "shadow_judge_runtime_attestation_sha256": contract[
            "shadow_judge_runtime_attestation_sha256"
        ],
        "status": "complete" if target_met else "failed",
        "shard_count": SHARD_COUNT,
        "attempted_shards": len(results),
        "launched_shards": sum("command" in row for row in results),
        "skipped_verified_shards": sum(
            row.get("status") == "skipped_verified" for row in results
        ),
        "recovered_completed_shards": sum(
            row.get("status") == "recovered_completed" for row in results
        ),
        "skipped_recovery_verified_shards": sum(
            row.get("status") == "skipped_recovery_verified"
            for row in results
        ),
        "successful_shards": successful,
        "failed_shards": failed,
        "all_shards_processed": processing_complete,
        "expected_input_count": generation_controller_attestation["input_count"],
        "verified_input_count": sum(
            int(row["input_count"]) for row in verified_results
        ),
        "verified_completed_count": sum(
            int(row["completed_count"]) for row in verified_results
        ),
        "gold_candidate_count": sum(
            int(row["gold_candidate_count"]) for row in verified_results
        ),
        "uncertain_count": sum(int(row["uncertain_count"]) for row in verified_results),
        "planned_by_factor_profile": generation_controller_attestation[
            "planned_by_factor_profile"
        ],
        "base_target_by_factor_profile": generation_controller_attestation[
            "base_final_target_by_factor_profile"
        ],
        "candidate_by_factor_profile": generation_controller_attestation[
            "candidate_by_factor_profile"
        ],
        "gold_by_factor_profile": gold_by_factor_profile,
        "uncertain_by_factor_profile": uncertain_by_factor_profile,
        "gold_shortfall_by_scenario": gold_shortfall_by_scenario,
        "gold_shortfall_by_factor_profile": gold_shortfall_by_factor_profile,
        "ready_for_exact_assembly": ready_for_exact_assembly,
        "final_revalidation": final_revalidation,
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
    complete_material = {
        "schema_version": CONTROLLER_SCHEMA_VERSION,
        "run_prefix": run_prefix,
        "run_contract_sha256": contract["run_contract_sha256"],
        "manifest_sha256": _sha256_bytes(manifest_payload),
        "stats_sha256": _sha256_bytes(stats_payload),
        "progress_sha256": _sha256_bytes(progress_payload),
        "target_met": target_met,
        "exit_code": 0 if target_met else 1,
        "runtime_model_attestations": dict(runtime_model_attestations),
    }
    complete = {
        **complete_material,
        "complete_payload_sha256": _sha256_bytes(
            _canonical_json_bytes(complete_material)
        ),
    }
    _atomic_write_json(controller_dir / "COMPLETE.json", complete, replace=False)
    return controller_dir, stats, 0 if target_met else 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation-controller", type=Path, required=True)
    parser.add_argument(
        "--generation-out-root",
        type=Path,
        default=Path("datasets/proxy_gold/generation_runs"),
    )
    parser.add_argument(
        "--judging-out-root",
        type=Path,
        default=Path("datasets/proxy_gold/judged_runs"),
    )
    parser.add_argument(
        "--controller-out-root",
        type=Path,
        default=Path("datasets/proxy_gold/judging_controllers"),
    )
    parser.add_argument("--run-prefix", required=True)
    parser.add_argument("--intended-use", choices=sorted(_USE_SPLITS), required=True)
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--judge-model", default="gemma3:12b")
    parser.add_argument("--judge-model-manifest-sha256", required=True)
    parser.add_argument("--shadow-model", default="qwen3:14b")
    parser.add_argument("--shadow-model-manifest-sha256")
    parser.add_argument("--no-shadow", action="store_true")
    parser.add_argument("--k-min", type=int, default=2)
    parser.add_argument("--k-max", type=int, default=3)
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--min-self-consistency", type=float, default=0.67)
    parser.add_argument("--require-evidence", action="store_true")
    parser.add_argument("--resume-controller", type=Path)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="verify all inputs and print the immutable plan without writing",
    )
    args = parser.parse_args(argv)
    if args.dry_run and args.resume_controller is not None:
        parser.error("--dry-run cannot be combined with --resume-controller")
    if not args.no_shadow and not args.shadow_model_manifest_sha256:
        parser.error("--shadow-model-manifest-sha256 is required with a shadow model")
    if args.no_shadow and args.shadow_model_manifest_sha256:
        parser.error(
            "--shadow-model-manifest-sha256 cannot be combined with --no-shadow"
        )
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    shadow_model = None if args.no_shadow else args.shadow_model
    shadow_revision = None if args.no_shadow else args.shadow_model_manifest_sha256
    try:
        normalized_url = _validate_common_arguments(
            run_prefix=args.run_prefix,
            intended_use=args.intended_use,
            base_url=args.base_url,
            judge_model=args.judge_model,
            judge_model_manifest_sha256=args.judge_model_manifest_sha256,
            shadow_model=shadow_model,
            shadow_model_manifest_sha256=shadow_revision,
            k_min=args.k_min,
            k_max=args.k_max,
            temperature=args.temperature,
            min_self_consistency=args.min_self_consistency,
        )
        if args.dry_run:
            generation_controller_dir = _checked_path(
                args.generation_controller, purpose="generation controller"
            )
            generation_out_root = _checked_path(
                args.generation_out_root, purpose="generation output root"
            )
            judging_out_root = _checked_path(
                args.judging_out_root, purpose="judging output root"
            )
            controller_out_root = _checked_path(
                args.controller_out_root,
                purpose="judge controller output root",
            )
            generation_attestation, shard_attestations = _attest_generation_controller(
                generation_controller_dir,
                generation_out_root=generation_out_root,
                intended_use=args.intended_use,
                runtime_base_url=normalized_url,
                revalidate_runtime=False,
            )
            specs = _build_specs(
                run_prefix=args.run_prefix,
                shard_attestations=shard_attestations,
            )
            _validate_generator_independence(specs, judge_model=args.judge_model)
            runtime_model_attestations = _preflight_judge_runtime_models(
                base_url=normalized_url,
                judge_model=args.judge_model,
                judge_model_manifest_sha256=args.judge_model_manifest_sha256,
                shadow_model=shadow_model,
                shadow_model_manifest_sha256=shadow_revision,
                live=False,
            )
            contract = _controller_contract(
                run_prefix=args.run_prefix,
                intended_use=args.intended_use,
                generation_controller_attestation=generation_attestation,
                generation_controller_dir=generation_controller_dir,
                generation_out_root=generation_out_root,
                judging_out_root=judging_out_root,
                controller_out_root=controller_out_root,
                base_url=normalized_url,
                judge_model=args.judge_model,
                judge_model_manifest_sha256=args.judge_model_manifest_sha256,
                shadow_model=shadow_model,
                shadow_model_manifest_sha256=shadow_revision,
                k_min=args.k_min,
                k_max=args.k_max,
                temperature=args.temperature,
                min_self_consistency=args.min_self_consistency,
                require_evidence=args.require_evidence,
                runtime_model_attestations=runtime_model_attestations,
                specs=specs,
            )
            commands = [
                _subprocess_command(
                    spec=spec,
                    judging_out_root=judging_out_root,
                    intended_use=args.intended_use,
                    base_url=normalized_url,
                    judge_model=args.judge_model,
                    judge_model_manifest_sha256=args.judge_model_manifest_sha256,
                    shadow_model=shadow_model,
                    shadow_model_manifest_sha256=shadow_revision,
                    k_min=args.k_min,
                    k_max=args.k_max,
                    temperature=args.temperature,
                    min_self_consistency=args.min_self_consistency,
                    require_evidence=args.require_evidence,
                )
                for spec in specs
            ]
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "controller_contract": contract,
                        "runtime_model_attestations": runtime_model_attestations,
                        "commands": commands,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        controller_dir, stats, exit_code = run_controller(
            generation_controller_dir=args.generation_controller,
            generation_out_root=args.generation_out_root,
            judging_out_root=args.judging_out_root,
            controller_out_root=args.controller_out_root,
            run_prefix=args.run_prefix,
            intended_use=args.intended_use,
            base_url=normalized_url,
            judge_model=args.judge_model,
            judge_model_manifest_sha256=args.judge_model_manifest_sha256,
            shadow_model=shadow_model,
            shadow_model_manifest_sha256=shadow_revision,
            k_min=args.k_min,
            k_max=args.k_max,
            temperature=args.temperature,
            min_self_consistency=args.min_self_consistency,
            require_evidence=args.require_evidence,
            resume_controller=args.resume_controller,
        )
    except (OSError, ProxyJudgingShardControllerError) as exc:
        print(f"proxy judging shard controller failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"controller_dir": str(controller_dir), "stats": stats},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
