"""Immutable proxy-training materialization tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import materialize_proxy_training_set as materializer
from lloydk.proxy_corpus import ProxyRecordCheck


GRADES = ("TS", "S1", "S2", "S3")


def _rows(family_count: int, *, prefix: str = "train") -> list[dict]:
    return [
        {
            "doc_id": f"{prefix}-{family_index:03d}-{grade}",
            "document_family_id": f"{prefix}-family-{family_index:03d}",
            "text": f"{prefix} unique document {family_index:03d} {grade}",
            "label": grade,
        }
        for family_index in range(family_count)
        for grade in GRADES
    ]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _bypass_proxy_content_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        materializer,
        "_validate_eligible_records",
        lambda records, *, purpose, intended_use: None,
    )


def _evaluation_only_public_record() -> dict:
    text = "\n\n".join(
        [
            "산업 현장의 에너지 효율 개선 사업은 설비별 전력 사용량과 가동 시간을 함께 측정해 기준선을 정했다. 조사팀은 열두 개 사업장에서 삼십 분 간격으로 수집한 값을 비교하고, 계절 변화와 생산량 차이를 보정한 결과를 표와 설명으로 남겼다.",
            "시범 운영에서는 압축기 압력과 냉각수 온도를 순차적으로 조정했다. 첫 주에는 기존 조건을 유지하고 둘째 주에는 제어 범위를 좁혀 품질 편차를 살폈다. 생산 수율은 안정적으로 유지됐으며 최대 수요 전력은 이전 기간보다 낮아졌다.",
            "현장 담당자는 센서 오류와 정비 시간을 별도 사건으로 기록했다. 분석자는 누락 구간을 임의로 채우지 않고 해당 기간을 제외한 결과와 포함한 결과를 나란히 제시했다. 이 절차는 기관이 공개한 조사 지침과 계산 기준을 그대로 따른 것이다.",
            "향후 계획은 지역별 기후 조건을 반영한 비교 실험과 장기 성능 점검을 포함한다. 참여 기관은 원자료의 공개 범위, 통계 산식, 변경 이력을 문서화하고 분기마다 성과를 검토한다. 최종 보고서는 누구나 확인할 수 있는 공개 정책 자료로 제공된다.",
        ]
    )
    return {
        "doc_id": "frozen-evaluation-only-001",
        "document_family_id": "frozen-public-family-001",
        "text": text,
        "label": "S3",
        "document_origin": "public_real",
        "proxy_role": "public_document",
        "document_type": "public_research_report",
        "source_id": "public-policy-unit-test",
        "source_reference": "https://example.invalid/public-policy-unit-test",
        "source_license": "KOGL-1",
        "source_sha256": "a" * 64,
        "license_evidence_sha256": "b" * 64,
        "retrieved_at": "2026-08-08T00:00:00+09:00",
        "training_use_permitted": False,
        "evaluation_use_permitted": True,
    }


def test_family_split_is_deterministic_exclusive_and_grade_complete():
    rows = _rows(30)

    first = materializer.deterministic_family_split(rows)
    second = materializer.deterministic_family_split(list(reversed(rows)))

    assert {
        name: [row["doc_id"] for row in split_rows]
        for name, split_rows in first.items()
    } == {
        name: [row["doc_id"] for row in split_rows]
        for name, split_rows in second.items()
    }
    families = {
        name: {row["document_family_id"] for row in split_rows}
        for name, split_rows in first.items()
    }
    assert families["train"].isdisjoint(families["validation"])
    assert families["train"].isdisjoint(families["calibration"])
    assert families["validation"].isdisjoint(families["calibration"])
    assert {name: len(split_rows) for name, split_rows in first.items()} == {
        "train": 96,
        "validation": 12,
        "calibration": 12,
    }
    assert all(
        {row["label"] for row in split_rows} == set(GRADES)
        for split_rows in first.values()
    )


def test_family_split_keeps_every_factor_profile_in_validation_and_calibration():
    rows = _rows(9)
    for row in rows:
        family_index = int(row["document_family_id"].rsplit("-", 1)[1])
        row["factor_profile_id"] = f"profile-{family_index % 3}"
    splits = materializer.deterministic_family_split(rows)
    assert all(
        {row["factor_profile_id"] for row in split_rows}
        == {"profile-0", "profile-1", "profile-2"}
        for split_rows in splits.values()
    )


def test_chunk_validation_rejects_lost_factor_profile_metadata():
    source = {
        "doc_id": "doc-1",
        "document_family_id": "family-1",
        "label": "S2",
        "scenario_id": "scenario-1",
        "factor_profile_id": "s2-s1-v1-m1",
        "expected_factor_scores": {"secrecy": 1, "value": 1, "management": 1},
    }
    chunk = {
        **source,
        "source_doc_id": "doc-1",
        "chunk_id": "doc-1:0",
        "factor_profile_id": "s2-s1-v1-m2",
    }
    with pytest.raises(
        materializer.TrainingMaterializationError,
        match="changed source factor profile",
    ):
        materializer._validate_train_chunks([source], [chunk])


def test_split_fails_when_a_grade_has_fewer_than_three_families():
    rows = _rows(3)
    rows = [
        row
        for row in rows
        if row["label"] != "TS" or row["document_family_id"].endswith("000")
    ]

    with pytest.raises(
        materializer.TrainingMaterializationError, match="grade TS needs at least 3"
    ):
        materializer.deterministic_family_split(rows)


@pytest.mark.parametrize("overlap_kind", ["family", "text"])
def test_frozen_overlap_fails_closed_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overlap_kind: str,
):
    _bypass_proxy_content_validation(monkeypatch)
    training = _rows(3)
    frozen = {
        "doc_id": "frozen-1",
        "document_family_id": "frozen-family",
        "text": "frozen unique text",
        "label": "S3",
    }
    if overlap_kind == "family":
        frozen["document_family_id"] = training[0]["document_family_id"]
    else:
        frozen["text"] = training[0]["text"]
    input_path = tmp_path / "input.jsonl"
    frozen_path = tmp_path / "frozen.jsonl"
    _write_jsonl(input_path, training)
    _write_jsonl(frozen_path, [frozen])
    out_root = tmp_path / "runs"

    with pytest.raises(
        materializer.TrainingMaterializationError, match="overlaps frozen/blocked"
    ):
        materializer.materialize_proxy_training_set(
            input_paths=[input_path],
            frozen_paths=[frozen_path],
            out_root=out_root,
            expected_count=len(training),
            expected_frozen_count=1,
        )
    assert not out_root.exists()


def test_ineligible_judge_output_fails_closed(tmp_path: Path):
    training_path = tmp_path / "training.jsonl"
    frozen_path = tmp_path / "frozen.jsonl"
    _write_jsonl(
        training_path,
        [
            {
                "doc_id": "candidate",
                "document_family_id": "family-a",
                "text": "short",
                "label": "TS",
            }
        ],
    )
    _write_jsonl(
        frozen_path,
        [
            {
                "doc_id": "frozen",
                "document_family_id": "family-b",
                "text": "shorter",
                "label": "S3",
            }
        ],
    )

    with pytest.raises(
        materializer.TrainingMaterializationError, match="ineligible records"
    ):
        materializer.materialize_proxy_training_set(
            input_paths=[training_path],
            frozen_paths=[frozen_path],
            out_root=tmp_path / "runs",
            expected_count=1,
            expected_frozen_count=1,
        )


def test_evaluation_only_public_record_is_accepted_as_frozen_not_training(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    training = _rows(30)
    frozen = _evaluation_only_public_record()
    input_path = tmp_path / "training.jsonl"
    frozen_path = tmp_path / "frozen.jsonl"
    _write_jsonl(input_path, training)
    _write_jsonl(frozen_path, [frozen])

    real_validate = materializer.validate_proxy_record
    seen_uses: list[tuple[str, str]] = []

    def route_validation(record, *, stage, intended_use):
        doc_id = str(record.get("doc_id") or "")
        seen_uses.append((doc_id, intended_use))
        if doc_id.startswith("train-"):
            return ProxyRecordCheck(doc_id=doc_id, errors=())
        return real_validate(record, stage=stage, intended_use=intended_use)

    monkeypatch.setattr(materializer, "validate_proxy_record", route_validation)
    monkeypatch.setattr(
        materializer,
        "expand_records_evidence_aware",
        lambda records: [
            {
                **dict(row),
                "source_doc_id": row["doc_id"],
                "chunk_id": f"{row['doc_id']}:chunk-0000",
            }
            for row in records
        ],
    )

    run_dir, manifest = materializer.materialize_proxy_training_set(
        input_paths=[input_path],
        frozen_paths=[frozen_path],
        out_root=tmp_path / "runs",
        run_id="evaluation-only-frozen",
        expected_count=len(training),
        expected_frozen_count=1,
    )

    assert run_dir.is_dir()
    assert (frozen["doc_id"], "evaluation") in seen_uses
    assert all(
        intended_use == "training"
        for doc_id, intended_use in seen_uses
        if doc_id.startswith("train-")
    )
    assert manifest["contract"]["training_validation_intended_use"] == "training"
    assert manifest["contract"]["frozen_validation_intended_use"] == "evaluation"


def test_missing_or_empty_declared_input_fails_closed(tmp_path: Path):
    frozen_path = tmp_path / "frozen.jsonl"
    empty_path = tmp_path / "empty.jsonl"
    _write_jsonl(
        frozen_path,
        [
            {
                "doc_id": "frozen",
                "document_family_id": "family-b",
                "text": "body",
                "label": "S3",
            }
        ],
    )
    empty_path.write_text("\n", encoding="utf-8")

    with pytest.raises(materializer.CorpusLoadError, match="does not exist"):
        materializer.materialize_proxy_training_set(
            input_paths=[tmp_path / "missing.jsonl"],
            frozen_paths=[frozen_path],
            out_root=tmp_path / "runs",
            expected_count=1,
            expected_frozen_count=1,
        )
    with pytest.raises(materializer.CorpusLoadError, match="empty"):
        materializer.materialize_proxy_training_set(
            input_paths=[empty_path],
            frozen_paths=[frozen_path],
            out_root=tmp_path / "runs",
            expected_count=1,
            expected_frozen_count=1,
        )


def test_chunk_output_cannot_reference_a_non_train_document():
    train = [
        {
            "doc_id": "train-1",
            "document_family_id": "train-family",
            "text": "train text",
            "label": "S3",
        }
    ]
    contaminated = [
        {
            "doc_id": "validation-1",
            "source_doc_id": "validation-1",
            "chunk_id": "validation-1:chunk-0000",
            "document_family_id": "validation-family",
            "text": "validation text",
            "label": "S3",
        }
    ]

    with pytest.raises(
        materializer.TrainingMaterializationError, match="non-train document"
    ):
        materializer._validate_train_chunks(train, contaminated)


def test_successful_run_is_atomic_audited_and_chunks_train_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _bypass_proxy_content_validation(monkeypatch)
    training = _rows(30)
    frozen = _rows(1, prefix="frozen")
    input_path = tmp_path / "training.jsonl"
    frozen_path = tmp_path / "frozen.json"
    _write_jsonl(input_path, training)
    frozen_path.write_text(json.dumps(frozen), encoding="utf-8")
    captured_train_doc_ids: set[str] = set()

    def fake_expand(records):
        captured_train_doc_ids.update(str(row["doc_id"]) for row in records)
        return [
            {
                **dict(row),
                "source_doc_id": row["doc_id"],
                "chunk_id": f"{row['doc_id']}:chunk-0000",
                "chunk_index": 0,
            }
            for row in records
        ]

    monkeypatch.setattr(materializer, "expand_records_evidence_aware", fake_expand)
    run_dir, returned_manifest = materializer.materialize_proxy_training_set(
        input_paths=[input_path],
        frozen_paths=[frozen_path],
        out_root=tmp_path / "runs",
        run_id="unit-run-001",
        expected_count=len(training),
        expected_frozen_count=len(frozen),
    )

    assert run_dir.name == "unit-run-001"
    assert sorted(path.name for path in run_dir.iterdir()) == [
        "COMPLETE",
        "calibration_documents.jsonl",
        "manifest.json",
        "train_chunks.jsonl",
        "train_documents.jsonl",
        "validation_documents.jsonl",
    ]
    assert not list(run_dir.parent.glob("*.staging-*"))
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    complete = json.loads((run_dir / "COMPLETE").read_text(encoding="utf-8"))
    assert returned_manifest["run_id"] == manifest["run_id"] == "unit-run-001"
    assert (
        complete["manifest_sha256"]
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )
    assert manifest["actual_ratios"] == {
        "train": 0.8,
        "validation": 0.1,
        "calibration": 0.1,
    }
    assert (
        manifest["inputs"]["training"]["loaded_files"][0]["sha256"]
        == hashlib.sha256(input_path.read_bytes()).hexdigest()
    )
    assert manifest["inputs"]["frozen"]["row_count"] == 4
    assert manifest["leakage_checks"]["frozen_records_in_splits"] == 0

    train_docs = _read_jsonl(run_dir / "train_documents.jsonl")
    validation_docs = _read_jsonl(run_dir / "validation_documents.jsonl")
    calibration_docs = _read_jsonl(run_dir / "calibration_documents.jsonl")
    chunks = _read_jsonl(run_dir / "train_chunks.jsonl")
    assert captured_train_doc_ids == {row["doc_id"] for row in train_docs}
    assert {row["source_doc_id"] for row in chunks} == captured_train_doc_ids
    assert all("chunk_id" not in row for row in validation_docs + calibration_docs)
    frozen_ids = {row["doc_id"] for row in frozen}
    assert frozen_ids.isdisjoint(
        {row["doc_id"] for row in train_docs + validation_docs + calibration_docs}
    )
    split_families = [
        {row["document_family_id"] for row in rows}
        for rows in (train_docs, validation_docs, calibration_docs)
    ]
    assert split_families[0].isdisjoint(split_families[1])
    assert split_families[0].isdisjoint(split_families[2])
    assert split_families[1].isdisjoint(split_families[2])
    for key, artifact in manifest["artifacts"].items():
        artifact_path = run_dir / artifact["path"]
        assert (
            hashlib.sha256(artifact_path.read_bytes()).hexdigest() == artifact["sha256"]
        ), key


def test_existing_run_directory_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _bypass_proxy_content_validation(monkeypatch)
    monkeypatch.setattr(
        materializer,
        "expand_records_evidence_aware",
        lambda records: [
            {
                **dict(row),
                "source_doc_id": row["doc_id"],
                "chunk_id": row["doc_id"],
            }
            for row in records
        ],
    )
    training = _rows(3)
    frozen = _rows(1, prefix="frozen")
    input_path = tmp_path / "training.jsonl"
    frozen_path = tmp_path / "frozen.jsonl"
    _write_jsonl(input_path, training)
    _write_jsonl(frozen_path, frozen)
    out_root = tmp_path / "runs"
    existing = out_root / "immutable-run"
    existing.mkdir(parents=True)
    sentinel = existing / "sentinel.txt"
    sentinel.write_text("preserve", encoding="utf-8")

    with pytest.raises(
        materializer.TrainingMaterializationError, match="already exists"
    ):
        materializer.materialize_proxy_training_set(
            input_paths=[input_path],
            frozen_paths=[frozen_path],
            out_root=out_root,
            run_id="immutable-run",
            expected_count=len(training),
            expected_frozen_count=len(frozen),
        )
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_production_3000_origin_label_mix_is_immutable():
    required = materializer._normalize_required_training_mix(
        expected_count=3_000,
        required_origin_label_counts=None,
    )

    assert required == materializer.EXPECTED_ORIGIN_LABEL_COUNTS
    with pytest.raises(
        materializer.TrainingMaterializationError,
        match="cannot be overridden",
    ):
        materializer._normalize_required_training_mix(
            expected_count=3_000,
            required_origin_label_counts={("synthetic", "S3"): 3_000},
        )


def _production_boundary_manifest() -> dict:
    zero_overlap = {
        "doc_id_overlap": 0,
        "document_family_id_overlap": 0,
        "normalized_text_hash_overlap": 0,
    }
    return {
        "contract": {
            "public_real_s3_target": 300,
            "frozen_primary_role": "evaluation_exclusion_only",
        },
        "inputs": {
            "frozen_primary": {
                "row_count": 1_000,
                "records_sha256": "a" * 64,
            },
            "public_s3_training": {"row_count": 300},
            "blocked_public_holdouts": {
                "artifact_count": 2,
                "row_count": 600,
                "records_sha256": "b" * 64,
                "artifacts": [
                    {
                        "manifest_schema": (
                            materializer.PUBLIC_HOLDOUT_MANIFEST_SCHEMA
                        ),
                        "manifest_sha256": "c" * 64,
                        "records_sha256": "d" * 64,
                        "records": 300,
                    },
                    {
                        "manifest_schema": (
                            materializer.PUBLIC_HOLDOUT_MANIFEST_SCHEMA
                        ),
                        "manifest_sha256": "e" * 64,
                        "records_sha256": "f" * 64,
                        "records": 300,
                    },
                ],
                "union_uniqueness": {
                    "unique_doc_ids": 600,
                    "unique_document_family_ids": 600,
                    "unique_normalized_text_hashes": 600,
                },
            },
        },
        "leakage_checks": {
            "frozen_primary_vs_attested_public_holdouts": dict(zero_overlap),
            "selected_vs_frozen_primary": dict(zero_overlap),
            "selected_vs_attested_public_holdouts": dict(zero_overlap),
        },
    }


def test_production_envelope_requires_complete_four_way_exclusion_contract():
    manifest = _production_boundary_manifest()
    boundary = materializer._production_exclusion_contract(manifest)

    assert boundary == {
        "expected_frozen_records": 1_000,
        "expected_frozen_records_sha256": "a" * 64,
        "expected_blocked_public_records": 600,
        "expected_blocked_public_records_sha256": "b" * 64,
        "expected_blocked_public_artifacts": 2,
        "expected_blocked_public_artifact_fingerprints": [
            {
                "manifest_schema": materializer.PUBLIC_HOLDOUT_MANIFEST_SCHEMA,
                "manifest_sha256": "c" * 64,
                "records_sha256": "d" * 64,
                "records": 300,
            },
            {
                "manifest_schema": materializer.PUBLIC_HOLDOUT_MANIFEST_SCHEMA,
                "manifest_sha256": "e" * 64,
                "records_sha256": "f" * 64,
                "records": 300,
            },
        ],
    }

    missing_check = json.loads(json.dumps(manifest))
    del missing_check["leakage_checks"]["frozen_primary_vs_attested_public_holdouts"]
    with pytest.raises(
        materializer.TrainingMaterializationError,
        match="missing or non-zero leakage checks",
    ):
        materializer._production_exclusion_contract(missing_check)

    contaminated = json.loads(json.dumps(manifest))
    contaminated["inputs"]["blocked_public_holdouts"]["union_uniqueness"][
        "unique_normalized_text_hashes"
    ] = 599
    with pytest.raises(
        materializer.TrainingMaterializationError,
        match="exclusion-boundary counts",
    ):
        materializer._production_exclusion_contract(contaminated)


def test_materializer_binds_frozen_and_public_exclusions_to_assembler_hashes():
    frozen = _rows(1, prefix="frozen")
    public = _rows(1, prefix="public")
    audit = {
        "expected_frozen_records": len(frozen),
        "expected_frozen_records_sha256": hashlib.sha256(
            materializer._canonical_record_bytes(frozen)
        ).hexdigest(),
        "expected_blocked_public_records": len(public),
        "expected_blocked_public_records_sha256": hashlib.sha256(
            materializer._canonical_record_bytes(public)
        ).hexdigest(),
    }
    materializer._verify_envelope_exclusion_inputs(
        audit, frozen=frozen, blocked_public=public
    )

    substituted = [dict(row) for row in public]
    substituted[0]["text"] += " changed"
    with pytest.raises(
        materializer.TrainingMaterializationError,
        match="exclusions differ from the assembled training pool",
    ):
        materializer._verify_envelope_exclusion_inputs(
            audit, frozen=frozen, blocked_public=substituted
        )


def test_parsed_training_rows_remain_bound_to_verified_envelope_bytes():
    records = _rows(3)
    audit = {
        "records_sha256": hashlib.sha256(materializer._jsonl_bytes(records)).hexdigest()
    }
    materializer._verify_loaded_training_matches_envelope(audit, records)

    substituted = [dict(row) for row in records]
    substituted[0]["text"] += " changed"
    with pytest.raises(
        materializer.TrainingMaterializationError,
        match="changed while its envelope was being loaded",
    ):
        materializer._verify_loaded_training_matches_envelope(audit, substituted)


def test_production_requires_the_same_number_of_attested_public_artifacts(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        materializer,
        "load_blocked_corpora",
        lambda paths: SimpleNamespace(
            files=({"records_path": "only-one/records.jsonl"},),
        ),
    )

    with pytest.raises(
        materializer.TrainingMaterializationError,
        match="artifact count differs from assembly",
    ):
        materializer._load_production_public_holdouts(
            [Path("only-one")],
            envelope_audit={"expected_blocked_public_artifacts": 2},
        )


def test_production_rejects_substituted_public_artifact_seals(
    monkeypatch: pytest.MonkeyPatch,
):
    actual_files = (
        {
            "records_path": "dev/records.jsonl",
            "manifest_schema": materializer.PUBLIC_HOLDOUT_MANIFEST_SCHEMA,
            "manifest_sha256": "1" * 64,
            "records_sha256": "2" * 64,
            "records": 300,
        },
        {
            "records_path": "blind/records.jsonl",
            "manifest_schema": materializer.PUBLIC_HOLDOUT_MANIFEST_SCHEMA,
            "manifest_sha256": "3" * 64,
            "records_sha256": "4" * 64,
            "records": 300,
        },
    )
    monkeypatch.setattr(
        materializer,
        "load_blocked_corpora",
        lambda paths: SimpleNamespace(files=actual_files),
    )
    expected = materializer._public_holdout_fingerprints(
        [
            {**actual_files[0], "manifest_sha256": "5" * 64},
            actual_files[1],
        ]
    )

    with pytest.raises(
        materializer.TrainingMaterializationError,
        match="artifact seals differ from assembly",
    ):
        materializer._load_production_public_holdouts(
            [Path("dev"), Path("blind")],
            envelope_audit={
                "expected_blocked_public_artifacts": 2,
                "expected_blocked_public_artifact_fingerprints": expected,
            },
        )


def test_training_mix_validation_rejects_origin_label_substitution():
    required = {
        ("synthetic", "TS"): 1,
        ("public_real", "S3"): 1,
    }
    valid = [
        {"document_origin": "synthetic", "label": "TS"},
        {"document_origin": "public_real", "label": "S3"},
    ]
    materializer._validate_training_mix(valid, required=required)

    substituted = [
        {"document_origin": "synthetic", "label": "TS"},
        {"document_origin": "synthetic", "label": "S3"},
    ]
    with pytest.raises(
        materializer.TrainingMaterializationError,
        match="origin/label mix",
    ):
        materializer._validate_training_mix(substituted, required=required)
