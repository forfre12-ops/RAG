"""Exact immutable proxy-training pool assembly tests."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import assemble_proxy_training_pool as assembler
from scripts import materialize_proxy_training_set as materializer
from scripts.build_proxy_scenarios import (
    expand_catalog_scenarios,
    generation_plan,
    generation_target_maps,
)
from koipa.hygiene import text_hash
from koipa.proxy_corpus import ProxyRecordCheck


GRADES = ("TS", "S1", "S2", "S3")
SHAPES = (
    ("shape-compact", "compact"),
    ("shape-standard", "standard"),
    ("shape-extended", "extended"),
)


def test_default_mixed_pool_contract_is_exactly_3000():
    assert assembler.DEFAULT_FINAL_TARGETS == {
        "TS": 750,
        "S1": 750,
        "S2": 750,
        "S3": 750,
    }
    assert assembler.DEFAULT_SYNTHETIC_TARGETS == {
        "TS": 750,
        "S1": 750,
        "S2": 750,
        "S3": 450,
    }
    assert sum(assembler.DEFAULT_SYNTHETIC_TARGETS.values()) == 2_700
    assert assembler.DEFAULT_PUBLIC_REAL_S3_TARGET == 300
    assert sum(assembler.DEFAULT_FINAL_TARGETS.values()) == 3_000


def _candidate(
    grade: str,
    family: str,
    shape: str,
    length_profile: str,
    ordinal: int,
) -> dict:
    text = f"unique judged proxy {grade} {family} {shape} {ordinal} " + ("가" * 40)
    return {
        "doc_id": f"candidate-{grade}-{family}-{shape}-{ordinal}",
        "document_family_id": family,
        "family_profile_id": shape,
        "length_profile_id": length_profile,
        "document_type": shape,
        "text": text,
        "label": grade,
        "document_origin": "synthetic",
        "proxy_role": "confidential_simulation",
        "catalog_split_role": "train_pool_only",
        "training_use_permitted": True,
        "evaluation_use_permitted": False,
        "decision_bucket": "gold_candidate",
        "gate_version": "proxy_semantic_quality_v2",
        "evidence_card": {"schema": "proxy-evidence-v1"},
        "consensus_evidence": {
            "schema": "proxy-semantic-quality-adjudication-v2",
            "semantic_gate_passed": True,
            "semantic_gate_failures": [],
            "document_quality_gate_passed": True,
            "document_quality_gate_failures": [],
            "quality_check_passed": {
                "structure_appropriate": True,
                "timeline_consistent": True,
                "quantitative_consistent": True,
                "non_repetitive": True,
            },
        },
        "requested_profile_min_chars": 1,
        "requested_profile_max_chars": 10_000,
    }


def _dense_candidates(*, families: int = 3, repetitions: int = 2) -> list[dict]:
    return [
        _candidate(grade, f"family-{family}", shape, length, ordinal)
        for grade in GRADES
        for family in range(families)
        for shape, length in SHAPES
        for ordinal in range(repetitions)
    ]


def _holdout(
    doc_id: str,
    family: str,
    text: str,
    *,
    origin: str = "synthetic",
) -> dict:
    row = {
        "doc_id": doc_id,
        "document_family_id": family,
        "text": text,
        "label": "S3",
        "document_origin": origin,
        "evaluation_use_permitted": True,
    }
    if origin == "synthetic":
        row.update(
            {
                "catalog_split_role": "frozen_proxy_eval_only",
                "training_use_permitted": False,
            }
        )
    return row


def _allow_content_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        assembler,
        "validate_proxy_record",
        lambda record, *, stage, intended_use: ProxyRecordCheck(
            doc_id=str(record.get("doc_id") or ""), errors=()
        ),
    )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_selection_is_deterministic_and_balanced_on_every_required_axis(
    monkeypatch: pytest.MonkeyPatch,
):
    _allow_content_validation(monkeypatch)
    candidates = _dense_candidates()
    kwargs = {
        "blocked_records": [],
        "final_targets": {grade: 6 for grade in GRADES},
        "public_real_s3_target": 0,
        "expected_document_shapes": 3,
        "expected_length_profiles": 3,
        "expected_families": 3,
    }

    first = assembler.select_training_pool(candidates, **kwargs)
    second = assembler.select_training_pool(reversed(candidates), **kwargs)

    assert [row["doc_id"] for row in first.selected] == [
        row["doc_id"] for row in second.selected
    ]
    assert len(first.selected) == 24
    for grade in GRADES:
        rows = [row for row in first.selected if row["label"] == grade]
        assert len(rows) == 6
        assert Counter(row["family_profile_id"] for row in rows) == {
            "shape-compact": 2,
            "shape-standard": 2,
            "shape-extended": 2,
        }
        assert Counter(row["length_profile_id"] for row in rows) == {
            "compact": 2,
            "standard": 2,
            "extended": 2,
        }
        assert set(Counter(row["document_family_id"] for row in rows).values()) == {2}


def test_namespaced_topup_candidates_are_accepted_without_duplicate_doc_ids(
    monkeypatch: pytest.MonkeyPatch,
):
    _allow_content_validation(monkeypatch)
    initial = _dense_candidates(repetitions=1)
    topup = []
    for index, row in enumerate(initial):
        candidate = _candidate(
            str(row["label"]),
            str(row["document_family_id"]),
            str(row["family_profile_id"]),
            str(row["length_profile_id"]),
            10_000 + index,
        )
        candidate["doc_id"] = f"proxy-topup-namespace-{index:04d}"
        topup.append(candidate)

    result = assembler.select_training_pool(
        [*initial, *topup],
        blocked_records=[],
        final_targets={grade: 6 for grade in GRADES},
        public_real_s3_target=0,
        expected_document_shapes=3,
        expected_length_profiles=3,
        expected_families=3,
    )

    selected_ids = [str(row["doc_id"]) for row in result.selected]
    assert len(selected_ids) == len(set(selected_ids)) == 24


def test_scenario_and_profile_quota_survive_balanced_selection_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
):
    _allow_content_validation(monkeypatch)
    candidates = _dense_candidates()
    scenario_targets: dict[str, int] = {}
    scenario_grades: dict[str, str] = {}
    scenario_profiles: dict[str, str] = {}
    for row in candidates:
        suffix = "a" if row["doc_id"].endswith("-0") else "b"
        scenario = f"scenario-{row['label']}-{suffix}"
        profile = f"profile-{row['label']}-{suffix}"
        row["scenario_id"] = scenario
        row["factor_profile_id"] = profile
        scenario_targets[scenario] = 3
        scenario_grades[scenario] = row["label"]
        scenario_profiles[scenario] = profile

    kwargs = {
        "blocked_records": [],
        "final_targets": {grade: 6 for grade in GRADES},
        "public_real_s3_target": 0,
        "expected_document_shapes": 3,
        "expected_length_profiles": 3,
        "expected_families": 3,
        "scenario_targets": scenario_targets,
        "scenario_target_grades": scenario_grades,
        "scenario_factor_profiles": scenario_profiles,
    }
    selected = assembler.select_training_pool(candidates, **kwargs)
    assert selected.audit["synthetic_selected_by_scenario"] == scenario_targets
    assert selected.audit["synthetic_selected_by_factor_profile"] == {
        profile: 3 for profile in sorted(set(scenario_profiles.values()))
    }

    without_ts_b = [
        row
        for row in candidates
        if row["scenario_id"] != "scenario-TS-b"
    ]
    with pytest.raises(
        assembler.TrainingPoolAssemblyError,
        match="shortfalls_by_factor_profile",
    ) as raised:
        assembler.select_training_pool(without_ts_b, **kwargs)
    assert "profile-TS-b" in str(raised.value)
    assert "scenario-TS-b" in str(raised.value)


def test_training_catalog_2x_candidates_select_exact_2700_joint_contract(
    monkeypatch: pytest.MonkeyPatch,
):
    """Exercise the production-size 315-scenario contract, not a toy margin."""
    _allow_content_validation(monkeypatch)
    catalog_path = (
        Path(__file__).resolve().parents[1]
        / "datasets/proxy_gold/training_scenario_catalog.v1.json"
    )
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    scenarios = expand_catalog_scenarios(catalog)
    plan = generation_plan(
        scenarios,
        catalog["instance_profiles"],
        catalog["family_profiles"],
        per_scenario=None,
        count_multiplier=2.0,
    )
    scenario_targets, grade_targets, candidate_targets, candidate_grades = (
        generation_target_maps(
            scenarios,
            per_scenario=None,
            candidate_buffer_factor=2.0,
        )
    )
    assert len(plan) == 5_400
    assert sum(scenario_targets.values()) == 2_700
    assert sum(candidate_targets.values()) == 5_400
    assert grade_targets == {"S1": 750, "S2": 750, "S3": 450, "TS": 750}
    assert candidate_grades == {
        "S1": 1_500,
        "S2": 1_500,
        "S3": 900,
        "TS": 1_500,
    }

    candidates: list[dict] = []
    for ordinal, (scenario, instance, shape, _) in enumerate(plan):
        row = _candidate(
            str(scenario["label"]),
            f"{scenario['document_family_id']}:{instance['instance_profile_id']}",
            str(shape["family_profile_id"]),
            str(shape["length_profile_id"]),
            ordinal,
        )
        row.update(
            {
                "scenario_id": scenario["scenario_id"],
                "factor_profile_id": scenario["factor_profile_id"],
                "expected_factor_scores": scenario["expected_factor_scores"],
            }
        )
        candidates.append(row)

    scenario_grades = {
        str(scenario["scenario_id"]): str(scenario["label"])
        for scenario in scenarios
    }
    scenario_profiles = {
        str(scenario["scenario_id"]): str(scenario["factor_profile_id"])
        for scenario in scenarios
    }
    kwargs = {
        "blocked_records": [],
        "final_targets": {"TS": 750, "S1": 750, "S2": 750, "S3": 450},
        "public_real_s3_target": 0,
        "expected_document_shapes": 12,
        "expected_length_profiles": 3,
        "expected_families": 225,
        "scenario_targets": scenario_targets,
        "scenario_target_grades": scenario_grades,
        "scenario_factor_profiles": scenario_profiles,
    }
    first = assembler.select_training_pool(candidates, **kwargs)
    second = assembler.select_training_pool(reversed(candidates), **kwargs)

    first_ids = [str(row["doc_id"]) for row in first.selected]
    assert first_ids == [str(row["doc_id"]) for row in second.selected]
    assert len(first_ids) == len(set(first_ids)) == 2_700
    assert len({text_hash(str(row["text"])) for row in first.selected}) == 2_700
    assert Counter(str(row["scenario_id"]) for row in first.selected) == Counter(
        scenario_targets
    )
    expected_profiles = Counter()
    for scenario_id, count in scenario_targets.items():
        expected_profiles[scenario_profiles[scenario_id]] += count
    assert Counter(
        str(row["factor_profile_id"]) for row in first.selected
    ) == expected_profiles

    for grade, target in grade_targets.items():
        selected = [row for row in first.selected if row["label"] == grade]
        assert len(selected) == target
        family_counts = Counter(
            str(row["document_family_id"]) for row in selected
        )
        shape_counts = Counter(str(row["family_profile_id"]) for row in selected)
        length_counts = Counter(str(row["length_profile_id"]) for row in selected)
        assert len(family_counts) == 225
        assert max(family_counts.values()) - min(family_counts.values()) <= 1
        assert len(shape_counts) == 12
        assert max(shape_counts.values()) - min(shape_counts.values()) <= 1
        assert len(length_counts) == 3
        assert max(length_counts.values()) - min(length_counts.values()) <= 1

    splits = materializer.deterministic_family_split(first.selected)
    expected_profile_ids = set(expected_profiles)
    split_families = []
    for split_rows in splits.values():
        assert {
            str(row["factor_profile_id"]) for row in split_rows
        } == expected_profile_ids
        split_families.append(
            {str(row["document_family_id"]) for row in split_rows}
        )
    assert split_families[0].isdisjoint(split_families[1])
    assert split_families[0].isdisjoint(split_families[2])
    assert split_families[1].isdisjoint(split_families[2])


def test_permission_and_quality_v2_are_fail_closed_then_shortage_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    _allow_content_validation(monkeypatch)
    candidates = _dense_candidates()
    for row in candidates:
        if row["label"] == "TS":
            row["training_use_permitted"] = False
            row["consensus_evidence"]["document_quality_gate_passed"] = False

    with pytest.raises(
        assembler.TrainingPoolAssemblyError,
        match="eligible judged candidates are insufficient",
    ) as raised:
        assembler.select_training_pool(
            candidates,
            blocked_records=[],
            final_targets={grade: 6 for grade in GRADES},
            public_real_s3_target=0,
            expected_document_shapes=3,
            expected_length_profiles=3,
            expected_families=3,
        )

    message = str(raised.value)
    assert "training_use_permitted_not_true" in message
    assert "document_quality_gate_not_passed" in message


def test_training_candidates_and_frozen_rows_cannot_swap_catalog_roles(
    monkeypatch: pytest.MonkeyPatch,
):
    _allow_content_validation(monkeypatch)
    training_row = _candidate("TS", "family-1", "shape-compact", "compact", 0)
    training_row.update(
        {
            "catalog_split_role": "frozen_proxy_eval_only",
            "training_use_permitted": False,
            "evaluation_use_permitted": True,
        }
    )
    assert any(
        error.startswith("catalog_usage_contract:")
        for error in assembler._candidate_errors(training_row)
    )

    frozen_row = _holdout("frozen", "frozen-family", "frozen text")
    frozen_row.update(
        {
            "catalog_split_role": "train_pool_only",
            "training_use_permitted": True,
            "evaluation_use_permitted": False,
        }
    )
    with pytest.raises(
        assembler.TrainingPoolAssemblyError,
        match="catalog_usage_contract|evaluation_use_permitted_not_true",
    ):
        assembler._validate_holdout(
            [frozen_row], purpose="frozen primary", require_public=False
        )


def test_frozen_holdout_rejects_duplicate_normalized_text(
    monkeypatch: pytest.MonkeyPatch,
):
    _allow_content_validation(monkeypatch)
    rows = [
        _holdout("frozen-a", "frozen-family-a", "same normalized text"),
        _holdout("frozen-b", "frozen-family-b", "same   normalized\ntext"),
    ]

    with pytest.raises(
        assembler.TrainingPoolAssemblyError,
        match="duplicate_normalized_text_hash",
    ):
        assembler._validate_holdout(
            rows, purpose="frozen primary", require_public=False
        )


def test_doc_family_and_normalized_text_holdout_overlap_are_all_excluded(
    monkeypatch: pytest.MonkeyPatch,
):
    _allow_content_validation(monkeypatch)
    candidates = _dense_candidates(families=4)
    doc_overlap = next(
        row for row in candidates if row["document_family_id"] == "family-1"
    )
    text_overlap = next(
        row for row in candidates if row["document_family_id"] == "family-2"
    )
    blocked = [
        _holdout("blocked-family", "family-0", "external family boundary"),
        _holdout(
            str(doc_overlap["doc_id"]), "external-family-a", "external doc boundary"
        ),
        _holdout("blocked-text", "external-family-b", str(text_overlap["text"])),
    ]

    result = assembler.select_training_pool(
        candidates,
        blocked_records=blocked,
        final_targets={grade: 6 for grade in GRADES},
        public_real_s3_target=0,
        expected_document_shapes=3,
        expected_length_profiles=3,
        expected_families=3,
    )

    selected_ids = {str(row["doc_id"]) for row in result.selected}
    selected_families = {str(row["document_family_id"]) for row in result.selected}
    selected_hashes = {text_hash(row["text"]) for row in result.selected}
    assert str(doc_overlap["doc_id"]) not in selected_ids
    assert "family-0" not in selected_families
    assert text_hash(text_overlap["text"]) not in selected_hashes
    assert result.audit["leakage_checks"] == {
        "doc_id_overlap": 0,
        "document_family_id_overlap": 0,
        "normalized_text_hash_overlap": 0,
    }
    assert result.audit["holdout_overlap_reason_counts"] == {
        "doc_id": 1,
        "document_family_id": 24,
        "normalized_text": 1,
    }


def test_simultaneous_family_and_shape_infeasibility_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    _allow_content_validation(monkeypatch)
    candidates: list[dict] = []
    for grade in GRADES:
        candidates.extend(
            _candidate(grade, "family-0", "shape-compact", "compact", ordinal)
            for ordinal in range(2)
        )
        candidates.extend(
            _candidate(grade, "family-1", "shape-compact", "compact", ordinal)
            for ordinal in range(2)
        )
        for ordinal in range(2):
            candidates.append(
                _candidate(grade, "family-2", "shape-standard", "standard", ordinal)
            )
            candidates.append(
                _candidate(grade, "family-2", "shape-extended", "extended", ordinal)
            )

    with pytest.raises(
        assembler.TrainingPoolAssemblyError,
        match="simultaneous family/shape quotas",
    ):
        assembler.select_training_pool(
            candidates,
            blocked_records=[],
            final_targets={grade: 6 for grade in GRADES},
            public_real_s3_target=0,
            expected_document_shapes=3,
            expected_length_profiles=3,
            expected_families=3,
        )


def test_immutable_run_has_input_code_output_hashes_and_materializer_consumes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _allow_content_validation(monkeypatch)
    candidates = _dense_candidates()
    frozen = [
        _holdout(f"frozen-{index}", f"frozen-family-{index}", f"frozen text {index}")
        for index in range(4)
    ]
    public = [
        _holdout(
            "public-1",
            "public-family-1",
            "public diagnostic text",
            origin="public_real",
        )
    ]
    public_training = [
        {
            **_holdout(
                f"public-train-{index}",
                f"public-train-family-{index}",
                f"licensed public training text {index}",
                origin="public_real",
            ),
            "training_use_permitted": True,
        }
        for index in range(2)
    ]
    input_path = tmp_path / "gold_candidate.jsonl"
    frozen_path = tmp_path / "frozen.jsonl"
    public_path = tmp_path / "public300.jsonl"
    public_training_path = tmp_path / "public-training.jsonl"
    _write_jsonl(input_path, candidates)
    _write_jsonl(frozen_path, frozen)
    _write_jsonl(public_path, public)
    _write_jsonl(public_training_path, public_training)
    monkeypatch.setattr(
        assembler,
        "_load_attested_public_training_artifacts",
        lambda paths: (
            [dict(row) for row in public_training],
            {
                "artifact_count": 1,
                "row_count": len(public_training),
                "artifacts": [
                    {
                        "path": str(paths[0]),
                        "schema": "public-s3-training-pool-manifest-v1",
                    }
                ],
                "records_sha256": "a" * 64,
            },
        ),
    )
    monkeypatch.setattr(
        assembler,
        "_load_attested_public_holdouts",
        lambda paths, *, minimum_artifacts, minimum_records: (
            [dict(row) for row in public],
            {
                "artifact_count": minimum_artifacts,
                "row_count": len(public),
                "artifacts": [{"path": str(paths[0])}],
                "records_sha256": "b" * 64,
            },
        ),
    )

    run_dir, returned = assembler.assemble_training_pool_run(
        input_paths=[input_path],
        public_training_paths=[public_training_path],
        frozen_primary_paths=[frozen_path],
        blocked_public_paths=[public_path],
        out_root=tmp_path / "pool-runs",
        run_id="pool-unit-001",
        final_targets={grade: 6 for grade in GRADES},
        public_real_s3_target=2,
        expected_frozen_primary_count=4,
        expected_document_shapes=3,
        expected_length_profiles=3,
        expected_families=3,
        minimum_public_holdout_artifacts=1,
        minimum_public_holdout_records=1,
    )

    assert sorted(path.name for path in run_dir.iterdir()) == [
        "COMPLETE",
        "manifest.json",
        "training_pool.jsonl",
    ]
    manifest_path = run_dir / "manifest.json"
    pool_path = run_dir / "training_pool.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    complete = json.loads((run_dir / "COMPLETE").read_text(encoding="utf-8"))
    assert returned["run_id"] == manifest["run_id"] == "pool-unit-001"
    assert (
        complete["manifest_sha256"]
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )
    assert (
        complete["training_pool_sha256"]
        == hashlib.sha256(pool_path.read_bytes()).hexdigest()
    )
    assert (
        manifest["inputs"]["judged_candidates"]["loaded_files"][0]["sha256"]
        == hashlib.sha256(input_path.read_bytes()).hexdigest()
    )
    assert manifest["code"]["contract_sha256"] == complete["code_contract_sha256"]
    assert len(manifest["code"]["files"]) == 6
    assert manifest["inputs"]["public_s3_training"]["artifact_count"] == 1
    assert manifest["artifact"]["by_grade"] == {
        "S1": 6,
        "S2": 6,
        "S3": 6,
        "TS": 6,
    }
    assert manifest["artifact"]["by_origin"] == {
        "public_real": 2,
        "synthetic": 22,
    }
    assert manifest["artifact"]["by_document_shape"]["not_applicable"] == 2
    assert manifest["leakage_checks"] == {
        "frozen_primary_vs_attested_public_holdouts": {
            "doc_id_overlap": 0,
            "document_family_id_overlap": 0,
            "normalized_text_hash_overlap": 0,
        },
        "selected_vs_attested_public_holdouts": {
            "doc_id_overlap": 0,
            "document_family_id_overlap": 0,
            "normalized_text_hash_overlap": 0,
        },
        "selected_vs_frozen_primary": {
            "doc_id_overlap": 0,
            "document_family_id_overlap": 0,
            "normalized_text_hash_overlap": 0,
        },
    }
    assert len(_read_jsonl(pool_path)) == 24
    assert manifest["selection_audit"]["origin_counts"] == {
        "public_real": 2,
        "synthetic": 22,
    }
    assert manifest["selection_audit"]["origin_label_counts"] == {
        "public_real:S3": 2,
        "synthetic:S1": 6,
        "synthetic:S2": 6,
        "synthetic:S3": 4,
        "synthetic:TS": 6,
    }

    monkeypatch.setattr(
        materializer,
        "_validate_eligible_records",
        lambda records, *, purpose, intended_use: None,
    )
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
    required_mix = {
        ("synthetic", "TS"): 6,
        ("synthetic", "S1"): 6,
        ("synthetic", "S2"): 6,
        ("synthetic", "S3"): 4,
        ("public_real", "S3"): 2,
    }
    materialized_dir, materialized_manifest = (
        materializer.materialize_proxy_training_set(
            input_paths=[pool_path],
            frozen_paths=[frozen_path],
            out_root=tmp_path / "materialized-runs",
            run_id="materialized-unit-001",
            expected_count=24,
            expected_frozen_count=4,
            required_origin_label_counts=required_mix,
            require_training_pool_envelope=True,
        )
    )
    assert (materialized_dir / "COMPLETE").is_file()
    assert materialized_manifest["contract"]["required_origin_label_counts"] == {
        "public_real:S3": 2,
        "synthetic:S1": 6,
        "synthetic:S2": 6,
        "synthetic:S3": 4,
        "synthetic:TS": 6,
    }
    assert (
        materialized_manifest["inputs"]["training"]["assembly_envelope"]["run_id"]
        == "pool-unit-001"
    )

    with pytest.raises(assembler.TrainingPoolAssemblyError, match="already exists"):
        assembler.assemble_training_pool_run(
            input_paths=[input_path],
            public_training_paths=[public_training_path],
            frozen_primary_paths=[frozen_path],
            blocked_public_paths=[public_path],
            out_root=tmp_path / "pool-runs",
            run_id="pool-unit-001",
            final_targets={grade: 6 for grade in GRADES},
            public_real_s3_target=2,
            expected_frozen_primary_count=4,
            expected_document_shapes=3,
            expected_length_profiles=3,
            expected_families=3,
            minimum_public_holdout_artifacts=1,
            minimum_public_holdout_records=1,
        )


def test_attested_public_holdout_artifacts_must_be_pairwise_disjoint(
    monkeypatch: pytest.MonkeyPatch,
):
    duplicate_identity = SimpleNamespace(
        files=(
            {"records_path": "dev/records.jsonl"},
            {"records_path": "blind/records.jsonl"},
        ),
        row_count=2,
        doc_ids=frozenset({"same-doc"}),
        document_family_ids=frozenset({"same-family"}),
        normalized_text_hashes=frozenset({"same-text-hash"}),
    )
    monkeypatch.setattr(
        assembler,
        "load_blocked_corpora",
        lambda paths: duplicate_identity,
    )

    with pytest.raises(
        assembler.TrainingPoolAssemblyError,
        match="holdout artifacts overlap each other",
    ):
        assembler._load_attested_public_holdouts(
            [Path("dev"), Path("blind")],
            minimum_artifacts=2,
            minimum_records=2,
        )


@pytest.mark.parametrize("overlap_kind", ["doc_id", "family", "text"])
def test_frozen_primary_must_be_disjoint_from_public_holdouts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overlap_kind: str,
):
    _allow_content_validation(monkeypatch)
    candidates = _dense_candidates()
    frozen = [
        _holdout(f"frozen-{index}", f"frozen-family-{index}", f"frozen text {index}")
        for index in range(4)
    ]
    public = _holdout(
        "public-holdout",
        "public-holdout-family",
        "public holdout text",
        origin="public_real",
    )
    if overlap_kind == "doc_id":
        public["doc_id"] = frozen[0]["doc_id"]
    elif overlap_kind == "family":
        public["document_family_id"] = frozen[0]["document_family_id"]
    else:
        public["text"] = frozen[0]["text"]

    input_path = tmp_path / "candidates.jsonl"
    frozen_path = tmp_path / "frozen.jsonl"
    _write_jsonl(input_path, candidates)
    _write_jsonl(frozen_path, frozen)
    monkeypatch.setattr(
        assembler,
        "_load_attested_public_training_artifacts",
        lambda paths: ([], {"artifact_count": 1, "row_count": 0}),
    )
    monkeypatch.setattr(
        assembler,
        "_load_attested_public_holdouts",
        lambda paths, *, minimum_artifacts, minimum_records: (
            [dict(public)],
            {
                "artifact_count": minimum_artifacts,
                "row_count": 1,
            },
        ),
    )

    with pytest.raises(
        assembler.TrainingPoolAssemblyError,
        match="frozen primary overlaps attested public holdouts",
    ):
        assembler.assemble_training_pool_run(
            input_paths=[input_path],
            public_training_paths=[tmp_path / "public-training"],
            frozen_primary_paths=[frozen_path],
            blocked_public_paths=[tmp_path / "public-holdout"],
            out_root=tmp_path / "runs",
            final_targets={grade: 6 for grade in GRADES},
            public_real_s3_target=0,
            expected_frozen_primary_count=4,
            expected_document_shapes=3,
            expected_length_profiles=3,
            expected_families=3,
            minimum_public_holdout_artifacts=1,
            minimum_public_holdout_records=1,
        )
    assert not (tmp_path / "runs").exists()


def test_raw_public_training_jsonl_is_not_an_attested_artifact(tmp_path: Path):
    raw = tmp_path / "public-training.jsonl"
    _write_jsonl(raw, [])

    with pytest.raises(
        assembler.PublicTrainingAssemblyError,
        match="must be a regular directory",
    ):
        assembler._load_attested_public_training_artifacts([raw])


def test_raw_public_holdout_jsonl_is_not_an_attested_artifact(tmp_path: Path):
    raw = tmp_path / "public-holdout.jsonl"
    _write_jsonl(raw, [])

    with pytest.raises(
        assembler.ChallengeAssemblyError,
        match="immutable public S3 challenge",
    ):
        assembler._load_attested_public_holdouts(
            [raw], minimum_artifacts=1, minimum_records=1
        )


def test_blocked_public_holdout_is_mandatory(tmp_path: Path):
    with pytest.raises(
        assembler.TrainingPoolAssemblyError,
        match="blocked public holdout is required",
    ):
        assembler.assemble_training_pool_run(
            input_paths=[tmp_path / "unused.jsonl"],
            public_training_paths=[tmp_path / "unused-public-training.jsonl"],
            frozen_primary_paths=[tmp_path / "unused-frozen.jsonl"],
            blocked_public_paths=[],
            out_root=tmp_path / "runs",
        )
