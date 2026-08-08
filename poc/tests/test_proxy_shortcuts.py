"""Tests for family-grouped proxy metadata shortcut detection."""
from __future__ import annotations

from collections import defaultdict

import pytest

from lloydk.proxy_shortcuts import (
    DEFAULT_SHORTCUT_FEATURES,
    deterministic_group_folds,
    strict_shortcut_gate,
    theils_u_label_given_feature,
)


LABELS = ("TS", "S1", "S2", "S3")


def _balanced_records(family_count: int = 40) -> list[dict]:
    rows: list[dict] = []
    for family_index in range(family_count):
        for label_index, label in enumerate(LABELS):
            rows.append(
                {
                    "doc_id": f"doc-{family_index}-{label}",
                    "label": label,
                    "document_family_id": f"family-{family_index:03d}",
                    "domain": f"domain-{family_index % 2}",
                    "industry": f"industry-{family_index % 4}",
                    "document_type": f"type-{family_index % 2}",
                    "family_profile_id": f"profile-{family_index:03d}",
                    "length_bin": f"bin-{family_index % 2}",
                    "document_origin": "synthetic",
                    "provider": "provider-shared",
                    "model": "model-shared",
                    "text": str(label_index) * 1200,
                }
            )
    return rows


def test_group_folds_are_deterministic_balanced_and_never_split_family() -> None:
    rows = _balanced_records(13)
    first = deterministic_group_folds(rows, n_splits=5)
    second = deterministic_group_folds(list(reversed(rows)), n_splits=5)

    by_family: dict[str, set[int]] = defaultdict(set)
    for row, fold in zip(rows, first, strict=True):
        by_family[row["document_family_id"]].add(fold)
    assert all(len(folds) == 1 for folds in by_family.values())

    reverse_mapping = {
        row["doc_id"]: fold
        for row, fold in zip(reversed(rows), second, strict=True)
    }
    assert all(reverse_mapping[row["doc_id"]] == fold for row, fold in zip(rows, first, strict=True))
    fold_sizes = [first.count(fold) for fold in sorted(set(first))]
    assert max(fold_sizes) - min(fold_sizes) <= 4


def test_balanced_metadata_passes_strict_gate() -> None:
    report = strict_shortcut_gate(_balanced_records())
    assert report["gate"]["passed"], report["gate"]["violations"]
    assert report["combined_baseline"]["macro_f1"] <= 0.45
    assert all(
        metrics["theil_u"] == pytest.approx(0.0)
        for metrics in report["single_features"].values()
    )


def test_perfect_single_metadata_shortcut_fails_accuracy_u_and_cells() -> None:
    rows = _balanced_records()
    for row in rows:
        row["domain"] = f"domain-{row['label']}"
    report = strict_shortcut_gate(rows)
    domain = report["single_features"]["domain"]
    assert domain["accuracy"] == pytest.approx(1.0)
    assert domain["macro_f1"] == pytest.approx(1.0)
    assert domain["theil_u"] == pytest.approx(1.0)
    assert any(value.startswith("single_accuracy:domain:") for value in report["gate"]["violations"])
    assert any(value.startswith("single_theil_u:domain:") for value in report["gate"]["violations"])
    assert any(value.startswith("cell_grade_coverage:domain:") for value in report["gate"]["violations"])


def test_combined_metadata_shortcut_fails_macro_f1() -> None:
    rows = _balanced_records()
    pairs = {
        "TS": ("a", "x"),
        "S1": ("a", "y"),
        "S2": ("b", "x"),
        "S3": ("b", "y"),
    }
    for row in rows:
        row["domain"], row["industry"] = pairs[row["label"]]
    report = strict_shortcut_gate(rows)
    assert report["combined_baseline"]["macro_f1"] > 0.45
    assert any(value.startswith("combined_macro_f1:") for value in report["gate"]["violations"])


def test_large_cell_requires_at_least_three_grades() -> None:
    rows = _balanced_records(10)
    for row in rows:
        if row["label"] in {"TS", "S1"}:
            row["industry"] = "two-grade-cell"
        else:
            row["industry"] = f"small-{row['doc_id']}"
    report = strict_shortcut_gate(rows)
    cell_errors = report["single_features"]["industry"]["large_cell_violations"]
    assert cell_errors == [
        {
            "value": "two-grade-cell",
            "count": 20,
            "grade_count": 2,
            "label_counts": {"S1": 10, "TS": 10},
        }
    ]
    assert report["gate"]["status"] == "inconclusive"


def test_known_confound_is_reported_but_excluded_for_frozen_gold() -> None:
    rows = _balanced_records()
    for row in rows:
        row["provider"] = f"provider-{row['label']}"

    frozen = strict_shortcut_gate(rows, frozen_gold=True)
    assert frozen["gate"]["passed"], frozen["gate"]["violations"]
    assert frozen["known_confounds"]["provider"]["accuracy"] == pytest.approx(1.0)
    assert "provider" in frozen["gate"]["excluded_known_confounds"]
    assert any(
        warning.startswith("known_confound_accuracy:provider:")
        for warning in frozen["gate"]["warnings"]
    )

    diagnostic = strict_shortcut_gate(rows, frozen_gold=False)
    assert diagnostic["gate"]["status"] == "fail"
    assert any(
        value.startswith("single_accuracy:provider:")
        for value in diagnostic["gate"]["violations"]
    )


def test_theil_u_is_directional_and_detects_independence() -> None:
    rows = _balanced_records(8)
    assert theils_u_label_given_feature(rows, "domain") == pytest.approx(0.0)
    for row in rows:
        row["domain"] = row["label"]
    assert theils_u_label_given_feature(rows, "domain") == pytest.approx(1.0)


def test_missing_length_bin_is_derived_from_text() -> None:
    rows = _balanced_records()
    for row in rows:
        row.pop("length_bin")
    report = strict_shortcut_gate(rows)
    assert report["single_features"]["length_bin"]["missing_count"] == 0


def test_fewer_than_100_records_is_inconclusive_not_pass() -> None:
    report = strict_shortcut_gate(_balanced_records(10))
    assert report["n"] == 40
    assert report["gate"]["status"] == "inconclusive"
    assert not report["gate"]["passed"]
    assert "insufficient_sample_size:40<100" in report["gate"]["violations"]


def test_missing_family_is_rejected() -> None:
    rows = _balanced_records(1)
    rows[0].pop("document_family_id")
    with pytest.raises(ValueError, match="missing document_family_id"):
        strict_shortcut_gate(rows)


def test_default_gate_features_are_exactly_the_requested_metadata() -> None:
    assert DEFAULT_SHORTCUT_FEATURES == (
        "domain",
        "industry",
        "document_type",
        "family_profile_id",
        "length_bin",
    )
