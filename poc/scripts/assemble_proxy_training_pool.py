"""Assemble an exact, immutable proxy-training document pool.

This is the fail-closed boundary between judged proxy candidates and
``materialize_proxy_training_set.py``.  It selects documents only; the output
rows retain the judged-candidate schema so the materializer can consume
``training_pool.jsonl`` without translation.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
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
from scripts.build_proxy_scenarios import load_catalog  # noqa: E402
from scripts.assemble_public_s3_challenge import (  # noqa: E402
    ChallengeAssemblyError,
    load_blocked_corpora,
)
from scripts.assemble_public_s3_training_pool import (  # noqa: E402
    PublicTrainingAssemblyError,
    load_public_s3_training_pool,
)
from lloydk.hygiene import text_hash  # noqa: E402
from lloydk.proxy_corpus import (  # noqa: E402
    GRADE_CODES,
    proxy_record_intended_use,
    validate_proxy_record,
)


SCHEMA_VERSION = "proxy-training-pool-run-v1"
DEFAULT_FINAL_TARGETS = {"TS": 750, "S1": 750, "S2": 750, "S3": 750}
DEFAULT_PUBLIC_REAL_S3_TARGET = 300
DEFAULT_SYNTHETIC_TARGETS = {
    **DEFAULT_FINAL_TARGETS,
    "S3": DEFAULT_FINAL_TARGETS["S3"] - DEFAULT_PUBLIC_REAL_S3_TARGET,
}
DEFAULT_EXPECTED_FROZEN_PRIMARY = 1_000
DEFAULT_EXPECTED_SHAPES = 12
DEFAULT_EXPECTED_LENGTH_PROFILES = 3
DEFAULT_EXPECTED_FAMILIES = 225
DEFAULT_MINIMUM_PUBLIC_HOLDOUT_ARTIFACTS = 2
DEFAULT_MINIMUM_PUBLIC_HOLDOUT_RECORDS = 600
SELECTION_SEED = "proxy-training-pool-balanced-v1"
_GRADE_ORDER = ("TS", "S1", "S2", "S3")
_QUALITY_CHECKS = (
    "structure_appropriate",
    "timeline_consistent",
    "quantitative_consistent",
    "non_repetitive",
)
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class TrainingPoolAssemblyError(ValueError):
    """The candidate population cannot satisfy the training-pool contract."""


@dataclass(frozen=True)
class TrainingPoolSelection:
    """An exact selected pool and its pre-publication audit."""

    selected: tuple[dict[str, object], ...]
    audit: dict[str, object]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _stable_hash(*parts: object) -> str:
    return hashlib.sha256(
        ":".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _canonical_record_bytes(records: Sequence[Mapping[str, object]]) -> bytes:
    ordered = sorted(
        (dict(row) for row in records),
        key=lambda row: (
            str(row.get("document_family_id") or ""),
            str(row.get("doc_id") or ""),
            text_hash(str(row.get("text") or "")),
        ),
    )
    return b"".join(_canonical_json_bytes(row) + b"\n" for row in ordered)


def _jsonl_bytes(records: Sequence[Mapping[str, object]]) -> bytes:
    return b"".join(_canonical_json_bytes(dict(row)) + b"\n" for row in records)


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise TrainingPoolAssemblyError(f"refusing to overwrite artifact: {path}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> bytes:
    encoded = (
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True).encode(
            "utf-8"
        )
        + b"\n"
    )
    _atomic_write(path, encoded)
    return encoded


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"proxy-training-pool-{stamp}-{uuid.uuid4().hex[:10]}"


def _source_stats_with_hashes(stats: Mapping[str, object]) -> dict[str, object]:
    enriched = dict(stats)
    files: list[dict[str, object]] = []
    for item in stats.get("loaded_files", []):
        if not isinstance(item, Mapping):
            raise TrainingPoolAssemblyError("invalid loader file statistics")
        path = Path(str(item.get("path") or ""))
        if not path.is_file():
            raise TrainingPoolAssemblyError(f"loaded source disappeared: {path}")
        files.append({**dict(item), "sha256": _sha256_bytes(path.read_bytes())})
    enriched["loaded_files"] = files
    return enriched


def _code_attestation() -> dict[str, object]:
    paths = (
        Path(__file__).resolve(),
        (_POC / "scripts" / "assemble_proxy_gold.py").resolve(),
        (_POC / "scripts" / "assemble_public_s3_challenge.py").resolve(),
        (_POC / "scripts" / "assemble_public_s3_training_pool.py").resolve(),
        (_POC / "src" / "lloydk" / "proxy_corpus.py").resolve(),
        (_POC / "src" / "lloydk" / "hygiene.py").resolve(),
    )
    files = []
    for path in paths:
        try:
            display = str(path.relative_to(_POC.resolve()))
        except ValueError:
            display = str(path)
        files.append({"path": display, "sha256": _sha256_bytes(path.read_bytes())})
    return {
        "files": files,
        "contract_sha256": _sha256_bytes(_canonical_json_bytes(files)),
    }


def _load_attested_public_training_artifacts(
    paths: Sequence[Path],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Load only committed public-real S3 training artifact envelopes."""
    if not paths:
        raise TrainingPoolAssemblyError(
            "an attested public-real S3 training artifact is required"
        )
    rows: list[dict[str, object]] = []
    artifact_audits: list[dict[str, object]] = []
    resolved_paths: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in resolved_paths:
            raise TrainingPoolAssemblyError(
                f"duplicate public S3 training artifact path: {resolved}"
            )
        resolved_paths.add(resolved)
        artifact_rows, audit = load_public_s3_training_pool(path)
        rows.extend(dict(row) for row in artifact_rows)
        artifact_audits.append(dict(audit))
    _ensure_unique_candidates(rows)
    return rows, {
        "artifact_count": len(artifact_audits),
        "row_count": len(rows),
        "artifacts": artifact_audits,
        "records_sha256": _sha256_bytes(_canonical_record_bytes(rows)),
    }


def _load_attested_public_holdouts(
    paths: Sequence[Path],
    *,
    minimum_artifacts: int,
    minimum_records: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Load immutable public S3 challenges and recheck their exact identities."""
    attested = load_blocked_corpora(paths)
    if len(attested.files) < minimum_artifacts:
        raise TrainingPoolAssemblyError(
            "blocked public holdouts require at least "
            f"{minimum_artifacts} attested artifacts; found {len(attested.files)}"
        )
    if attested.row_count < minimum_records:
        raise TrainingPoolAssemblyError(
            "blocked public holdouts require at least "
            f"{minimum_records} attested records; found {attested.row_count}"
        )
    cross_artifact_uniqueness = {
        "unique_doc_ids": len(attested.doc_ids),
        "unique_document_family_ids": len(attested.document_family_ids),
        "unique_normalized_text_hashes": len(attested.normalized_text_hashes),
    }
    if any(value != attested.row_count for value in cross_artifact_uniqueness.values()):
        raise TrainingPoolAssemblyError(
            "attested public holdout artifacts overlap each other: "
            + json.dumps(
                {
                    "rows": attested.row_count,
                    **cross_artifact_uniqueness,
                },
                sort_keys=True,
            )
        )
    records_paths = [Path(str(item["records_path"])) for item in attested.files]
    rows, _ = _load_corpus(records_paths, purpose="attested public S3 holdout")
    actual_doc_ids = {_required_string(row, "doc_id") for row in rows}
    actual_families = {_required_string(row, "document_family_id") for row in rows}
    actual_hashes = {text_hash(str(row.get("text") or "")) for row in rows}
    if (
        len(rows) != attested.row_count
        or actual_doc_ids != set(attested.doc_ids)
        or actual_families != set(attested.document_family_ids)
        or actual_hashes != set(attested.normalized_text_hashes)
    ):
        raise TrainingPoolAssemblyError(
            "public S3 holdout identities changed after artifact verification"
        )
    for item in attested.files:
        records_path = Path(str(item["records_path"]))
        if _sha256_bytes(records_path.read_bytes()) != item["records_sha256"]:
            raise TrainingPoolAssemblyError(
                f"public S3 holdout changed after verification: {records_path}"
            )
    return rows, {
        "artifact_count": len(attested.files),
        "row_count": attested.row_count,
        "artifacts": [dict(item) for item in attested.files],
        "union_uniqueness": {
            **cross_artifact_uniqueness,
        },
        "records_sha256": _sha256_bytes(_canonical_record_bytes(rows)),
    }


def _required_string(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        return ""
    return value.strip()


def _strict_quality_gate_errors(record: Mapping[str, object]) -> tuple[str, ...]:
    """Require the current semantic/document-quality adjudication contract."""
    errors: list[str] = []
    if record.get("training_use_permitted") is not True:
        errors.append("training_use_permitted_not_true")
    if record.get("decision_bucket") != "gold_candidate":
        errors.append("decision_bucket_not_gold_candidate")
    if record.get("gate_version") != "proxy_semantic_quality_v2":
        errors.append("gate_version_not_proxy_semantic_quality_v2")
    evidence_card = record.get("evidence_card")
    if (
        not isinstance(evidence_card, Mapping)
        or evidence_card.get("schema") != "proxy-evidence-v1"
    ):
        errors.append("evidence_card_not_proxy_evidence_v1")
    evidence = record.get("consensus_evidence")
    if not isinstance(evidence, Mapping):
        return (*errors, "missing_consensus_evidence")
    if evidence.get("schema") != "proxy-semantic-quality-adjudication-v2":
        errors.append("consensus_schema_not_quality_v2")
    if evidence.get("semantic_gate_passed") is not True:
        errors.append("semantic_gate_not_passed")
    if evidence.get("semantic_gate_failures") not in ([], ()):
        errors.append("semantic_gate_has_failures")
    if evidence.get("document_quality_gate_passed") is not True:
        errors.append("document_quality_gate_not_passed")
    if evidence.get("document_quality_gate_failures") not in ([], ()):
        errors.append("document_quality_gate_has_failures")
    passed = evidence.get("quality_check_passed")
    if not isinstance(passed, Mapping) or any(
        passed.get(check) is not True for check in _QUALITY_CHECKS
    ):
        errors.append("document_quality_checks_not_unanimously_true")
    return tuple(errors)


def _profile_errors(record: Mapping[str, object]) -> tuple[str, ...]:
    errors: list[str] = []
    for field in ("family_profile_id", "length_profile_id"):
        if not _required_string(record, field):
            errors.append(f"missing:{field}")
    text = str(record.get("text") or "")
    minimum = record.get("requested_profile_min_chars")
    maximum = record.get("requested_profile_max_chars")
    if type(minimum) is not int or type(maximum) is not int:
        errors.append("invalid:requested_profile_char_bounds")
    elif minimum < 1 or maximum < minimum or not minimum <= len(text) <= maximum:
        errors.append(f"outside_requested_profile:{len(text)}!={minimum}..{maximum}")
    return tuple(errors)


def _candidate_errors(record: Mapping[str, object]) -> tuple[str, ...]:
    check = validate_proxy_record(record, stage="eligible", intended_use="training")
    try:
        declared_use = proxy_record_intended_use(record)
    except ValueError as exc:
        usage_errors = (f"catalog_usage_contract:{exc}",)
    else:
        usage_errors = (
            ()
            if declared_use == "training"
            else (f"catalog_usage_contract:unexpected:{declared_use}",)
        )
    return (
        tuple(check.errors)
        + usage_errors
        + _strict_quality_gate_errors(record)
        + _profile_errors(record)
    )


def _validate_holdout(
    records: Sequence[Mapping[str, object]], *, purpose: str, require_public: bool
) -> None:
    failures: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for index, record in enumerate(records):
        doc_id = _required_string(record, "doc_id")
        family = _required_string(record, "document_family_id")
        digest = text_hash(str(record.get("text") or ""))
        if not doc_id or not family:
            errors = ["missing_doc_id_or_family"]
        else:
            errors = list(
                validate_proxy_record(
                    record, stage="eligible", intended_use="evaluation"
                ).errors
            )
        if record.get("evaluation_use_permitted") is not True:
            errors.append("evaluation_use_permitted_not_true")
        if record.get("document_origin") == "synthetic":
            try:
                declared_use = proxy_record_intended_use(record)
            except ValueError as exc:
                errors.append(f"catalog_usage_contract:{exc}")
            else:
                if declared_use != "evaluation":
                    errors.append(f"catalog_usage_contract:unexpected:{declared_use}")
        if require_public and record.get("document_origin") != "public_real":
            errors.append("blocked_public_holdout_not_public_real")
        if doc_id in seen_ids:
            errors.append("duplicate_doc_id")
        if digest in seen_hashes:
            errors.append("duplicate_normalized_text_hash")
        seen_ids.add(doc_id)
        seen_hashes.add(digest)
        if errors:
            failures.append({"index": index, "doc_id": doc_id, "errors": errors})
            if len(failures) == 20:
                break
    if failures:
        raise TrainingPoolAssemblyError(
            f"{purpose} failed evaluation validation: "
            + json.dumps(failures, ensure_ascii=False, sort_keys=True)
        )


def _ensure_unique_candidates(records: Sequence[Mapping[str, object]]) -> None:
    doc_ids: set[str] = set()
    hashes: set[str] = set()
    for index, record in enumerate(records):
        doc_id = _required_string(record, "doc_id")
        digest = text_hash(str(record.get("text") or ""))
        if not doc_id:
            raise TrainingPoolAssemblyError(f"candidate missing doc_id at row {index}")
        if doc_id in doc_ids:
            raise TrainingPoolAssemblyError(f"duplicate candidate doc_id: {doc_id}")
        if digest in hashes:
            raise TrainingPoolAssemblyError(
                f"duplicate candidate normalized text hash at row {index}: {digest}"
            )
        doc_ids.add(doc_id)
        hashes.add(digest)


def _validate_public_training_records(
    records: Sequence[Mapping[str, object]],
) -> None:
    """Validate licensed public-real S3 without imposing synthetic judge fields."""
    _ensure_unique_candidates(records)
    failures: list[dict[str, object]] = []
    for index, record in enumerate(records):
        errors = list(
            validate_proxy_record(
                record, stage="eligible", intended_use="training"
            ).errors
        )
        if record.get("document_origin") != "public_real":
            errors.append("public_training_requires_public_real")
        if record.get("label") != "S3":
            errors.append("public_training_requires_S3")
        if record.get("training_use_permitted") is not True:
            errors.append("public_training_use_not_permitted")
        if errors:
            failures.append(
                {
                    "index": index,
                    "doc_id": str(record.get("doc_id") or ""),
                    "errors": errors,
                }
            )
            if len(failures) == 20:
                break
    if failures:
        raise TrainingPoolAssemblyError(
            "public S3 training artifact contains ineligible records: "
            + json.dumps(failures, ensure_ascii=False, sort_keys=True)
        )


def _identity_overlap_counts(
    left: Sequence[Mapping[str, object]],
    right: Sequence[Mapping[str, object]],
) -> dict[str, int]:
    left_ids = {_required_string(row, "doc_id") for row in left}
    right_ids = {_required_string(row, "doc_id") for row in right}
    left_families = {_required_string(row, "document_family_id") for row in left}
    right_families = {_required_string(row, "document_family_id") for row in right}
    left_hashes = {text_hash(str(row.get("text") or "")) for row in left}
    right_hashes = {text_hash(str(row.get("text") or "")) for row in right}
    left_ids.discard("")
    right_ids.discard("")
    left_families.discard("")
    right_families.discard("")
    return {
        "doc_id_overlap": len(left_ids & right_ids),
        "document_family_id_overlap": len(left_families & right_families),
        "normalized_text_hash_overlap": len(left_hashes & right_hashes),
    }


def _balanced_quotas(values: Sequence[str], total: int, *, seed: str) -> dict[str, int]:
    if not values:
        raise TrainingPoolAssemblyError("cannot balance an empty category")
    base, remainder = divmod(total, len(values))
    order = sorted(values, key=lambda value: (_stable_hash(seed, value), value))
    return {
        value: base + (position < remainder) for position, value in enumerate(order)
    }


@dataclass
class _Edge:
    target: str
    reverse: int
    capacity: int
    initial_capacity: int


class _Dinic:
    """Small deterministic integer max-flow implementation."""

    def __init__(self) -> None:
        self.graph: dict[str, list[_Edge]] = defaultdict(list)

    def add_edge(self, source: str, target: str, capacity: int) -> tuple[str, int]:
        if capacity < 0:
            raise ValueError("flow capacity must be non-negative")
        forward = _Edge(target, len(self.graph[target]), capacity, capacity)
        reverse = _Edge(source, len(self.graph[source]), 0, 0)
        self.graph[source].append(forward)
        self.graph[target].append(reverse)
        return source, len(self.graph[source]) - 1

    def flow(self, handle: tuple[str, int]) -> int:
        source, index = handle
        edge = self.graph[source][index]
        return edge.initial_capacity - edge.capacity

    def max_flow(self, source: str, sink: str) -> int:
        total = 0
        while True:
            level = {source: 0}
            queue = deque([source])
            while queue:
                node = queue.popleft()
                for edge in self.graph[node]:
                    if edge.capacity > 0 and edge.target not in level:
                        level[edge.target] = level[node] + 1
                        queue.append(edge.target)
            if sink not in level:
                return total
            cursor: dict[str, int] = defaultdict(int)

            def send(node: str, amount: int) -> int:
                if node == sink:
                    return amount
                while cursor[node] < len(self.graph[node]):
                    edge = self.graph[node][cursor[node]]
                    if edge.capacity > 0 and level.get(edge.target) == level[node] + 1:
                        pushed = send(edge.target, min(amount, edge.capacity))
                        if pushed:
                            edge.capacity -= pushed
                            reverse = self.graph[edge.target][edge.reverse]
                            reverse.capacity += pushed
                            return pushed
                    cursor[node] += 1
                return 0

            while True:
                pushed = send(source, 1 << 60)
                if not pushed:
                    break
                total += pushed


def _balanced_cell_counts(
    rows: Sequence[dict[str, object]],
    *,
    grade: str,
    target: int,
    families: Sequence[str],
    shape_quotas: Mapping[str, int],
) -> dict[tuple[str, str], int]:
    """Solve exact family lower/upper and exact document-shape margins."""
    by_cell = Counter(
        (
            str(row["document_family_id"]),
            str(row["family_profile_id"]),
        )
        for row in rows
    )
    family_floor, family_remainder = divmod(target, len(families))
    family_ceiling = family_floor + bool(family_remainder)
    source, sink = "source", "sink"
    super_source, super_sink = "super-source", "super-sink"
    network = _Dinic()
    balances: Counter[str] = Counter()
    handles: dict[tuple[str, str], tuple[tuple[str, int], int]] = {}

    def add_bounded_edge(
        left: str, right: str, lower: int, upper: int
    ) -> tuple[tuple[str, int], int]:
        if lower > upper:
            raise TrainingPoolAssemblyError(
                f"invalid flow bounds for {grade}: {left}->{right} {lower}>{upper}"
            )
        handle = network.add_edge(left, right, upper - lower)
        balances[left] -= lower
        balances[right] += lower
        return handle, lower

    for family in sorted(families):
        family_node = f"family:{family}"
        add_bounded_edge(source, family_node, family_floor, family_ceiling)
        for shape in sorted(shape_quotas):
            capacity = by_cell[(family, shape)]
            if capacity:
                handles[(family, shape)] = add_bounded_edge(
                    family_node, f"shape:{shape}", 0, capacity
                )
    for shape, quota in sorted(shape_quotas.items()):
        add_bounded_edge(f"shape:{shape}", sink, quota, quota)
    network.add_edge(sink, source, target)

    required = 0
    for node, balance in sorted(balances.items()):
        if balance > 0:
            network.add_edge(super_source, node, balance)
            required += balance
        elif balance < 0:
            network.add_edge(node, super_sink, -balance)
    if network.max_flow(super_source, super_sink) != required:
        availability = {
            family: sum(by_cell[(family, shape)] for shape in shape_quotas)
            for family in families
        }
        raise TrainingPoolAssemblyError(
            f"grade {grade} cannot satisfy simultaneous family/shape quotas: "
            + json.dumps(
                {
                    "target": target,
                    "family_floor": family_floor,
                    "family_ceiling": family_ceiling,
                    "shape_quotas": dict(shape_quotas),
                    "families_below_floor": {
                        family: count
                        for family, count in availability.items()
                        if count < family_floor
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    counts: dict[tuple[str, str], int] = {}
    for cell, (handle, lower) in handles.items():
        count = lower + network.flow(handle)
        if count:
            counts[cell] = count
    if sum(counts.values()) != target:
        raise AssertionError(f"flow selected {sum(counts.values())}, expected {target}")
    return counts


def _direct_scenario_family_shape_counts(
    records: Sequence[Mapping[str, object]],
    *,
    grade: str,
    target: int,
    scenario_quotas: Mapping[str, int],
    families: Sequence[str],
    shape_quotas: Mapping[str, int],
    seed: str,
) -> dict[tuple[str, str, str], int]:
    """Select scenario x family x shape cells under one exact MILP contract.

    The older two-stage selector first fixed family/shape cells and then tried to
    fit scenarios into those cells.  A feasible joint solution could therefore
    be rejected solely because the first flow chose the wrong cells.  This
    model chooses the three axes at once, with integer cell counts bounded by
    actual candidate availability.
    """
    if target != sum(scenario_quotas.values()) or target != sum(
        shape_quotas.values()
    ):
        raise TrainingPoolAssemblyError(
            f"grade {grade} scenario, shape, and grade targets disagree"
        )
    available = Counter(
        (
            str(row.get("scenario_id") or ""),
            str(row.get("document_family_id") or ""),
            str(row.get("family_profile_id") or ""),
        )
        for row in records
    )
    cells = sorted(
        cell
        for cell, count in available.items()
        if count
        and cell[0] in scenario_quotas
        and cell[1] in families
        and cell[2] in shape_quotas
    )
    family_floor, family_remainder = divmod(target, len(families))
    family_ceiling = family_floor + bool(family_remainder)
    available_by_scenario = Counter()
    available_by_family = Counter()
    available_by_shape = Counter()
    for scenario_id, family, shape in cells:
        count = available[(scenario_id, family, shape)]
        available_by_scenario[scenario_id] += count
        available_by_family[family] += count
        available_by_shape[shape] += count

    local_shortfalls = {
        "scenario": {
            scenario_id: quota - available_by_scenario[scenario_id]
            for scenario_id, quota in sorted(scenario_quotas.items())
            if available_by_scenario[scenario_id] < quota
        },
        "family_below_floor": {
            family: family_floor - available_by_family[family]
            for family in sorted(families)
            if available_by_family[family] < family_floor
        },
        "shape": {
            shape: quota - available_by_shape[shape]
            for shape, quota in sorted(shape_quotas.items())
            if available_by_shape[shape] < quota
        },
    }
    if any(local_shortfalls.values()):
        raise TrainingPoolAssemblyError(
            f"grade {grade} cannot satisfy joint scenario/family/shape quotas: "
            + json.dumps(
                {
                    "target": target,
                    "family_floor": family_floor,
                    "family_ceiling": family_ceiling,
                    "scenario_quotas": dict(sorted(scenario_quotas.items())),
                    "shape_quotas": dict(sorted(shape_quotas.items())),
                    "local_shortfalls": local_shortfalls,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    try:
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
        from scipy.sparse import coo_array
    except ImportError as exc:  # pragma: no cover - production dependency guard
        raise TrainingPoolAssemblyError(
            "exact scenario/family/shape solver is unavailable; scipy is required"
        ) from exc

    column_for_cell = {cell: index for index, cell in enumerate(cells)}
    constraint_rows: list[int] = []
    constraint_columns: list[int] = []
    constraint_values: list[float] = []
    lower_bounds: list[float] = []
    upper_bounds: list[float] = []

    def add_constraint(
        matching_cells: Iterable[tuple[str, str, str]],
        lower: int,
        upper: int,
    ) -> None:
        row_index = len(lower_bounds)
        for cell in matching_cells:
            constraint_rows.append(row_index)
            constraint_columns.append(column_for_cell[cell])
            constraint_values.append(1.0)
        lower_bounds.append(float(lower))
        upper_bounds.append(float(upper))

    for scenario_id, quota in sorted(scenario_quotas.items()):
        add_constraint(
            (cell for cell in cells if cell[0] == scenario_id), quota, quota
        )
    for family in sorted(families):
        add_constraint(
            (cell for cell in cells if cell[1] == family),
            family_floor,
            family_ceiling,
        )
    for shape, quota in sorted(shape_quotas.items()):
        add_constraint((cell for cell in cells if cell[2] == shape), quota, quota)

    matrix = coo_array(
        (
            np.asarray(constraint_values, dtype=float),
            (
                np.asarray(constraint_rows, dtype=np.int32),
                np.asarray(constraint_columns, dtype=np.int32),
            ),
        ),
        shape=(len(lower_bounds), len(cells)),
    ).tocsr()
    objective = np.asarray(
        [
            int(_stable_hash(seed, grade, *cell)[:13], 16) / float(16**13)
            for cell in cells
        ],
        dtype=float,
    )
    result = milp(
        c=objective,
        integrality=np.ones(len(cells), dtype=np.int8),
        bounds=Bounds(
            np.zeros(len(cells), dtype=float),
            np.asarray([available[cell] for cell in cells], dtype=float),
        ),
        constraints=LinearConstraint(
            matrix,
            np.asarray(lower_bounds, dtype=float),
            np.asarray(upper_bounds, dtype=float),
        ),
        options={"presolve": True},
    )
    if not result.success or result.x is None:
        raise TrainingPoolAssemblyError(
            f"grade {grade} cannot satisfy joint scenario/family/shape quotas: "
            + json.dumps(
                {
                    "target": target,
                    "solver_status": int(result.status),
                    "solver_message": str(result.message),
                    "family_floor": family_floor,
                    "family_ceiling": family_ceiling,
                    "scenario_quotas": dict(sorted(scenario_quotas.items())),
                    "shape_quotas": dict(sorted(shape_quotas.items())),
                    "available_by_scenario": dict(
                        sorted(available_by_scenario.items())
                    ),
                    "available_by_family": dict(sorted(available_by_family.items())),
                    "available_by_shape": dict(sorted(available_by_shape.items())),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    rounded = np.rint(result.x).astype(np.int64)
    if np.max(np.abs(result.x - rounded), initial=0.0) > 1e-6:
        raise AssertionError(f"grade {grade} MILP returned non-integral counts")
    counts = {
        cell: int(count)
        for cell, count in zip(cells, rounded, strict=True)
        if count
    }
    selected_by_scenario = Counter()
    selected_by_family = Counter()
    selected_by_shape = Counter()
    for (scenario_id, family, shape), count in counts.items():
        if count < 0 or count > available[(scenario_id, family, shape)]:
            raise AssertionError(f"grade {grade} MILP violated a cell bound")
        selected_by_scenario[scenario_id] += count
        selected_by_family[family] += count
        selected_by_shape[shape] += count
    if selected_by_scenario != Counter(scenario_quotas):
        raise AssertionError(f"grade {grade} MILP violated scenario quotas")
    if selected_by_shape != Counter(shape_quotas):
        raise AssertionError(f"grade {grade} MILP violated shape quotas")
    if sum(counts.values()) != target or any(
        count < family_floor or count > family_ceiling
        for count in selected_by_family.values()
    ):
        raise AssertionError(f"grade {grade} MILP violated family quotas")
    return counts


def _distribution(records: Sequence[Mapping[str, object]]) -> dict[str, object]:
    by_grade = Counter(str(row.get("label") or "") for row in records)
    by_shape = Counter(
        str(row.get("family_profile_id") or "not_applicable") for row in records
    )
    by_length = Counter(
        str(row.get("length_profile_id") or "not_applicable") for row in records
    )
    by_family = Counter(str(row.get("document_family_id") or "") for row in records)
    by_origin = Counter(str(row.get("document_origin") or "") for row in records)
    by_origin_grade = Counter(
        (
            str(row.get("document_origin") or ""),
            str(row.get("label") or ""),
        )
        for row in records
    )
    by_factor_profile = Counter(
        str(row.get("factor_profile_id") or "not_applicable") for row in records
    )
    by_scenario = Counter(
        str(row.get("scenario_id") or "not_applicable") for row in records
    )
    return {
        "records": len(records),
        "by_grade": dict(sorted(by_grade.items())),
        "by_origin": dict(sorted(by_origin.items())),
        "by_origin_and_grade": {
            f"{origin}:{grade}": count
            for (origin, grade), count in sorted(by_origin_grade.items())
        },
        "by_document_shape": dict(sorted(by_shape.items())),
        "by_length_profile": dict(sorted(by_length.items())),
        "by_factor_profile": dict(sorted(by_factor_profile.items())),
        "by_scenario": dict(sorted(by_scenario.items())),
        "families": len(by_family),
        "family_count_min": min(by_family.values(), default=0),
        "family_count_max": max(by_family.values(), default=0),
    }


def select_training_pool(
    candidates: Iterable[Mapping[str, object]],
    *,
    public_training_records: Iterable[Mapping[str, object]] = (),
    blocked_records: Iterable[Mapping[str, object]],
    final_targets: Mapping[str, int] = DEFAULT_FINAL_TARGETS,
    public_real_s3_target: int = DEFAULT_PUBLIC_REAL_S3_TARGET,
    expected_document_shapes: int = DEFAULT_EXPECTED_SHAPES,
    expected_length_profiles: int = DEFAULT_EXPECTED_LENGTH_PROFILES,
    expected_families: int = DEFAULT_EXPECTED_FAMILIES,
    seed: str = SELECTION_SEED,
    scenario_targets: Mapping[str, int] | None = None,
    scenario_target_grades: Mapping[str, str] | None = None,
    scenario_factor_profiles: Mapping[str, str] | None = None,
) -> TrainingPoolSelection:
    """Validate, exclude holdouts, and deterministically balance an exact pool."""
    rows = [dict(row) for row in candidates]
    public_rows = [dict(row) for row in public_training_records]
    blocked = [dict(row) for row in blocked_records]
    if set(final_targets) != set(GRADE_CODES) or any(
        isinstance(final_targets[grade], bool)
        or not isinstance(final_targets[grade], int)
        or final_targets[grade] < 1
        for grade in _GRADE_ORDER
    ):
        raise TrainingPoolAssemblyError(
            "final_targets must contain positive integer TS/S1/S2/S3 counts"
        )
    normalized_final_targets = {grade: final_targets[grade] for grade in _GRADE_ORDER}
    if (
        isinstance(public_real_s3_target, bool)
        or not isinstance(public_real_s3_target, int)
        or public_real_s3_target < 0
        or public_real_s3_target >= normalized_final_targets["S3"]
    ):
        raise TrainingPoolAssemblyError(
            "public_real_s3_target must be non-negative and smaller than final S3"
        )
    normalized_targets = {
        **normalized_final_targets,
        "S3": normalized_final_targets["S3"] - public_real_s3_target,
    }
    scenario_contract_values = (
        scenario_targets,
        scenario_target_grades,
        scenario_factor_profiles,
    )
    if any(value is not None for value in scenario_contract_values) and not all(
        value is not None for value in scenario_contract_values
    ):
        raise TrainingPoolAssemblyError(
            "scenario_targets, scenario_target_grades, and "
            "scenario_factor_profiles must be provided together"
        )
    normalized_scenario_targets: dict[str, int] = {}
    normalized_scenario_grades: dict[str, str] = {}
    normalized_scenario_profiles: dict[str, str] = {}
    target_by_factor_profile: Counter[str] = Counter()
    if scenario_targets is not None:
        assert scenario_target_grades is not None
        assert scenario_factor_profiles is not None
        if set(scenario_targets) != set(scenario_target_grades) or set(
            scenario_targets
        ) != set(scenario_factor_profiles):
            raise TrainingPoolAssemblyError(
                "scenario quota maps must have identical keys"
            )
        target_by_scenario_grade: Counter[str] = Counter()
        for raw_scenario_id, raw_count in scenario_targets.items():
            scenario_id = str(raw_scenario_id).strip()
            grade = str(scenario_target_grades[raw_scenario_id]).strip()
            profile = str(scenario_factor_profiles[raw_scenario_id]).strip()
            if not scenario_id or not profile or grade not in GRADE_CODES:
                raise TrainingPoolAssemblyError("scenario quota metadata is invalid")
            if (
                isinstance(raw_count, bool)
                or not isinstance(raw_count, int)
                or raw_count < 1
            ):
                raise TrainingPoolAssemblyError(
                    "scenario quota counts must be positive integers"
                )
            normalized_scenario_targets[scenario_id] = raw_count
            normalized_scenario_grades[scenario_id] = grade
            normalized_scenario_profiles[scenario_id] = profile
            target_by_scenario_grade[grade] += raw_count
            target_by_factor_profile[profile] += raw_count
        if target_by_scenario_grade != Counter(normalized_targets):
            raise TrainingPoolAssemblyError(
                "scenario quotas must sum exactly to synthetic grade targets"
            )
    for name, value in (
        ("expected_document_shapes", expected_document_shapes),
        ("expected_length_profiles", expected_length_profiles),
        ("expected_families", expected_families),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise TrainingPoolAssemblyError(f"{name} must be a positive integer")
    _ensure_unique_candidates(rows)
    _validate_public_training_records(public_rows)
    if len(public_rows) != public_real_s3_target:
        raise TrainingPoolAssemblyError(
            f"public S3 training artifact must contain exactly "
            f"{public_real_s3_target} records; found {len(public_rows)}"
        )

    holdout_ids = {_required_string(row, "doc_id") for row in blocked}
    holdout_families = {_required_string(row, "document_family_id") for row in blocked}
    holdout_hashes = {text_hash(str(row.get("text") or "")) for row in blocked}
    public_ids = {_required_string(row, "doc_id") for row in public_rows}
    public_families = {
        _required_string(row, "document_family_id") for row in public_rows
    }
    public_hashes = {text_hash(str(row.get("text") or "")) for row in public_rows}
    if len(public_families) != len(public_rows):
        raise TrainingPoolAssemblyError(
            "public S3 training artifact must contain one record per document family"
        )
    public_overlap = {
        "doc_id": len(public_ids & holdout_ids),
        "document_family_id": len(public_families & holdout_families),
        "normalized_text": len(public_hashes & holdout_hashes),
    }
    if any(public_overlap.values()):
        raise TrainingPoolAssemblyError(
            "public S3 training artifact overlaps frozen/blocked corpora: "
            + json.dumps(public_overlap, sort_keys=True)
        )

    synthetic_exclusions = [*blocked, *public_rows]
    blocked_ids = {_required_string(row, "doc_id") for row in synthetic_exclusions}
    blocked_families = {
        _required_string(row, "document_family_id") for row in synthetic_exclusions
    }
    blocked_hashes = {
        text_hash(str(row.get("text") or "")) for row in synthetic_exclusions
    }
    blocked_ids.discard("")
    blocked_families.discard("")

    invalid_reasons: Counter[str] = Counter()
    overlap_reasons: Counter[str] = Counter()
    invalid_records = 0
    overlap_records = 0
    unexpected_scenario_records = 0
    scenario_contract_mismatch_records = 0
    eligible: list[dict[str, object]] = []
    for row in rows:
        overlaps = []
        if _required_string(row, "doc_id") in blocked_ids:
            overlaps.append("doc_id")
        if _required_string(row, "document_family_id") in blocked_families:
            overlaps.append("document_family_id")
        if text_hash(str(row.get("text") or "")) in blocked_hashes:
            overlaps.append("normalized_text")
        if overlaps:
            overlap_records += 1
            overlap_reasons.update(overlaps)
            continue
        errors = _candidate_errors(row)
        if errors:
            invalid_records += 1
            invalid_reasons.update(errors)
            continue
        if normalized_scenario_targets:
            scenario_id = str(row.get("scenario_id") or "").strip()
            if scenario_id not in normalized_scenario_targets:
                unexpected_scenario_records += 1
                continue
            if (
                str(row.get("label") or "")
                != normalized_scenario_grades[scenario_id]
                or str(row.get("factor_profile_id") or "").strip()
                != normalized_scenario_profiles[scenario_id]
            ):
                scenario_contract_mismatch_records += 1
                invalid_records += 1
                invalid_reasons.update(["scenario_contract_mismatch"])
                continue
        eligible.append(row)

    available_by_grade = Counter(str(row.get("label") or "") for row in eligible)
    available_by_scenario = Counter(
        str(row.get("scenario_id") or "") for row in eligible
    )
    available_by_factor_profile = Counter(
        str(row.get("factor_profile_id") or "") for row in eligible
    )
    shortfalls = {
        grade: target - available_by_grade[grade]
        for grade, target in normalized_targets.items()
        if available_by_grade[grade] < target
    }
    scenario_shortfalls = {
        scenario_id: target - available_by_scenario[scenario_id]
        for scenario_id, target in sorted(normalized_scenario_targets.items())
        if available_by_scenario[scenario_id] < target
    }
    factor_profile_shortfalls = {
        profile: target - available_by_factor_profile[profile]
        for profile, target in sorted(target_by_factor_profile.items())
        if available_by_factor_profile[profile] < target
    }
    if shortfalls or scenario_shortfalls or factor_profile_shortfalls:
        raise TrainingPoolAssemblyError(
            "eligible judged candidates are insufficient: "
            + json.dumps(
                {
                    "shortfalls": shortfalls,
                    "shortfalls_by_scenario": scenario_shortfalls,
                    "shortfalls_by_factor_profile": factor_profile_shortfalls,
                    "available_by_grade": dict(available_by_grade),
                    "available_by_scenario": dict(available_by_scenario),
                    "available_by_factor_profile": dict(
                        available_by_factor_profile
                    ),
                    "invalid_reasons": dict(invalid_reasons),
                    "holdout_overlap_reasons": dict(overlap_reasons),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    shapes = sorted({_required_string(row, "family_profile_id") for row in eligible})
    lengths = sorted({_required_string(row, "length_profile_id") for row in eligible})
    families = sorted({_required_string(row, "document_family_id") for row in eligible})
    expected_actual = {
        "document shapes": (expected_document_shapes, len(shapes)),
        "length profiles": (expected_length_profiles, len(lengths)),
        "families": (expected_families, len(families)),
    }
    mismatches = {
        name: {"expected": expected, "actual": actual}
        for name, (expected, actual) in expected_actual.items()
        if expected != actual
    }
    if mismatches:
        raise TrainingPoolAssemblyError(
            "eligible category cardinality mismatch: "
            + json.dumps(mismatches, ensure_ascii=False, sort_keys=True)
        )

    shape_to_lengths: dict[str, set[str]] = defaultdict(set)
    for row in eligible:
        shape_to_lengths[str(row["family_profile_id"])].add(
            str(row["length_profile_id"])
        )
    inconsistent_shapes = {
        shape: sorted(values)
        for shape, values in shape_to_lengths.items()
        if len(values) != 1
    }
    if inconsistent_shapes:
        raise TrainingPoolAssemblyError(
            "document shape maps to multiple length profiles: "
            + json.dumps(inconsistent_shapes, ensure_ascii=False, sort_keys=True)
        )
    shape_to_length = {
        shape: next(iter(values)) for shape, values in shape_to_lengths.items()
    }

    selected: list[dict[str, object]] = []
    quotas_by_grade: dict[str, object] = {}
    for grade in _GRADE_ORDER:
        grade_rows = [row for row in eligible if row.get("label") == grade]
        grade_shapes = {str(row["family_profile_id"]) for row in grade_rows}
        grade_lengths = {str(row["length_profile_id"]) for row in grade_rows}
        grade_families = {str(row["document_family_id"]) for row in grade_rows}
        if (
            grade_shapes != set(shapes)
            or grade_lengths != set(lengths)
            or grade_families != set(families)
        ):
            raise TrainingPoolAssemblyError(
                f"grade {grade} does not cover the common shape/length/family universe"
            )

        target = normalized_targets[grade]
        if target < len(families):
            raise TrainingPoolAssemblyError(
                f"grade {grade} target {target} cannot cover all {len(families)} families"
            )
        length_quotas = _balanced_quotas(lengths, target, seed=f"{seed}:{grade}:length")
        shape_quotas: dict[str, int] = {}
        for length in lengths:
            length_shapes = sorted(
                shape for shape in shapes if shape_to_length[shape] == length
            )
            shape_quotas.update(
                _balanced_quotas(
                    length_shapes,
                    length_quotas[length],
                    seed=f"{seed}:{grade}:shape:{length}",
                )
            )
        if max(shape_quotas.values()) - min(shape_quotas.values()) > 1:
            raise TrainingPoolAssemblyError(
                f"grade {grade} cannot have globally balanced document-shape quotas"
            )

        grade_scenario_quotas = {
            scenario_id: count
            for scenario_id, count in normalized_scenario_targets.items()
            if normalized_scenario_grades[scenario_id] == grade
        }
        if grade_scenario_quotas:
            scenario_cell_counts = _direct_scenario_family_shape_counts(
                grade_rows,
                grade=grade,
                target=target,
                scenario_quotas=grade_scenario_quotas,
                families=families,
                shape_quotas=shape_quotas,
                seed=seed,
            )
            by_scenario_cell: dict[
                tuple[str, str, str], list[dict[str, object]]
            ] = defaultdict(list)
            for row in grade_rows:
                by_scenario_cell[
                    (
                        str(row["scenario_id"]),
                        str(row["document_family_id"]),
                        str(row["family_profile_id"]),
                    )
                ].append(row)
            for scenario_cell, count in sorted(scenario_cell_counts.items()):
                ranked = sorted(
                    by_scenario_cell[scenario_cell],
                    key=lambda row: (
                        _stable_hash(
                            seed,
                            grade,
                            *scenario_cell,
                            row["doc_id"],
                            text_hash(row["text"]),
                        ),
                        str(row["doc_id"]),
                    ),
                )
                if len(ranked) < count:
                    raise AssertionError(
                        f"joint solver over-selected cell {scenario_cell}"
                    )
                selected.extend(ranked[:count])
        else:
            cell_counts = _balanced_cell_counts(
                grade_rows,
                grade=grade,
                target=target,
                families=families,
                shape_quotas=shape_quotas,
            )
            by_cell: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(
                list
            )
            for row in grade_rows:
                by_cell[
                    (str(row["document_family_id"]), str(row["family_profile_id"]))
                ].append(row)
            for cell, count in sorted(cell_counts.items()):
                ranked = sorted(
                    by_cell[cell],
                    key=lambda row: (
                        _stable_hash(
                            seed,
                            grade,
                            cell[0],
                            cell[1],
                            row["doc_id"],
                            text_hash(row["text"]),
                        ),
                        str(row["doc_id"]),
                    ),
                )
                if len(ranked) < count:
                    raise AssertionError(f"flow over-selected cell {cell}")
                selected.extend(ranked[:count])
        quotas_by_grade[grade] = {
            "target": target,
            "length_profile": dict(sorted(length_quotas.items())),
            "document_shape": dict(sorted(shape_quotas.items())),
            "family_floor": target // len(families),
            "family_ceiling": (target + len(families) - 1) // len(families),
            "scenario": dict(sorted(grade_scenario_quotas.items())),
        }

    selected.sort(
        key=lambda row: (
            _GRADE_ORDER.index(str(row["label"])),
            str(row["document_family_id"]),
            str(row["family_profile_id"]),
            str(row["doc_id"]),
        )
    )
    selected_ids = {str(row["doc_id"]) for row in selected}
    selected_families = {str(row["document_family_id"]) for row in selected}
    selected_hashes = {text_hash(str(row["text"])) for row in selected}
    if len(selected) != sum(normalized_targets.values()):
        raise AssertionError("selected record count is not exact")
    if len(selected_ids) != len(selected) or len(selected_hashes) != len(selected):
        raise AssertionError("selected pool is not unique")
    if (
        selected_ids & blocked_ids
        or selected_families & blocked_families
        or selected_hashes & blocked_hashes
    ):
        raise AssertionError("selected pool overlaps a frozen/blocked corpus")
    selected_by_scenario = Counter(
        str(row.get("scenario_id") or "") for row in selected
    )
    selected_by_factor_profile = Counter(
        str(row.get("factor_profile_id") or "") for row in selected
    )
    if normalized_scenario_targets and selected_by_scenario != Counter(
        normalized_scenario_targets
    ):
        raise AssertionError(
            "selected synthetic pool does not match exact scenario quotas"
        )
    if target_by_factor_profile and selected_by_factor_profile != target_by_factor_profile:
        raise AssertionError(
            "selected synthetic pool does not match exact factor-profile quotas"
        )
    for grade in _GRADE_ORDER:
        grade_selected = [row for row in selected if row["label"] == grade]
        family_counts = Counter(
            str(row["document_family_id"]) for row in grade_selected
        )
        shape_counts = Counter(str(row["family_profile_id"]) for row in grade_selected)
        length_counts = Counter(str(row["length_profile_id"]) for row in grade_selected)
        if len(grade_selected) != normalized_targets[grade]:
            raise AssertionError(f"grade {grade} count is not exact")
        if max(family_counts.values()) - min(family_counts.values()) > 1:
            raise AssertionError(f"grade {grade} family balance failed")
        if max(shape_counts.values()) - min(shape_counts.values()) > 1:
            raise AssertionError(f"grade {grade} document-shape balance failed")
        if max(length_counts.values()) - min(length_counts.values()) > 1:
            raise AssertionError(f"grade {grade} length-profile balance failed")
        for row in grade_selected:
            errors = _candidate_errors(row)
            if errors:
                raise AssertionError(
                    f"selected row failed revalidation: {row['doc_id']} {errors}"
                )

    synthetic_selected = list(selected)
    selected = [*synthetic_selected, *public_rows]
    selected.sort(
        key=lambda row: (
            _GRADE_ORDER.index(str(row["label"])),
            str(row.get("document_origin") or ""),
            str(row["document_family_id"]),
            str(row["doc_id"]),
        )
    )
    combined_ids = {str(row["doc_id"]) for row in selected}
    combined_families = {str(row["document_family_id"]) for row in selected}
    combined_hashes = {text_hash(str(row["text"])) for row in selected}
    if (
        len(combined_ids) != len(selected)
        or len(combined_hashes) != len(selected)
        or selected_families & public_families
    ):
        raise AssertionError("combined synthetic/public pool is not exactly unique")
    final_grade_counts = Counter(str(row["label"]) for row in selected)
    if final_grade_counts != Counter(normalized_final_targets):
        raise AssertionError(
            f"combined final grade distribution mismatch: {dict(final_grade_counts)}"
        )
    origin_counts = Counter(str(row.get("document_origin") or "") for row in selected)
    expected_origin_counts = {
        "synthetic": sum(normalized_targets.values()),
        "public_real": public_real_s3_target,
    }
    if origin_counts != Counter(expected_origin_counts):
        raise AssertionError(
            f"combined origin distribution mismatch: {dict(origin_counts)}"
        )
    if (
        combined_ids & holdout_ids
        or combined_families & holdout_families
        or combined_hashes & holdout_hashes
    ):
        raise AssertionError("combined pool overlaps frozen/blocked corpora")

    audit: dict[str, object] = {
        "synthetic_input_records": len(rows),
        "synthetic_eligible_after_exclusion": len(eligible),
        "public_training_input_records": len(public_rows),
        "selected_records": len(selected),
        "final_targets": normalized_final_targets,
        "synthetic_targets": normalized_targets,
        "public_real_s3_target": public_real_s3_target,
        "synthetic_available_by_grade": dict(sorted(available_by_grade.items())),
        "synthetic_target_by_scenario": dict(
            sorted(normalized_scenario_targets.items())
        ),
        "synthetic_available_by_scenario": dict(
            sorted(available_by_scenario.items())
        ),
        "synthetic_selected_by_scenario": dict(
            sorted(selected_by_scenario.items())
        ),
        "synthetic_target_by_factor_profile": dict(
            sorted(target_by_factor_profile.items())
        ),
        "synthetic_available_by_factor_profile": dict(
            sorted(available_by_factor_profile.items())
        ),
        "synthetic_selected_by_factor_profile": dict(
            sorted(selected_by_factor_profile.items())
        ),
        "synthetic_invalid_records": invalid_records,
        "holdout_overlap_records": overlap_records,
        "unexpected_scenario_records": unexpected_scenario_records,
        "scenario_contract_mismatch_records": scenario_contract_mismatch_records,
        "scenario_quota_contract": bool(normalized_scenario_targets),
        "synthetic_invalid_reason_counts": dict(sorted(invalid_reasons.items())),
        "holdout_overlap_reason_counts": dict(sorted(overlap_reasons.items())),
        "selection_seed": seed,
        "synthetic_quotas_by_grade": quotas_by_grade,
        "distribution": _distribution(selected),
        "synthetic_distribution": _distribution(synthetic_selected),
        "public_real_distribution": _distribution(public_rows),
        "origin_counts": dict(sorted(origin_counts.items())),
        "origin_label_counts": {
            f"{origin}:{grade}": count
            for (origin, grade), count in sorted(
                Counter(
                    (
                        str(row.get("document_origin") or ""),
                        str(row.get("label") or ""),
                    )
                    for row in selected
                ).items()
            )
        },
        "leakage_checks": {
            "doc_id_overlap": 0,
            "document_family_id_overlap": 0,
            "normalized_text_hash_overlap": 0,
        },
    }
    return TrainingPoolSelection(tuple(selected), audit)


def assemble_training_pool_run(
    *,
    input_paths: Sequence[Path],
    public_training_paths: Sequence[Path],
    frozen_primary_paths: Sequence[Path],
    blocked_public_paths: Sequence[Path],
    out_root: Path,
    run_id: str | None = None,
    final_targets: Mapping[str, int] = DEFAULT_FINAL_TARGETS,
    public_real_s3_target: int = DEFAULT_PUBLIC_REAL_S3_TARGET,
    expected_frozen_primary_count: int = DEFAULT_EXPECTED_FROZEN_PRIMARY,
    expected_document_shapes: int = DEFAULT_EXPECTED_SHAPES,
    expected_length_profiles: int = DEFAULT_EXPECTED_LENGTH_PROFILES,
    expected_families: int = DEFAULT_EXPECTED_FAMILIES,
    minimum_public_holdout_artifacts: int = DEFAULT_MINIMUM_PUBLIC_HOLDOUT_ARTIFACTS,
    minimum_public_holdout_records: int = DEFAULT_MINIMUM_PUBLIC_HOLDOUT_RECORDS,
    seed: str = SELECTION_SEED,
    catalog_path: Path | None = None,
) -> tuple[Path, dict[str, object]]:
    """Build and atomically publish one immutable materializer-ready pool."""
    if not input_paths:
        raise TrainingPoolAssemblyError("at least one judged input is required")
    if not public_training_paths:
        raise TrainingPoolAssemblyError(
            "an attested public-real S3 training artifact is required"
        )
    if not frozen_primary_paths:
        raise TrainingPoolAssemblyError(
            "at least one frozen primary corpus is required"
        )
    if not blocked_public_paths:
        raise TrainingPoolAssemblyError(
            "at least one blocked public holdout is required, including public300 diagnostics"
        )
    if (
        isinstance(expected_frozen_primary_count, bool)
        or not isinstance(expected_frozen_primary_count, int)
        or expected_frozen_primary_count < 1
    ):
        raise TrainingPoolAssemblyError(
            "expected frozen primary count must be a positive integer"
        )
    if (
        isinstance(minimum_public_holdout_artifacts, bool)
        or not isinstance(minimum_public_holdout_artifacts, int)
        or minimum_public_holdout_artifacts < 1
        or isinstance(minimum_public_holdout_records, bool)
        or not isinstance(minimum_public_holdout_records, int)
        or minimum_public_holdout_records < 1
    ):
        raise TrainingPoolAssemblyError(
            "minimum public holdout artifacts/records must be positive"
        )
    scenario_targets: dict[str, int] | None = None
    scenario_target_grades: dict[str, str] | None = None
    scenario_factor_profiles: dict[str, str] | None = None
    catalog_attestation: dict[str, object] | None = None
    if catalog_path is not None:
        try:
            catalog, catalog_scenarios = load_catalog(catalog_path)
        except (OSError, json.JSONDecodeError, SystemExit) as exc:
            raise TrainingPoolAssemblyError(f"catalog loading failed: {exc}") from exc
        if catalog.get("split_role") != "train_pool_only":
            raise TrainingPoolAssemblyError(
                "training-pool catalog must declare train_pool_only"
            )
        scenario_targets = {
            str(row["scenario_id"]): int(row["target_count"])
            for row in catalog_scenarios
        }
        scenario_target_grades = {
            str(row["scenario_id"]): str(row["label"])
            for row in catalog_scenarios
        }
        scenario_factor_profiles = {
            str(row["scenario_id"]): str(row["factor_profile_id"])
            for row in catalog_scenarios
        }
        catalog_attestation = {
            "path": str(catalog_path),
            "version": str(catalog.get("version") or "unknown"),
            "factor_profile_schema_id": str(
                catalog.get("factor_profile_schema_id") or "legacy"
            ),
            "sha256": _sha256_bytes(catalog_path.read_bytes()),
            "scenarios": len(catalog_scenarios),
        }

    run_id_value = run_id or _new_run_id()
    if not _SAFE_RUN_ID.fullmatch(run_id_value):
        raise TrainingPoolAssemblyError(f"unsafe run id: {run_id_value!r}")
    final_dir = out_root / run_id_value
    if final_dir.exists():
        raise TrainingPoolAssemblyError(f"run directory already exists: {final_dir}")

    candidates, candidate_stats = _load_corpus(
        list(input_paths), purpose="judged candidate input"
    )
    public_training, public_training_stats = _load_attested_public_training_artifacts(
        public_training_paths
    )
    frozen, frozen_stats = _load_corpus(
        list(frozen_primary_paths), purpose="frozen primary corpus"
    )
    public_blocked, public_stats = _load_attested_public_holdouts(
        blocked_public_paths,
        minimum_artifacts=minimum_public_holdout_artifacts,
        minimum_records=minimum_public_holdout_records,
    )
    if len(frozen) != expected_frozen_primary_count:
        raise TrainingPoolAssemblyError(
            f"frozen primary must contain exactly {expected_frozen_primary_count} records; "
            f"found {len(frozen)}"
        )
    _validate_holdout(frozen, purpose="frozen primary", require_public=False)
    _validate_holdout(
        public_blocked, purpose="blocked public holdout", require_public=True
    )
    frozen_public_holdout_overlap = _identity_overlap_counts(frozen, public_blocked)
    if any(frozen_public_holdout_overlap.values()):
        raise TrainingPoolAssemblyError(
            "frozen primary overlaps attested public holdouts: "
            + json.dumps(frozen_public_holdout_overlap, sort_keys=True)
        )
    selection = select_training_pool(
        candidates,
        public_training_records=public_training,
        blocked_records=[*frozen, *public_blocked],
        final_targets=final_targets,
        public_real_s3_target=public_real_s3_target,
        expected_document_shapes=expected_document_shapes,
        expected_length_profiles=expected_length_profiles,
        expected_families=expected_families,
        seed=seed,
        scenario_targets=scenario_targets,
        scenario_target_grades=scenario_target_grades,
        scenario_factor_profiles=scenario_factor_profiles,
    )
    frozen_overlap = _identity_overlap_counts(selection.selected, frozen)
    public_holdout_overlap = _identity_overlap_counts(
        selection.selected, public_blocked
    )
    if any(frozen_overlap.values()) or any(public_holdout_overlap.values()):
        raise AssertionError("named holdout leakage audit is non-zero")

    output_payload = _jsonl_bytes(selection.selected)
    artifact = {
        "path": "training_pool.jsonl",
        "records": len(selection.selected),
        "bytes": len(output_payload),
        "sha256": _sha256_bytes(output_payload),
        **_distribution(selection.selected),
    }
    code = _code_attestation()
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id_value,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "claim_scope": "proxy_training_only_not_customer_or_production_accuracy",
        "human_reviewed": False,
        "contract": {
            "materializer_ready_schema": "eligible proxy records unchanged",
            "final_target_counts": {
                str(key): int(value) for key, value in final_targets.items()
            },
            "synthetic_target_counts": {
                **{str(key): int(value) for key, value in final_targets.items()},
                "S3": int(final_targets["S3"]) - public_real_s3_target,
            },
            "public_real_s3_target": public_real_s3_target,
            "expected_origin_counts": {
                "synthetic": sum(int(value) for value in final_targets.values())
                - public_real_s3_target,
                "public_real": public_real_s3_target,
            },
            "expected_document_shapes": expected_document_shapes,
            "expected_length_profiles": expected_length_profiles,
            "expected_synthetic_families": expected_families,
            "required_training_use_permitted": True,
            "synthetic_required_gate_version": "proxy_semantic_quality_v2",
            "synthetic_required_evidence_schema": "proxy-evidence-v1",
            "public_required_contract": (
                "attested public-real S3 training artifact with source licence, "
                "attribution, and explicit training permission"
            ),
            "normalized_text_hash": "lloydk.hygiene.text_hash (SHA1 of whitespace-free text)",
            "frozen_primary_role": "evaluation_exclusion_only",
            "blocked_public_holdout_role": (
                "development_diagnostic_exclusion_only; never training selection"
            ),
            "minimum_attested_public_holdout_artifacts": (
                minimum_public_holdout_artifacts
            ),
            "minimum_attested_public_holdout_records": minimum_public_holdout_records,
            "selection_seed": seed,
            "scenario_quota_contract": catalog_attestation is not None,
            "factor_profile_catalog": catalog_attestation,
        },
        "inputs": {
            "judged_candidates": {
                **_source_stats_with_hashes(candidate_stats),
                "records_sha256": _sha256_bytes(_canonical_record_bytes(candidates)),
            },
            "public_s3_training": public_training_stats,
            "frozen_primary": {
                **_source_stats_with_hashes(frozen_stats),
                "records_sha256": _sha256_bytes(_canonical_record_bytes(frozen)),
            },
            "blocked_public_holdouts": public_stats,
        },
        "code": code,
        "selection_audit": selection.audit,
        "leakage_checks": {
            "frozen_primary_vs_attested_public_holdouts": (
                frozen_public_holdout_overlap
            ),
            "selected_vs_frozen_primary": frozen_overlap,
            "selected_vs_attested_public_holdouts": public_holdout_overlap,
        },
        "artifact": artifact,
    }

    out_root.mkdir(parents=True, exist_ok=True)
    staging = out_root / f".{run_id_value}.staging-{uuid.uuid4().hex}"
    staging.mkdir(exist_ok=False)
    try:
        _atomic_write(staging / "training_pool.jsonl", output_payload)
        manifest_bytes = _atomic_write_json(staging / "manifest.json", manifest)
        complete = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id_value,
            "committed_at": datetime.now(timezone.utc).isoformat(),
            "manifest_sha256": _sha256_bytes(manifest_bytes),
            "training_pool_sha256": artifact["sha256"],
            "training_pool_records": artifact["records"],
            "code_contract_sha256": code["contract_sha256"],
        }
        _atomic_write_json(staging / "COMPLETE", complete)
        if final_dir.exists():
            raise TrainingPoolAssemblyError(
                f"run directory already exists: {final_dir}"
            )
        try:
            staging.rename(final_dir)
        except OSError as exc:
            raise TrainingPoolAssemblyError(
                f"cannot atomically publish run {run_id_value}: {exc}"
            ) from exc
    except Exception:
        if staging.exists():
            for child in staging.iterdir():
                if child.is_file():
                    child.unlink()
            staging.rmdir()
        raise
    return final_dir, manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Select an exact balanced 3,000-record immutable training pool from "
            "quality-v2 judged candidates"
        )
    )
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        help="quality-v2 judged gold_candidate JSON/JSONL; repeatable",
    )
    parser.add_argument(
        "--public-s3-training-artifact",
        action="append",
        required=True,
        help=(
            "committed public-real S3 training artifact directory; raw JSONL is "
            "rejected; repeat only for disjoint artifacts"
        ),
    )
    parser.add_argument(
        "--frozen-primary",
        action="append",
        required=True,
        help="frozen primary evaluation corpus; repeatable exclusion boundary",
    )
    parser.add_argument(
        "--blocked-public-holdout",
        action="append",
        required=True,
        help=(
            "immutable public S3 challenge artifact directory; repeat for v1/v2; "
            "always blocked from training"
        ),
    )
    parser.add_argument(
        "--catalog",
        default="datasets/proxy_gold/training_scenario_catalog.v1.json",
        help="train-only factor-profile catalog that binds exact scenario quotas",
    )
    parser.add_argument("--out-root", default="datasets/proxy_gold/training_pool_runs")
    parser.add_argument("--run-id", help="optional unique immutable run id")
    args = parser.parse_args(argv)
    try:
        run_dir, manifest = assemble_training_pool_run(
            input_paths=[Path(value) for value in args.input],
            public_training_paths=[
                Path(value) for value in args.public_s3_training_artifact
            ],
            frozen_primary_paths=[Path(value) for value in args.frozen_primary],
            blocked_public_paths=[Path(value) for value in args.blocked_public_holdout],
            out_root=Path(args.out_root),
            run_id=args.run_id,
            catalog_path=Path(args.catalog),
        )
    except (
        CorpusLoadError,
        ChallengeAssemblyError,
        PublicTrainingAssemblyError,
        TrainingPoolAssemblyError,
        ValueError,
    ) as exc:
        raise SystemExit(f"training-pool assembly failed: {exc}") from exc
    print(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "run_id": manifest["run_id"],
                "records": manifest["artifact"]["records"],
                "sha256": manifest["artifact"]["sha256"],
                "complete": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
