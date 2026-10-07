"""The proxy scenario catalog is a controlled input, not free-form prompt text."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from koipa.proxy_corpus import REQUIRED_HIGH_GRADE_EVIDENCE
from koipa.modules.m3_labeling.rule_engine import grade_from_svm
from koipa.proxy_shortcuts import strict_shortcut_gate
from scripts.build_proxy_scenarios import expand_catalog_scenarios, generation_plan


_CATALOG = (
    Path(__file__).resolve().parents[1] / "datasets/proxy_gold/scenario_catalog.v1.json"
)


def test_catalog_has_the_exact_matched_primary_distribution():
    catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
    scenarios = expand_catalog_scenarios(catalog)
    assert all(
        row["catalog_split_role"] == "frozen_proxy_eval_only" for row in scenarios
    )
    assert all(row["evaluation_use_permitted"] is True for row in scenarios)
    assert all(row["training_use_permitted"] is False for row in scenarios)
    totals = {
        grade: sum(row["target_count"] for row in scenarios if row["label"] == grade)
        for grade in ("TS", "S1", "S2", "S3")
    }
    assert totals == {"TS": 200, "S1": 250, "S2": 250, "S3": 300}
    architecture = catalog["evaluation_architecture"]
    assert architecture["primary_frozen_proxy"] == {
        "target_count": 1000,
        "document_origin": "synthetic",
        "grade_counts": {"TS": 200, "S1": 250, "S2": 250, "S3": 300},
        "claim_scope": "matched synthetic proxy regression and calibration only",
    }
    assert (
        architecture["public_real_s3_challenge"]["included_in_primary_metrics"] is False
    )


def test_catalog_scenarios_have_quality_and_evidence_contracts():
    catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
    assert len(catalog["instance_profiles"]) >= 10
    assert len(catalog["family_profiles"]) >= 10
    seen_ids = set()
    scenarios = expand_catalog_scenarios(catalog)
    for row in scenarios:
        assert row["scenario_id"] not in seen_ids
        seen_ids.add(row["scenario_id"])
        assert row["min_chars"] >= 1200
        assert row["max_chars"] >= row["min_chars"]
        assert REQUIRED_HIGH_GRADE_EVIDENCE.issubset(row["evidence_card"])


def test_each_archetype_has_matched_grade_variants_with_valid_svm():
    catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
    scenarios = expand_catalog_scenarios(catalog)
    by_family: dict[str, set[str]] = {}
    for row in scenarios:
        scores = row["expected_factor_scores"]
        assert (
            grade_from_svm(scores["secrecy"], scores["value"], scores["management"])
            == row["label"]
        )
        by_family.setdefault(row["document_family_id"], set()).add(row["label"])
    assert len(by_family) == len(catalog["archetypes"])
    assert all(labels == {"TS", "S1", "S2", "S3"} for labels in by_family.values())


def test_industry_and_document_type_do_not_identify_grade():
    catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
    scenarios = expand_catalog_scenarios(catalog)
    for field in ("domain", "industry", "document_type"):
        labels_by_value: dict[str, set[str]] = {}
        for row in scenarios:
            labels_by_value.setdefault(row[field], set()).add(row["label"])
        assert all(
            labels == {"TS", "S1", "S2", "S3"} for labels in labels_by_value.values()
        )


def test_factor_catalog_covers_all_and_only_the_21_plausible_svm_cells():
    catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
    profiles = catalog["factor_profiles"]
    triples = {
        tuple(row["expected_factor_scores"][name] for name in ("secrecy", "value", "management"))
        for row in profiles
    }
    expected = {
        (secrecy, value, management)
        for secrecy in range(3)
        for value in range(3)
        for management in range(3)
        if not (secrecy == 0 and management > 0)
    }
    assert len(profiles) == 21
    assert triples == expected
    assert Counter(row["label"] for row in profiles) == {
        "TS": 2,
        "S1": 1,
        "S2": 6,
        "S3": 12,
    }
    assert {
        tuple(row["expected_factor_scores"][name] for name in ("secrecy", "value", "management"))
        for row in profiles
        if row.get("representative_pilot") is True
    } == {(2, 2, 2), (2, 2, 0), (1, 1, 1), (0, 0, 0)}

    public_axis = catalog["factor_axes"]["secrecy"][0]
    value_zero_axis = catalog["factor_axes"]["value"][0]
    management_zero_axis = catalog["factor_axes"]["management"][0]
    combined = " ".join(
        [
            public_axis["condition"],
            public_axis["claim"],
            public_axis["disclosure_scope"],
            value_zero_axis["condition"],
            value_zero_axis["claim"],
            value_zero_axis["harm_potential"],
            management_zero_axis["condition"],
            management_zero_axis["claim"],
        ]
    )
    assert "공개" in combined and "누구나" in combined
    assert "재현" in combined and "통제" in combined
    assert all(
        marker not in combined for marker in ("TS", "S1", "S2", "S3", "대외비", "극비")
    )


def test_exact_primary_plan_passes_the_metadata_shortcut_gate():
    catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
    scenarios = expand_catalog_scenarios(catalog)
    plan = generation_plan(
        scenarios,
        catalog["instance_profiles"],
        catalog["family_profiles"],
        per_scenario=None,
    )
    rows = []
    for scenario, instance, shape, ordinal in plan:
        target_chars = (int(shape["min_chars"]) + int(shape["max_chars"])) // 2
        rows.append(
            {
                "doc_id": f"{scenario['scenario_id']}:{instance['instance_profile_id']}:{ordinal}",
                "label": scenario["label"],
                "document_origin": "synthetic",
                "document_family_id": (
                    f"{scenario['document_family_id']}:{instance['instance_profile_id']}"
                ),
                "domain": scenario["domain"],
                "industry": scenario["industry"],
                "document_type": shape["family_profile_id"],
                "family_profile_id": shape["family_profile_id"],
                "text": ("가상 문서의 서로 연결된 사실과 수치 항목. " * 200)[
                    :target_chars
                ],
                "provider": "pinned-generator",
                "model": "pinned-model",
            }
        )

    assert len(rows) == 1000
    assert Counter(row["label"] for row in rows) == {
        "TS": 200,
        "S1": 250,
        "S2": 250,
        "S3": 300,
    }
    assert len({row["document_family_id"] for row in rows}) == 100
    report = strict_shortcut_gate(rows, frozen_gold=True)
    assert report["gate"]["status"] == "pass"
    assert report["gate"]["violations"] == []
