"""Deny known policy fixtures at training/evaluation boundaries, without ML imports.

Passing this guard is NOT policy approval or gold qualification. It only checks
explicit restrictions and the versioned, local fixture fingerprint registry.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable
from collections.abc import Mapping

REGISTRY_PATH = Path(__file__).with_name("policy_fixture_registry.json")
PURPOSES = {"training", "model_evaluation"}
FIXTURE_SCHEMAS = {"content-reference-pack-v1", "content-reference-material-revision-v1.1"}
ID_FIELDS = ("doc_id", "id", "parent_doc_id", "source_doc_id", "source_document_id")
FAMILY_FIELDS = ("family_id", "document_family_id", "source_family_id", "source_document_family_id")
PERMISSION_FIELDS = {
    "training": ("training_allowed", "training_use_permitted"),
    "model_evaluation": ("model_evaluation_allowed", "evaluation_allowed", "evaluation_use_permitted"),
}


class DatasetUsageError(ValueError):
    """The requested use is prohibited or cannot be checked."""


def body_fingerprint(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", "", text).encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def fixture_registry() -> dict:
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        if (data["schema_version"] != "policy-fixture-deny-registry-v1"
                or data["usage"] != "policy_fixture" or not data["records"]
                or not data["packs"]):
            raise ValueError("invalid registry header")
        records = data["records"]
        ids = [r["doc_id"] for r in records]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate fixture ID")
        for row in records:
            if not row["family_id"] or not row["policy_version"] or not row["body_fingerprints"]:
                raise ValueError("incomplete fixture record")
            if any(not re.fullmatch(r"[0-9a-f]{64}", h) for h in row["body_fingerprints"]):
                raise ValueError("invalid body fingerprint")
        return data
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DatasetUsageError("Fixture registry unavailable/invalid; refusing unchecked use") from exc


@lru_cache(maxsize=1)
def _fixture_index() -> tuple[set, set, set, set]:
    rows = fixture_registry()["records"]
    return ({r["doc_id"] for r in rows}, {r["family_id"] for r in rows},
            {r["policy_version"] for r in rows},
            {h for r in rows for h in r["body_fingerprints"]})


def _restriction(row: dict, purpose: str) -> str | None:
    if row.get("schema_version") in FIXTURE_SCHEMAS:
        return "policy_fixture schema"
    if any(row.get(k) == "policy_fixture" for k in ("usage", "dataset_role", "purpose")):
        return "policy_fixture role"
    for flag in PERMISSION_FIELDS[purpose]:
        if flag in row and type(row[flag]) is not bool:
            return f"invalid {flag} (boolean required)"
        if row.get(flag) is False:
            return f"{flag}=false"
    return None


def assert_path_usage(source: Path, purpose: str) -> None:
    if purpose not in PURPOSES:
        raise DatasetUsageError(f"Unknown dataset purpose: {purpose}")
    fixture_registry()  # Missing installed registry is not a silent bypass.
    source = source.resolve()
    directory = source if source.is_dir() else source.parent
    for parent in (directory, *directory.parents):
        manifest = parent / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("manifest must be an object")
        except (OSError, ValueError) as exc:
            raise DatasetUsageError(f"Invalid usage manifest: {manifest.name}") from exc
        reason = _restriction(value, purpose)
        if reason:
            raise DatasetUsageError(f"{purpose} prohibited: {reason}; source={source.name}")


def assert_dataset_usage(rows: Iterable[dict], *, purpose: str, source: Path | None = None) -> None:
    if purpose not in PURPOSES:
        raise DatasetUsageError(f"Unknown dataset purpose: {purpose}")
    if source is not None:
        assert_path_usage(source, purpose)
    ids, families, policies, hashes = _fixture_index()
    count = 0
    for count, row in enumerate(rows, 1):
        if not isinstance(row, Mapping):
            raise DatasetUsageError(f"Invalid dataset object at row {count}")
        reason = _restriction(row, purpose)
        if any(str(row.get(k, "")).strip() in ids for k in ID_FIELDS):
            reason = "registered policy_fixture ID"
        if any(str(row.get(k, "")).strip() in families for k in FAMILY_FIELDS):
            reason = "registered policy_fixture family"
        version = str(row.get("policy_version", "")).strip()
        if version in policies or version.startswith("content-protection-reference-"):
            reason = "internal content-reference policy_fixture"
        for field in ("text", "content", "body", "desc"):
            value = row.get(field)
            if isinstance(value, str) and value.strip() and body_fingerprint(value) in hashes:
                reason = "registered policy_fixture body (including whitespace-only copies)"
        if reason:
            # No source text or labels in the exception/log.
            raise DatasetUsageError(f"{purpose} prohibited at row {count}: {reason}")
    if not count:
        raise DatasetUsageError(f"Empty dataset: cannot perform {purpose}")
