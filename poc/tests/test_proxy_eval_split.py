from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

import pytest

from koipa.proxy_eval_split import (
    DEVELOPMENT_GRADE_COUNTS,
    FINAL_GRADE_COUNTS,
    FROZEN_GRADE_COUNTS,
    FrozenEvalSplitError,
    split_frozen_proxy_eval,
)
from koipa.proxy_model_comparison import (
    ProxyComparisonError,
    load_final_locked_proxy_eval,
)


def _records() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for grade, count in FROZEN_GRADE_COUNTS.items():
        for index in range(count):
            rows.append(
                {
                    "doc_id": f"{grade}-{index:03d}",
                    "document_family_id": f"{grade}-family-{index:03d}",
                    "text": f"unique frozen proxy document {grade} {index}",
                    "label": grade,
                    "document_origin": "synthetic",
                    "catalog_split_role": "frozen_proxy_eval_only",
                    "training_use_permitted": False,
                    "evaluation_use_permitted": True,
                }
            )
    return rows


def _counts(rows: tuple[dict[str, object], ...]) -> dict[str, int]:
    return dict(Counter(str(row["label"]) for row in rows))


def test_exact_development_and_final_counts_are_family_exclusive():
    result = split_frozen_proxy_eval(_records())
    assert len(result.development) == 200
    assert len(result.final) == 800
    assert _counts(result.development) == DEVELOPMENT_GRADE_COUNTS
    assert _counts(result.final) == FINAL_GRADE_COUNTS
    dev_families = {row["document_family_id"] for row in result.development}
    final_families = {row["document_family_id"] for row in result.final}
    assert dev_families.isdisjoint(final_families)
    assert {row["evaluation_partition"] for row in result.development} == {"development"}
    assert {row["evaluation_partition"] for row in result.final} == {"final_locked"}


def test_partition_is_deterministic():
    first = split_frozen_proxy_eval(_records())
    second = split_frozen_proxy_eval(list(reversed(_records())))
    assert [row["doc_id"] for row in first.development] == [row["doc_id"] for row in second.development]


def test_matched_multi_grade_families_stay_atomic():
    rows = _records()
    for index, row in enumerate(rows):
        row["document_family_id"] = f"matched-family-{index % 250:03d}"
    result = split_frozen_proxy_eval(rows)
    dev_families = {row["document_family_id"] for row in result.development}
    final_families = {row["document_family_id"] for row in result.final}
    assert dev_families.isdisjoint(final_families)
    assert _counts(result.development) == DEVELOPMENT_GRADE_COUNTS


def test_training_eligible_record_fails_closed():
    rows = _records()
    rows[0]["training_use_permitted"] = True
    with pytest.raises(FrozenEvalSplitError, match="training must be forbidden"):
        split_frozen_proxy_eval(rows)


def test_final_loader_rejects_development_partition_and_accepts_final(
    tmp_path: Path,
):
    split = split_frozen_proxy_eval(_records())
    final_path = tmp_path / "final_800.locked.jsonl"
    final_bytes = b"".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
        for row in split.final
    )
    final_path.write_bytes(final_bytes)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                **split.audit,
                "final_sha256": hashlib.sha256(final_bytes).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    loaded, audit = load_final_locked_proxy_eval(final_path, manifest_path)
    assert len(loaded) == 800
    assert audit["partition"] == "final_locked"

    dev_path = tmp_path / "development_200.jsonl"
    dev_path.write_text("\n".join(json.dumps(row) for row in split.development), encoding="utf-8")
    with pytest.raises(ProxyComparisonError, match="SHA-256"):
        load_final_locked_proxy_eval(dev_path, manifest_path)
