"""Partition a quality-gated frozen Proxy evaluation corpus without leakage.

The 1,000-document Proxy evaluation corpus is useful only if model selection
cannot see the final scorecard.  This module creates an exact 200-document
development partition and an exact 800-document final partition while keeping
matched document families atomic.  It is intentionally separate from training
splits: neither output may be supplied to a trainer or calibrator.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
import hashlib
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from lloydk.hygiene import text_hash


GRADE_ORDER = ("TS", "S1", "S2", "S3")
FROZEN_GRADE_COUNTS = {"TS": 200, "S1": 250, "S2": 250, "S3": 300}
DEVELOPMENT_GRADE_COUNTS = {"TS": 40, "S1": 50, "S2": 50, "S3": 60}
FINAL_GRADE_COUNTS = {
    grade: FROZEN_GRADE_COUNTS[grade] - DEVELOPMENT_GRADE_COUNTS[grade]
    for grade in GRADE_ORDER
}
PARTITION_POLICY = "frozen-proxy-eval-dev200-final800-v1"


class FrozenEvalSplitError(ValueError):
    """The proposed evaluation corpus cannot be safely partitioned."""


@dataclass(frozen=True)
class FrozenEvalSplit:
    development: tuple[dict[str, object], ...]
    final: tuple[dict[str, object], ...]
    audit: dict[str, object]


def _required_string(record: Mapping[str, object], field: str) -> str:
    value = str(record.get(field) or "").strip()
    if not value:
        raise FrozenEvalSplitError(f"missing {field} for {record.get('doc_id')!r}")
    return value


def _stable_cost(family_id: str) -> float:
    # A deterministic, unique-ish objective makes a valid partition repeatable.
    return int(hashlib.sha256(family_id.encode("utf-8")).hexdigest()[:14], 16) / 1e14


def _grade_counts(records: Sequence[Mapping[str, object]]) -> dict[str, int]:
    return {grade: sum(row.get("label") == grade for row in records) for grade in GRADE_ORDER}


def _validate_records(records: Sequence[Mapping[str, object]]) -> None:
    if len(records) != sum(FROZEN_GRADE_COUNTS.values()):
        raise FrozenEvalSplitError(
            f"frozen corpus must contain exactly 1000 documents; found {len(records)}"
        )
    if _grade_counts(records) != FROZEN_GRADE_COUNTS:
        raise FrozenEvalSplitError(
            "frozen corpus grade distribution must be "
            f"{FROZEN_GRADE_COUNTS}; found {_grade_counts(records)}"
        )

    doc_ids: set[str] = set()
    text_hashes: set[str] = set()
    for record in records:
        doc_id = _required_string(record, "doc_id")
        _required_string(record, "document_family_id")
        text = _required_string(record, "text")
        if doc_id in doc_ids:
            raise FrozenEvalSplitError(f"duplicate doc_id: {doc_id}")
        digest = text_hash(text)
        if digest in text_hashes:
            raise FrozenEvalSplitError(f"duplicate text hash: {doc_id}")
        doc_ids.add(doc_id)
        text_hashes.add(digest)
        if record.get("document_origin") != "synthetic":
            raise FrozenEvalSplitError(f"frozen primary must remain synthetic: {doc_id}")
        if record.get("catalog_split_role") != "frozen_proxy_eval_only":
            raise FrozenEvalSplitError(f"invalid catalog split role: {doc_id}")
        if record.get("training_use_permitted") is not False:
            raise FrozenEvalSplitError(f"training must be forbidden: {doc_id}")
        if record.get("evaluation_use_permitted") is not True:
            raise FrozenEvalSplitError(f"evaluation permission missing: {doc_id}")


def _select_development_families(
    families: Mapping[str, Sequence[Mapping[str, object]]],
) -> set[str]:
    family_ids = sorted(families)
    matrix = np.array(
        [
            [sum(record.get("label") == grade for record in families[family]) for family in family_ids]
            for grade in GRADE_ORDER
        ],
        dtype=float,
    )
    target = np.array([DEVELOPMENT_GRADE_COUNTS[grade] for grade in GRADE_ORDER], dtype=float)
    result = milp(
        c=np.array([_stable_cost(family) for family in family_ids], dtype=float),
        integrality=np.ones(len(family_ids)),
        bounds=Bounds(np.zeros(len(family_ids)), np.ones(len(family_ids))),
        constraints=LinearConstraint(matrix, target, target),
        options={"presolve": True},
    )
    if not result.success or result.x is None:
        raise FrozenEvalSplitError(
            "cannot select an exact family-separated 200-document development set"
        )
    selected = {family_ids[index] for index, value in enumerate(result.x) if value >= 0.5}
    actual = Counter(
        str(record.get("label"))
        for family in selected
        for record in families[family]
    )
    if dict(actual) != DEVELOPMENT_GRADE_COUNTS:
        raise FrozenEvalSplitError(
            "MILP result does not meet exact development grade targets: "
            f"{dict(actual)}"
        )
    return selected


def split_frozen_proxy_eval(records: Sequence[Mapping[str, object]]) -> FrozenEvalSplit:
    """Create exact, family-exclusive development and final score partitions."""
    normalized = [dict(record) for record in records]
    _validate_records(normalized)
    by_family: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in normalized:
        by_family[str(record["document_family_id"])].append(record)

    development_families = _select_development_families(by_family)
    development: list[dict[str, object]] = []
    final: list[dict[str, object]] = []
    for family_id, rows in by_family.items():
        partition = "development" if family_id in development_families else "final_locked"
        target = development if partition == "development" else final
        target.extend(
            {
                **record,
                "evaluation_partition": partition,
                "evaluation_partition_policy": PARTITION_POLICY,
            }
            for record in rows
        )
    development.sort(key=lambda row: str(row["doc_id"]))
    final.sort(key=lambda row: str(row["doc_id"]))

    if _grade_counts(development) != DEVELOPMENT_GRADE_COUNTS:
        raise FrozenEvalSplitError("development count changed after partitioning")
    if _grade_counts(final) != FINAL_GRADE_COUNTS:
        raise FrozenEvalSplitError("final count changed after partitioning")
    development_families_check = {str(row["document_family_id"]) for row in development}
    final_families = {str(row["document_family_id"]) for row in final}
    if development_families_check & final_families:
        raise FrozenEvalSplitError("document family leakage across evaluation partitions")

    audit: dict[str, Any] = {
        "schema": "frozen-proxy-eval-split-audit-v1",
        "partition_policy": PARTITION_POLICY,
        "source_documents": len(normalized),
        "source_grade_counts": _grade_counts(normalized),
        "development_documents": len(development),
        "development_grade_counts": _grade_counts(development),
        "final_documents": len(final),
        "final_grade_counts": _grade_counts(final),
        "development_families": len(development_families_check),
        "final_families": len(final_families),
        "family_overlap": 0,
        "source_text_sha256": hashlib.sha256(
            "".join(sorted(text_hash(str(row["text"])) for row in normalized)).encode("utf-8")
        ).hexdigest(),
    }
    return FrozenEvalSplit(tuple(development), tuple(final), audit)
