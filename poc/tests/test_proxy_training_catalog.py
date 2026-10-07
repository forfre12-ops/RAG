"""The train-only proxy catalog must stay disjoint from frozen evaluation."""

from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest

from koipa.modules.m3_labeling.rule_engine import grade_from_svm
from scripts.build_proxy_scenarios import expand_catalog_scenarios, generation_plan


_ROOT = Path(__file__).resolve().parents[1]
_TRAIN = _ROOT / "datasets/proxy_gold/training_scenario_catalog.v1.json"
_EVAL = _ROOT / "datasets/proxy_gold/scenario_catalog.v1.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_training_plan_is_exactly_2700_synthetic_plus_300_public_real():
    catalog = _load(_TRAIN)
    scenarios = expand_catalog_scenarios(catalog)
    assert all(row["catalog_split_role"] == "train_pool_only" for row in scenarios)
    assert all(
        row["fact_ledger_contract"] == "proxy-fact-ledger-v1" for row in scenarios
    )
    assert all(row["training_use_permitted"] is True for row in scenarios)
    assert all(row["evaluation_use_permitted"] is False for row in scenarios)
    plan = generation_plan(
        scenarios,
        catalog["instance_profiles"],
        catalog["family_profiles"],
        per_scenario=None,
    )
    assert catalog["split_role"] == "train_pool_only"
    assert len(plan) == catalog["target_training_documents"] == 2700
    assert catalog["target_combined_training_documents"] == 3000
    assert catalog["target_public_real_s3_documents"] == 300
    assert Counter(row[0]["label"] for row in plan) == {
        "TS": 750,
        "S1": 750,
        "S2": 750,
        "S3": 450,
    }
    for grade in ("TS", "S1", "S2", "S3"):
        grade_shapes = Counter(
            shape["family_profile_id"]
            for scenario, _, shape, _ in plan
            if scenario["label"] == grade
        )
        assert set(grade_shapes) == {
            row["family_profile_id"] for row in catalog["family_profiles"]
        }
        assert max(grade_shapes.values()) - min(grade_shapes.values()) <= 1
    assert (
        len(
            {
                f"{scenario['document_family_id']}:{instance['instance_profile_id']}"
                for scenario, instance, _, _ in plan
            }
        )
        == 225
    )


def test_training_and_frozen_catalog_families_are_disjoint():
    training = _load(_TRAIN)
    frozen = _load(_EVAL)
    train_archetypes = {row["document_family_id"] for row in training["archetypes"]}
    eval_archetypes = {row["document_family_id"] for row in frozen["archetypes"]}
    assert train_archetypes.isdisjoint(eval_archetypes)

    train_shapes = {row["family_profile_id"] for row in training["family_profiles"]}
    eval_shapes = {row["family_profile_id"] for row in frozen["family_profiles"]}
    assert train_shapes.isdisjoint(eval_shapes)


def test_training_and_eval_share_the_exact_factor_semantics_only_quotas_differ():
    training = _load(_TRAIN)
    frozen = _load(_EVAL)
    assert training["factor_profile_schema_id"] == frozen["factor_profile_schema_id"]
    assert training["factor_axes"] == frozen["factor_axes"]

    def semantic_rows(catalog: dict) -> list[dict]:
        return [
            {
                key: row.get(key)
                for key in (
                    "profile_id",
                    "label",
                    "representative_pilot",
                    "expected_factor_scores",
                )
            }
            for row in catalog["factor_profiles"]
        ]

    assert semantic_rows(training) == semantic_rows(frozen)
    assert sum(row["target_count_per_archetype"] for row in training["factor_profiles"]) == 180
    assert sum(row["target_count_per_archetype"] for row in frozen["factor_profiles"]) == 100


def test_every_training_topic_has_matched_valid_svm_variants():
    catalog = _load(_TRAIN)
    scenarios = expand_catalog_scenarios(catalog)
    by_family: dict[str, set[str]] = {}
    for row in scenarios:
        scores = row["expected_factor_scores"]
        assert (
            grade_from_svm(scores["secrecy"], scores["value"], scores["management"])
            == row["label"]
        )
        by_family.setdefault(row["document_family_id"], set()).add(row["label"])
    assert len(by_family) == 15
    assert all(labels == {"TS", "S1", "S2", "S3"} for labels in by_family.values())


def test_training_metadata_does_not_identify_the_grade():
    scenarios = expand_catalog_scenarios(_load(_TRAIN))
    for field in ("domain", "industry", "document_type"):
        labels_by_value: dict[str, set[str]] = {}
        for row in scenarios:
            labels_by_value.setdefault(row[field], set()).add(row["label"])
        assert all(
            labels == {"TS", "S1", "S2", "S3"} for labels in labels_by_value.values()
        )


def test_catalog_language_gate_rejects_damaged_korean_before_generation():
    catalog = deepcopy(_load(_TRAIN))
    catalog["archetypes"][0]["shared_context"] = (
        "\u5f02\u5e38 decoded context without Hangul"
    )
    with pytest.raises(SystemExit, match="UTF-8/Hangul integrity gate"):
        expand_catalog_scenarios(catalog)
