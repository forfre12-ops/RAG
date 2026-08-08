"""Validate the public-document source registry without fetching any source.

This gate is intentionally stricter than public availability.  Training is
limited to KOGL-0, KOGL-AI, or explicit ML-training permission.  Under the
project's conservative policy KOGL-1 is evaluation-only, never training data.
Catalog pages are discovery aids; their licence never flows through to linked
documents and recipient-specific approval (for example AI-Hub) is validated by
the corresponding intake tool, not inferred here.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse


TRAINING_ELIGIBLE_LICENSES = frozenset(
    {"KOGL-0", "KOGL-AI", "EXPLICIT-ML-TRAINING"}
)
# Backwards-compatible import name used by the collector.  "Eligible" means
# eligible for training; KOGL-1 is deliberately absent.
ELIGIBLE_LICENSES = TRAINING_ELIGIBLE_LICENSES
EVALUATION_ONLY_LICENSES = frozenset({"KOGL-1"})
BLOCKED_LICENSES = frozenset({"KOGL-2", "KOGL-3", "KOGL-4", "UNSPECIFIED"})
KNOWN_LICENSES = TRAINING_ELIGIBLE_LICENSES | EVALUATION_ONLY_LICENSES | BLOCKED_LICENSES | {
    "MIXED",
    "OTHER-FREE-REUSE",
}
SOURCE_TIERS = frozenset(
    {
        "tier_1_official_primary",
        "tier_2_official_index",
        "tier_3_excluded",
    }
)
RIGHTS_STATES = frozenset(
    {
        "cleared",
        "possible",
        "unknown",
        "varies_by_item",
        "requires_item_verification",
    }
)
DECLARED_STATUSES = frozenset(
    {"eligible", "evaluation_only", "conditional", "blocked"}
)
REQUIRED_FIELDS = (
    "source_id",
    "title",
    "provider",
    "page_url",
    "license",
    "source_tier",
    "status",
    "third_party_rights",
    "checked_at",
)
PROHIBITED_HOSTS = frozenset({"dart.fss.or.kr", "opendart.fss.or.kr"})

_HERE = Path(__file__).resolve().parent
DEFAULT_REGISTRY = (
    _HERE.parent / "datasets" / "proxy_gold" / "public_source_registry.v1.json"
)


def load_registry(path: Path) -> dict:
    """Load one registry object, raising ValueError for malformed JSON/shape."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load registry {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("registry root must be an object")
    return raw


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_checked_at(value: object) -> bool:
    if not _nonempty_text(value):
        return False
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError:
        return False
    return parsed.isoformat() == value


def _is_dart(source: Mapping[str, object]) -> bool:
    provider = str(source.get("provider") or "").strip().casefold()
    source_id = str(source.get("source_id") or "").strip().casefold()
    host = (urlparse(str(source.get("page_url") or "")).hostname or "").casefold()
    return provider == "dart" or source_id.startswith("dart-") or host in PROHIBITED_HOSTS


def policy_status(source: Mapping[str, object]) -> tuple[str, str]:
    """Return the status required by the hard-coded ingestion policy."""
    if _is_dart(source):
        return "blocked", "prohibited_source:DART"

    license_code = str(source.get("license") or "").strip()
    tier = str(source.get("source_tier") or "").strip()
    rights = str(source.get("third_party_rights") or "").strip()
    if license_code in BLOCKED_LICENSES:
        return "blocked", f"blocked_license:{license_code}"
    if rights == "requires_item_verification":
        return "conditional", "requires_item_level_rights_and_license_verification"
    if rights != "cleared":
        return "blocked", f"third_party_rights_not_cleared:{rights or 'missing'}"
    if (
        license_code in TRAINING_ELIGIBLE_LICENSES
        and tier == "tier_1_official_primary"
    ):
        return "eligible", "eligible_explicit_permission"
    if license_code in EVALUATION_ONLY_LICENSES and tier == "tier_1_official_primary":
        return "evaluation_only", "evaluation_only_under_conservative_policy"
    return "conditional", "requires_document_level_explicit_ml_permission"


def _validate_embedded_policy(registry: Mapping[str, object], errors: list[str]) -> None:
    policy = registry.get("policy")
    if not isinstance(policy, Mapping):
        errors.append("registry:missing_or_invalid:policy")
        return
    expected_sets = {
        "eligible_licenses": TRAINING_ELIGIBLE_LICENSES,
        "evaluation_only_licenses": EVALUATION_ONLY_LICENSES,
        "blocked_licenses": BLOCKED_LICENSES,
        "eligible_source_tiers": {"tier_1_official_primary"},
        "eligible_third_party_rights": {"cleared"},
        "prohibited_sources": {"DART"},
    }
    for key, expected in expected_sets.items():
        value = policy.get(key)
        if not isinstance(value, list) or set(value) != set(expected):
            errors.append(f"registry:policy_mismatch:{key}")


def _validate_source(source: object, index: int, errors: list[str]) -> None:
    prefix = f"source[{index}]"
    if not isinstance(source, Mapping):
        errors.append(f"{prefix}:not_an_object")
        return
    source_id = str(source.get("source_id") or f"index-{index}").strip()
    prefix = f"source:{source_id}"
    for field in REQUIRED_FIELDS:
        if not _nonempty_text(source.get(field)):
            errors.append(f"{prefix}:missing:{field}")

    page_url = str(source.get("page_url") or "")
    parsed = urlparse(page_url)
    if parsed.scheme != "https" or not parsed.hostname:
        errors.append(f"{prefix}:invalid_https_url")

    if not _valid_checked_at(source.get("checked_at")):
        errors.append(f"{prefix}:invalid_checked_at")

    license_code = str(source.get("license") or "").strip()
    if license_code not in KNOWN_LICENSES:
        errors.append(f"{prefix}:unknown_license:{license_code or 'missing'}")
    tier = str(source.get("source_tier") or "").strip()
    if tier not in SOURCE_TIERS:
        errors.append(f"{prefix}:invalid_source_tier:{tier or 'missing'}")
    rights = str(source.get("third_party_rights") or "").strip()
    if rights not in RIGHTS_STATES:
        errors.append(f"{prefix}:invalid_third_party_rights:{rights or 'missing'}")
    declared = str(source.get("status") or "").strip()
    if declared not in DECLARED_STATUSES:
        errors.append(f"{prefix}:invalid_status:{declared or 'missing'}")

    required, reason = policy_status(source)
    if declared and declared != required:
        errors.append(
            f"{prefix}:status_mismatch:{declared}!={required}:{reason}"
        )

    expected_count = source.get("expected_document_count")
    if not isinstance(expected_count, int) or isinstance(expected_count, bool):
        errors.append(f"{prefix}:invalid_expected_document_count")
    elif expected_count < 0:
        errors.append(f"{prefix}:invalid_expected_document_count")


def validate_registry(registry: Mapping[str, object]) -> dict:
    """Return a deterministic, audit-friendly validation report."""
    errors: list[str] = []
    if not _nonempty_text(registry.get("registry_version")):
        errors.append("registry:missing:registry_version")
    if not _valid_checked_at(registry.get("checked_at")):
        errors.append("registry:invalid_checked_at")
    _validate_embedded_policy(registry, errors)

    sources = registry.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("registry:missing_or_empty:sources")
        sources = []

    seen_ids: set[str] = set()
    for index, source in enumerate(sources):
        _validate_source(source, index, errors)
        if not isinstance(source, Mapping):
            continue
        source_id = str(source.get("source_id") or "").strip()
        if source_id in seen_ids:
            errors.append(f"source:{source_id}:duplicate_source_id")
        seen_ids.add(source_id)

    statuses = Counter()
    eligible_documents = 0
    evaluation_only_documents = 0
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        required, _ = policy_status(source)
        statuses[required] += 1
        if required == "eligible":
            count = source.get("expected_document_count")
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                eligible_documents += count
        elif required == "evaluation_only":
            count = source.get("expected_document_count")
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                evaluation_only_documents += count

    return {
        "ok": not errors,
        "source_count": len(sources),
        "status_counts": dict(sorted(statuses.items())),
        "eligible_expected_documents": eligible_documents,
        "evaluation_only_expected_documents": evaluation_only_documents,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate public-source license and provenance registry"
    )
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--json", action="store_true", help="print the full report")
    args = parser.parse_args(argv)

    try:
        registry = load_registry(Path(args.registry))
    except ValueError as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 2
    report = validate_registry(registry)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        summary = {
            key: report[key]
            for key in (
                "ok",
                "source_count",
                "status_counts",
                "eligible_expected_documents",
                "errors",
            )
        }
        print(json.dumps(summary, ensure_ascii=False))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
