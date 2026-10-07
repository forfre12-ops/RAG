"""Public-source registry schema and hard license-gate tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.validate_public_source_registry import (
    DEFAULT_REGISTRY,
    ELIGIBLE_LICENSES,
    EVALUATION_ONLY_LICENSES,
    load_registry,
    policy_status,
    validate_registry,
)


def _source(**overrides: object) -> dict:
    source = {
        "source_id": "official-primary",
        "title": "Official primary document",
        "provider": "Official Agency",
        "page_url": "https://www.data.go.kr/data/123/fileData.do",
        "license": "KOGL-0",
        "source_tier": "tier_1_official_primary",
        "status": "eligible",
        "third_party_rights": "cleared",
        "checked_at": "2026-08-08",
        "expected_document_count": 1,
    }
    source.update(overrides)
    return source


def _registry(*sources: dict) -> dict:
    return {
        "registry_version": "test",
        "checked_at": "2026-08-08",
        "policy": {
            "eligible_licenses": sorted(ELIGIBLE_LICENSES),
            "evaluation_only_licenses": sorted(EVALUATION_ONLY_LICENSES),
            "blocked_licenses": ["KOGL-2", "KOGL-3", "KOGL-4", "UNSPECIFIED"],
            "eligible_source_tiers": ["tier_1_official_primary"],
            "eligible_third_party_rights": ["cleared"],
            "prohibited_sources": ["DART"],
        },
        "sources": list(sources),
    }


def test_checked_in_registry_is_policy_consistent() -> None:
    report = validate_registry(load_registry(DEFAULT_REGISTRY))
    assert report["ok"], report["errors"]
    assert report["status_counts"]["eligible"] >= 2
    assert report["status_counts"]["evaluation_only"] >= 10
    assert report["status_counts"]["blocked"] >= 3
    assert report["evaluation_only_expected_documents"] >= 20


def test_every_declared_eligible_source_has_explicit_permission() -> None:
    registry = load_registry(DEFAULT_REGISTRY)
    eligible = [row for row in registry["sources"] if row["status"] == "eligible"]
    assert eligible
    for source in eligible:
        assert source["license"] in ELIGIBLE_LICENSES
        assert source["source_tier"] == "tier_1_official_primary"
        assert source["third_party_rights"] == "cleared"
        assert policy_status(source)[0] == "eligible"


@pytest.mark.parametrize("license_code", ["KOGL-2", "KOGL-3", "KOGL-4"])
def test_restricted_kogl_license_cannot_be_declared_eligible(
    license_code: str,
) -> None:
    source = _source(license=license_code)
    report = validate_registry(_registry(source))
    assert not report["ok"]
    assert policy_status(source)[0] == "blocked"
    assert any("status_mismatch:eligible!=blocked" in error for error in report["errors"])


def test_dart_is_blocked_even_if_record_claims_kogl_1() -> None:
    source = _source(
        source_id="dart-filing",
        provider="DART",
        page_url="https://dart.fss.or.kr/",
    )
    report = validate_registry(_registry(source))
    assert not report["ok"]
    assert policy_status(source) == ("blocked", "prohibited_source:DART")


@pytest.mark.parametrize("rights", ["unknown", "possible", "varies_by_item"])
def test_unclear_third_party_rights_are_blocked(rights: str) -> None:
    source = _source(third_party_rights=rights)
    report = validate_registry(_registry(source))
    assert not report["ok"]
    assert policy_status(source)[0] == "blocked"


@pytest.mark.parametrize("license_code", ["KOGL-0", "KOGL-AI"])
def test_permitted_kogl_types_are_eligible(license_code: str) -> None:
    source = _source(license=license_code)
    report = validate_registry(_registry(source))
    assert report["ok"], report["errors"]
    assert policy_status(source)[0] == "eligible"


def test_kogl_1_is_evaluation_only_not_training_eligible() -> None:
    source = _source(license="KOGL-1", status="evaluation_only")
    report = validate_registry(_registry(source))
    assert report["ok"], report["errors"]
    assert policy_status(source) == (
        "evaluation_only",
        "evaluation_only_under_conservative_policy",
    )


def test_explicit_machine_learning_permission_is_eligible() -> None:
    source = _source(license="EXPLICIT-ML-TRAINING")
    report = validate_registry(_registry(source))
    assert report["ok"], report["errors"]


def test_free_reuse_without_explicit_ml_permission_remains_conditional() -> None:
    source = _source(
        license="OTHER-FREE-REUSE",
        source_tier="tier_2_official_index",
        status="conditional",
    )
    report = validate_registry(_registry(source))
    assert report["ok"], report["errors"]
    assert policy_status(source)[0] == "conditional"


def test_aggregate_attachment_catalog_requires_item_verification() -> None:
    source = _source(
        license="KOGL-1",
        source_tier="tier_2_official_index",
        status="conditional",
        third_party_rights="requires_item_verification",
    )
    report = validate_registry(_registry(source))
    assert report["ok"], report["errors"]
    assert policy_status(source) == (
        "conditional",
        "requires_item_level_rights_and_license_verification",
    )


def test_index_license_does_not_promote_linked_documents() -> None:
    source = _source(
        source_tier="tier_2_official_index",
        status="conditional",
    )
    report = validate_registry(_registry(source))
    assert report["ok"], report["errors"]
    assert policy_status(source)[0] == "conditional"


def test_missing_required_evidence_and_duplicate_ids_fail() -> None:
    first = _source()
    second = _source(page_url="http://example.invalid/not-official", checked_at="08-08-2026")
    second.pop("third_party_rights")
    report = validate_registry(_registry(first, second))
    assert not report["ok"]
    assert any("missing:third_party_rights" in error for error in report["errors"])
    assert any("invalid_https_url" in error for error in report["errors"])
    assert any("invalid_checked_at" in error for error in report["errors"])
    assert any("duplicate_source_id" in error for error in report["errors"])


def test_cli_registry_file_shape_is_plain_json(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(_registry(_source())), encoding="utf-8")
    assert load_registry(path)["sources"][0]["source_id"] == "official-primary"
