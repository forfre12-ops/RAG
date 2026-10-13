"""Proxy generation planning must honor exact targets and family diversity."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from scripts.build_proxy_scenarios import (
    describe_plan,
    expand_catalog_scenarios,
    generation_plan,
    main,
    partition_generation_plan_by_family,
)


_CATALOG = (
    Path(__file__).resolve().parents[1] / "datasets/proxy_gold/scenario_catalog.v1.json"
)
_TRAINING_CATALOG = (
    Path(__file__).resolve().parents[1]
    / "datasets/proxy_gold/training_scenario_catalog.v1.json"
)


def test_target_plan_is_exactly_1000_and_family_diverse():
    catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
    scenarios = expand_catalog_scenarios(catalog)
    plan = generation_plan(
        scenarios,
        catalog["instance_profiles"],
        catalog["family_profiles"],
        per_scenario=None,
    )
    by_grade = Counter(scenario["label"] for scenario, _, _, _ in plan)
    families = {
        f"{scenario['document_family_id']}:{instance['instance_profile_id']}"
        for scenario, instance, _, _ in plan
    }
    assert len(plan) == 1000
    assert by_grade == {"TS": 200, "S1": 250, "S2": 250, "S3": 300}
    assert len(families) == 100
    by_shape = Counter(shape["family_profile_id"] for _, _, shape, _ in plan)
    assert set(by_shape) == {
        row["family_profile_id"] for row in catalog["family_profiles"]
    }
    assert max(by_shape.values()) - min(by_shape.values()) <= 1
    for grade in ("TS", "S1", "S2", "S3"):
        grade_shapes = Counter(
            shape["family_profile_id"]
            for scenario, _, shape, _ in plan
            if scenario["label"] == grade
        )
        assert max(grade_shapes.values()) - min(grade_shapes.values()) <= 1

    for family_id, grade in {
        (scenario["document_family_id"], scenario["label"])
        for scenario, _, _, _ in plan
    }:
        family_grade_shapes = Counter(
            shape["family_profile_id"]
            for scenario, _, shape, _ in plan
            if scenario["document_family_id"] == family_id
            and scenario["label"] == grade
        )
        assert len(family_grade_shapes) == len(catalog["family_profiles"])
        assert max(family_grade_shapes.values()) - min(family_grade_shapes.values()) <= 1


def test_pilot_plan_keeps_one_per_scenario():
    catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
    scenarios = [
        row
        for row in expand_catalog_scenarios(catalog)
        if row["representative_pilot"] is True
    ]
    plan = generation_plan(
        scenarios,
        catalog["instance_profiles"],
        catalog["family_profiles"],
        per_scenario=1,
    )
    assert len(plan) == 40


def test_all_boundary_profiles_pilot_contains_210_documents():
    catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
    scenarios = expand_catalog_scenarios(catalog)
    plan = generation_plan(
        scenarios,
        catalog["instance_profiles"],
        catalog["family_profiles"],
        per_scenario=1,
    )
    assert len(plan) == 210
    assert len({row[0]["factor_profile_id"] for row in plan}) == 21


def test_oversample_plan_preserves_balanced_shapes_and_expands_targets():
    catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
    scenarios = expand_catalog_scenarios(catalog)
    plan = generation_plan(
        scenarios,
        catalog["instance_profiles"],
        catalog["family_profiles"],
        per_scenario=None,
        count_multiplier=2.2,
    )
    by_grade = Counter(scenario["label"] for scenario, _, _, _ in plan)
    assert len(plan) == 2_200
    assert by_grade == {"TS": 440, "S1": 550, "S2": 550, "S3": 660}
    by_shape = Counter(shape["family_profile_id"] for _, _, shape, _ in plan)
    assert max(by_shape.values()) - min(by_shape.values()) <= 1


def test_primary_cli_target_counts_reports_exact_matched_1000(monkeypatch, capsys):
    monkeypatch.setenv("LOCAL_LLM_MODEL", "qwen3:14b")
    result = main(
        [
            "--catalog",
            str(_CATALOG),
            "--provider",
            "local_openai",
            "--model-manifest-sha256",
            "sha256:" + "a" * 64,
            "--target-counts",
            "--dry-run",
        ]
    )
    summary = json.loads(capsys.readouterr().out)
    assert result == 0
    assert summary["documents"] == 1000
    assert summary["by_grade"] == {
        "S1": 250,
        "S2": 250,
        "S3": 300,
        "TS": 200,
    }
    assert summary["families"] == 100


def test_family_shards_are_disjoint_and_union_to_the_unsharded_plan():
    catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
    scenarios = expand_catalog_scenarios(catalog)
    full_plan = generation_plan(
        scenarios,
        catalog["instance_profiles"],
        catalog["family_profiles"],
        per_scenario=None,
    )
    full_descriptors, _ = describe_plan(full_plan)
    expected_keys = {row["resume_key"] for row in full_descriptors}
    seen_keys: set[str] = set()
    seen_families: set[str] = set()

    for shard_index in range(10):
        shard, metadata = partition_generation_plan_by_family(
            full_plan, shard_count=10, shard_index=shard_index
        )
        shard_descriptors, summary = describe_plan(shard)
        shard_keys = {row["resume_key"] for row in shard_descriptors}
        family_ids = {str(item[0]["document_family_id"]) for item in shard}
        assert len(shard) == 100
        assert summary["by_grade"] == {"S1": 25, "S2": 25, "S3": 30, "TS": 20}
        assert len(family_ids) == 1
        assert not (shard_keys & seen_keys)
        assert not (family_ids & seen_families)
        assert metadata["selected_document_family_ids"] == sorted(family_ids)
        seen_keys.update(shard_keys)
        seen_families.update(family_ids)

    assert seen_keys == expected_keys
    assert len(seen_families) == 10


def test_cli_shard_separates_base_buffer_and_attempt_targets(monkeypatch, capsys):
    monkeypatch.setenv("LOCAL_LLM_MODEL", "qwen3:14b")
    result = main(
        [
            "--catalog",
            str(_CATALOG),
            "--provider",
            "local_openai",
            "--model-manifest-sha256",
            "sha256:" + "a" * 64,
            "--target-counts",
            "--oversample-factor",
            "2.5",
            "--candidate-buffer-factor",
            "2.0",
            "--shard-count",
            "10",
            "--shard-index",
            "0",
            "--dry-run",
        ]
    )

    summary = json.loads(capsys.readouterr().out)
    assert result == 0
    assert summary["documents"] == 251
    assert summary["by_grade"] == {"S1": 63, "S2": 63, "S3": 75, "TS": 50}
    assert summary["base_final_target_by_grade"] == {
        "S1": 25,
        "S2": 25,
        "S3": 30,
        "TS": 20,
    }
    assert summary["selection_target_by_grade"] == {
        "S1": 50,
        "S2": 50,
        "S3": 60,
        "TS": 40,
    }
    assert summary["partition"]["selected_document_family_count"] == 1
    assert summary["partition"]["shard_count"] == 10


def test_cli_grade_filter_supports_real_s3_replacement(monkeypatch, capsys):
    monkeypatch.setenv("LOCAL_LLM_MODEL", "qwen3:14b")
    result = main(
        [
            "--catalog",
            str(_TRAINING_CATALOG),
            "--provider",
            "local_openai",
            "--model-manifest-sha256",
            "sha256:" + "a" * 64,
            "--target-counts",
            "--grade",
            "TS",
            "--grade",
            "S1",
            "--grade",
            "S2",
            "--dry-run",
        ]
    )
    summary = json.loads(capsys.readouterr().out)
    assert result == 0
    assert summary["documents"] == 2_250
    assert summary["by_grade"] == {"S1": 750, "S2": 750, "TS": 750}
    assert "S3" not in summary["selection_target_by_grade"]
