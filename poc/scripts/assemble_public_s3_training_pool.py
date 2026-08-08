"""Assemble an immutable public-real S3 training-only pool.

This artifact is deliberately not an evaluation challenge. Source-level
permissions remain intact for provenance, while the artifact contract forbids
using selected rows for evaluation, golden-set claims, or model comparison.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Mapping, Sequence

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
sys.path.insert(0, str(_POC))
sys.path.insert(0, str(_POC / "src"))

from lloydk.hygiene import text_hash  # noqa: E402
from lloydk.proxy_corpus import record_text, validate_proxy_record  # noqa: E402
from scripts import assemble_public_s3_challenge as public_challenge  # noqa: E402


MANIFEST_SCHEMA = "public-s3-training-pool-manifest-v1"
COMPLETE_SCHEMA = "public-s3-training-pool-complete-v1"
ARTIFACT_KIND = "public_real_s3_training_pool"
DEFAULT_COUNT = 300
DEFAULT_SEED = "public-real-s3-training-v1"
MIN_BLOCKED_CORPORA = 2
ALLOWED_LICENSES = frozenset({"KOGL-0", "KOGL-1", "KOGL-AI"})
KOGL_1_TRAINING_POLICY = "training_eligible_with_source_attribution"
ATTRIBUTION_SCHEMA = "kogl-source-attribution-v1"
TRAINING_PERMISSION_ISSUER = "Korea Culture Information Service"
TRAINING_PERMISSION_TITLE = "2025 Q3 public-copyright issue report"
TRAINING_PERMISSION_RULE = (
    "KOG-L type 1 text is usable for AI training with source attribution"
)
TRAINING_PERMISSION_URL = (
    "https://www.kogl.or.kr/namoEditor/binary/files/000001/"
    "2025%EB%85%84_3%EB%B6%84%EA%B8%B0_"
    "%EA%B3%B5%EA%B3%B5%EC%A0%80%EC%9E%91%EB%AC%BC_"
    "%EC%9D%B4%EC%8A%88%EB%A6%AC%ED%8F%AC%ED%8A%B8_-_AI_"
    "%EC%8B%9C%EB%8C%80_%EA%B3%B5%EA%B3%B5%EC%A0%80%EC%9E%91%EB%AC%BC%"
    "EC%9D%B4_%EB%82%98%EC%95%84%EA%B0%80%EC%95%BC_%ED%95%A0_"
    "%EB%B0%A9%ED%96%A5_4.pdf"
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class PublicTrainingAssemblyError(ValueError):
    """The training pool cannot be assembled without weakening its contract."""


class IneligibleTrainingRecord(PublicTrainingAssemblyError):
    """One source row is ineligible while the collection envelope remains valid."""


@dataclass(frozen=True)
class LoadedTrainingInputs:
    loaded: public_challenge.LoadedInputs
    run_policies: Mapping[str, Mapping[str, object]]
    files: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class TrainingPoolAssembly:
    selected: tuple[dict[str, object], ...]
    manifest: dict[str, object]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return public_challenge._canonical_json_bytes(value)


def _canonical_jsonl_bytes(rows: Sequence[Mapping[str, object]]) -> bytes:
    return public_challenge._canonical_jsonl_bytes(rows)


def _require_sha256(value: object, *, field: str, location: str) -> str:
    digest = str(value or "").strip()
    if not _SHA256.fullmatch(digest):
        raise PublicTrainingAssemblyError(f"invalid {field} at {location}")
    return digest


def _strict_json_object(payload: bytes, *, location: str) -> dict[str, object]:
    try:
        decoded = payload.decode("utf-8")
        value = public_challenge._loads_strict(decoded, location=location)
    except UnicodeError as exc:
        raise PublicTrainingAssemblyError(f"invalid UTF-8 at {location}") from exc
    if not isinstance(value, dict):
        raise PublicTrainingAssemblyError(f"JSON object required at {location}")
    return value


def _policy_for_training(
    manifest: Mapping[str, object], *, location: str
) -> Mapping[str, object]:
    policy = manifest.get("policy")
    if not isinstance(policy, Mapping):
        raise PublicTrainingAssemblyError(f"collection policy is missing: {location}")
    accepted = policy.get("accepted_license_markers")
    if not isinstance(accepted, list) or not ALLOWED_LICENSES <= set(accepted):
        raise PublicTrainingAssemblyError(
            f"collection policy lacks the required training licence allowlist: {location}"
        )
    if policy.get("kogl_1_training_policy") != KOGL_1_TRAINING_POLICY:
        raise PublicTrainingAssemblyError(
            f"collection policy lacks the KOG-L 1 training rule: {location}"
        )
    evidence = policy.get("training_permission_evidence")
    if not isinstance(evidence, Mapping):
        raise PublicTrainingAssemblyError(
            f"collection policy lacks training permission evidence: {location}"
        )
    required_evidence = {
        "issuer": TRAINING_PERMISSION_ISSUER,
        "title": TRAINING_PERMISSION_TITLE,
        "url": TRAINING_PERMISSION_URL,
        "rule": TRAINING_PERMISSION_RULE,
        "attribution_required": True,
    }
    if any(evidence.get(key) != value for key, value in required_evidence.items()):
        raise PublicTrainingAssemblyError(
            f"invalid KOG-L 1 training permission evidence: {location}"
        )
    return dict(policy)


def load_training_inputs(paths: Sequence[Path]) -> LoadedTrainingInputs:
    """Load collection runs and re-attest their exact records/manifest bytes."""
    try:
        loaded = public_challenge.load_inputs(paths)
    except public_challenge.ChallengeAssemblyError as exc:
        raise PublicTrainingAssemblyError(str(exc)) from exc

    policies: dict[str, Mapping[str, object]] = {}
    files: list[dict[str, object]] = []
    by_run = {str(item["run_id"]): item for item in loaded.files}
    if len(by_run) != len(loaded.files):
        raise PublicTrainingAssemblyError(
            "duplicate collection run_id across training inputs"
        )
    for path in paths:
        try:
            records_payload = path.read_bytes()
            manifest_path = path.with_name("manifest.json")
            manifest_payload = manifest_path.read_bytes()
            manifest = _strict_json_object(
                manifest_payload, location=str(manifest_path)
            )
        except OSError as exc:
            raise PublicTrainingAssemblyError(
                f"cannot re-read collection envelope for {path}: {exc}"
            ) from exc
        run_id = str(manifest.get("run_id") or "").strip()
        if run_id in policies:
            raise PublicTrainingAssemblyError(
                f"duplicate collection run_id across training inputs: {run_id}"
            )
        source_audit = by_run.get(run_id)
        if source_audit is None:
            raise PublicTrainingAssemblyError(
                f"collection run disappeared from loaded input audit: {run_id}"
            )
        if source_audit.get("records_sha256") != _sha256_bytes(records_payload):
            raise PublicTrainingAssemblyError(
                f"collection records changed during verification: {path}"
            )
        if source_audit.get("run_manifest_sha256") != _sha256_bytes(manifest_payload):
            raise PublicTrainingAssemblyError(
                f"collection manifest changed during verification: {manifest_path}"
            )
        policies[run_id] = _policy_for_training(manifest, location=str(manifest_path))
        files.append(
            {
                **dict(source_audit),
                "records_path": str(path.resolve()),
                "records_bytes": len(records_payload),
                "records_sha256": _sha256_bytes(records_payload),
                "run_manifest_path": str(manifest_path.resolve()),
                "run_manifest_bytes": len(manifest_payload),
                "run_manifest_sha256": _sha256_bytes(manifest_payload),
            }
        )
    return LoadedTrainingInputs(
        loaded=loaded,
        run_policies=policies,
        files=tuple(files),
    )


def _validate_training_provenance(
    loaded: public_challenge.LoadedRow,
    *,
    policy: Mapping[str, object],
) -> None:
    row = loaded.record
    location = f"{loaded.input_path}:{loaded.line_number}"
    if row.get("collection_schema") != public_challenge.COLLECTION_SCHEMA:
        raise PublicTrainingAssemblyError(f"unexpected collection schema at {location}")
    if row.get("source_id") != public_challenge.SOURCE_ID:
        raise PublicTrainingAssemblyError(f"unexpected source_id at {location}")
    if row.get("document_origin") != "public_real" or row.get("label") != "S3":
        raise PublicTrainingAssemblyError(
            f"training pool accepts only public_real S3 at {location}"
        )
    if row.get("training_use_permitted") is not True:
        raise IneligibleTrainingRecord(f"training_use_not_permitted at {location}")

    try:
        reference, news_id = public_challenge._source_url_parts(row, location=location)
    except public_challenge.ChallengeAssemblyError as exc:
        raise PublicTrainingAssemblyError(str(exc)) from exc
    match = public_challenge._DOC_ID.fullmatch(str(row.get("doc_id") or ""))
    if match is None or match.group(1) != news_id:
        raise PublicTrainingAssemblyError(
            f"doc_id/source newsId mismatch at {location}"
        )
    if row.get("document_family_id") != f"korea-policy-{news_id}":
        raise PublicTrainingAssemblyError(
            f"family/source newsId mismatch at {location}"
        )
    section_index = int(match.group(2))
    if row.get("section_index") != section_index:
        raise PublicTrainingAssemblyError(f"section index mismatch at {location}")

    source_hash = _require_sha256(
        row.get("source_sha256"), field="source_sha256", location=location
    )
    raw_hash = _require_sha256(
        row.get("raw_html_sha256"), field="raw_html_sha256", location=location
    )
    if source_hash != raw_hash:
        raise PublicTrainingAssemblyError(f"source/raw hash mismatch at {location}")
    licence_hash = _require_sha256(
        row.get("license_evidence_sha256"),
        field="license_evidence_sha256",
        location=location,
    )
    licence = str(row.get("source_license") or "")
    if licence not in ALLOWED_LICENSES:
        raise PublicTrainingAssemblyError(
            f"unsupported training licence {licence!r} at {location}"
        )
    if (
        row.get("license_status") != "training_eligible"
        or not str(row.get("license_exact_snippet") or "").strip()
    ):
        raise PublicTrainingAssemblyError(
            f"missing training-eligible item licence at {location}"
        )

    page = loaded.source_page
    expected = {
        "source_reference": reference,
        "source_title": str(row.get("source_title") or ""),
        "source_agency": str(row.get("source_agency") or ""),
        "published_at": str(row.get("published_at") or ""),
        "raw_html_sha256": raw_hash,
        "retrieved_at": str(row.get("retrieved_at") or ""),
        "license_code": licence,
        "license_evidence_sha256": licence_hash,
        "license_exact_snippet": str(row.get("license_exact_snippet") or ""),
        "license_status": "training_eligible",
        "training_use_permitted": True,
        "evaluation_use_permitted": row.get("evaluation_use_permitted"),
    }
    for key, value in expected.items():
        if page.get(key) != value:
            raise PublicTrainingAssemblyError(
                f"record/page provenance mismatch for {key} at {location}"
            )
    if (
        page.get("status") != "accepted"
        or int(page.get("section_count") or 0) < section_index
    ):
        raise PublicTrainingAssemblyError(
            f"source page did not accept this section at {location}"
        )
    permission_basis = str(page.get("permission_basis") or "").strip()
    if not permission_basis:
        raise PublicTrainingAssemblyError(
            f"source page lacks a training permission basis at {location}"
        )
    licence_html = str(page.get("license_exact_html") or "")
    if not licence_html or _sha256_bytes(licence_html.encode("utf-8")) != licence_hash:
        raise PublicTrainingAssemblyError(
            f"item licence evidence bytes/hash mismatch at {location}"
        )
    if licence == "KOGL-1":
        if policy.get("kogl_1_training_policy") != KOGL_1_TRAINING_POLICY:
            raise PublicTrainingAssemblyError(
                f"KOG-L 1 training policy missing at {location}"
            )
        evidence = policy.get("training_permission_evidence")
        if (
            not isinstance(evidence, Mapping)
            or evidence.get("attribution_required") is not True
        ):
            raise PublicTrainingAssemblyError(
                f"KOG-L 1 attribution evidence missing at {location}"
            )

    run_dir = Path(loaded.input_path).parent
    try:
        raw_path = public_challenge._resolve_run_file(
            run_dir,
            page.get("raw_html_path"),
            field="raw_html_path",
            location=location,
        )
        text_path = public_challenge._resolve_run_file(
            run_dir,
            page.get("text_path"),
            field="text_path",
            location=location,
        )
        raw_payload = raw_path.read_bytes()
        extracted_body = text_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, public_challenge.ChallengeAssemblyError) as exc:
        raise PublicTrainingAssemblyError(
            f"cannot verify source artifacts at {location}: {exc}"
        ) from exc
    if _sha256_bytes(raw_payload) != raw_hash:
        raise PublicTrainingAssemblyError(
            f"raw source bytes/hash mismatch at {location}"
        )
    if licence_html.encode("utf-8") not in raw_payload:
        raise PublicTrainingAssemblyError(
            f"item licence evidence is not present in raw source at {location}"
        )
    if extracted_body.endswith("\n"):
        extracted_body = extracted_body[:-1]
    body_start = row.get("body_start")
    body_end = row.get("body_end")
    if (
        not isinstance(body_start, int)
        or isinstance(body_start, bool)
        or not isinstance(body_end, int)
        or isinstance(body_end, bool)
        or not 0 <= body_start < body_end <= len(extracted_body)
        or extracted_body[body_start:body_end] != record_text(row)
    ):
        raise PublicTrainingAssemblyError(
            f"record text/source offsets mismatch at {location}"
        )

    candidate = validate_proxy_record(row, stage="candidate", intended_use="training")
    eligible = validate_proxy_record(row, stage="eligible", intended_use="training")
    errors = [*candidate.errors, *eligible.errors]
    if errors:
        raise IneligibleTrainingRecord(
            "proxy_training_validation_failed:"
            + ",".join(sorted(set(errors)))
            + f" at {location}"
        )


def _attribution(
    row: Mapping[str, object], page: Mapping[str, object]
) -> dict[str, object]:
    licence = str(row["source_license"])
    agency = re.sub(r"\s+", " ", str(row["source_agency"])).strip()
    title = re.sub(r"\s+", " ", str(row["source_title"])).strip()
    reference = str(row["source_reference"])
    published_at = str(row.get("published_at") or "")
    required = licence != "KOGL-0"
    rendered = (
        f"출처: {agency}, 「{title}」, 정책브리핑, {published_at}, "
        f"{reference}, {licence}"
    )
    return {
        "schema": ATTRIBUTION_SCHEMA,
        "required": required,
        "source_agency": agency,
        "source_title": title,
        "source_reference": reference,
        "published_at": published_at,
        "source_license": licence,
        "license_evidence_sha256": str(row["license_evidence_sha256"]),
        "rendered_text": rendered,
        "permission_basis": str(page["permission_basis"]),
    }


def _prepare_training_row(
    loaded: public_challenge.LoadedRow,
    *,
    policy: Mapping[str, object],
) -> dict[str, object]:
    row = dict(loaded.record)
    page = loaded.source_page
    row.update(
        {
            "artifact_intended_use": "training_only",
            "artifact_training_use_permitted": True,
            "artifact_evaluation_use_permitted": False,
            "evaluation_pool_use_prohibited": True,
            "source_evaluation_use_permitted": row.get("evaluation_use_permitted"),
            "source_permission_basis": str(page["permission_basis"]),
            "license_exact_html": str(page["license_exact_html"]),
            "source_attribution": _attribution(row, page),
            "training_permission_evidence": dict(
                policy.get("training_permission_evidence") or {}
            ),
        }
    )
    return row


def _blocked_reasons(
    row: Mapping[str, object], blocked: public_challenge.LoadedBlockedCorpora
) -> list[str]:
    reasons: list[str] = []
    if str(row["doc_id"]).strip() in blocked.doc_ids:
        reasons.append("doc_id_overlap")
    if str(row["document_family_id"]).strip() in blocked.document_family_ids:
        reasons.append("document_family_id_overlap")
    if text_hash(record_text(row)) in blocked.normalized_text_hashes:
        reasons.append("normalized_text_overlap")
    return reasons


def assemble_training_pool(
    inputs: LoadedTrainingInputs,
    *,
    blocked: public_challenge.LoadedBlockedCorpora,
    count: int = DEFAULT_COUNT,
    seed: str = DEFAULT_SEED,
    document_type_targets: Mapping[str, int] | None = None,
    assembled_at: str | None = None,
) -> TrainingPoolAssembly:
    if count < 1:
        raise PublicTrainingAssemblyError("count must be positive")
    if not seed:
        raise PublicTrainingAssemblyError("seed must not be empty")
    if len(blocked.files) < MIN_BLOCKED_CORPORA:
        raise PublicTrainingAssemblyError(
            "both development and blind public holdouts are required as blocked corpora"
        )
    normalized_type_targets: dict[str, int] | None = None
    if document_type_targets is not None:
        normalized_type_targets = {}
        for raw_type, raw_count in document_type_targets.items():
            document_type = str(raw_type).strip()
            if (
                not document_type
                or isinstance(raw_count, bool)
                or not isinstance(raw_count, int)
                or raw_count < 1
            ):
                raise PublicTrainingAssemblyError(
                    "document type targets require non-empty names and positive integers"
                )
            normalized_type_targets[document_type] = raw_count
        if sum(normalized_type_targets.values()) != count:
            raise PublicTrainingAssemblyError(
                "document type target counts must sum to the requested pool count"
            )

    rows: list[dict[str, object]] = []
    seen_doc_ids: dict[str, str] = {}
    seen_text_hashes: dict[str, str] = {}
    source_bindings: dict[str, tuple[str, str, str]] = {}
    ineligible_counts: Counter[str] = Counter()
    duplicate_doc_ids = 0
    duplicate_texts = 0
    blocked_records = 0
    blocked_reason_counts: Counter[str] = Counter()
    blocked_combination_counts: Counter[str] = Counter()
    for loaded_row in inputs.loaded.rows:
        policy = inputs.run_policies.get(loaded_row.run_id)
        if policy is None:
            raise PublicTrainingAssemblyError(
                f"missing collection policy for run {loaded_row.run_id}"
            )
        try:
            _validate_training_provenance(loaded_row, policy=policy)
        except IneligibleTrainingRecord as exc:
            reason = str(exc).split(" at ", 1)[0]
            ineligible_counts[reason] += 1
            continue
        row = _prepare_training_row(loaded_row, policy=policy)
        reasons = _blocked_reasons(row, blocked)
        if reasons:
            blocked_records += 1
            blocked_reason_counts.update(reasons)
            blocked_combination_counts["+".join(reasons)] += 1
            continue

        doc_id = str(row["doc_id"]).strip()
        digest = text_hash(record_text(row))
        source_reference = str(row["source_reference"])
        binding = (
            str(row["source_sha256"]),
            str(row["source_license"]),
            str(row["license_evidence_sha256"]),
        )
        prior_binding = source_bindings.setdefault(source_reference, binding)
        if prior_binding != binding:
            raise PublicTrainingAssemblyError(
                f"conflicting provenance for {source_reference}"
            )
        if doc_id in seen_doc_ids:
            if seen_doc_ids[doc_id] != digest:
                raise PublicTrainingAssemblyError(
                    f"conflicting duplicate doc_id: {doc_id}"
                )
            duplicate_doc_ids += 1
            continue
        if digest in seen_text_hashes:
            duplicate_texts += 1
            continue
        seen_doc_ids[doc_id] = digest
        seen_text_hashes[digest] = doc_id
        rows.append(row)

    try:
        if normalized_type_targets is None:
            selected = public_challenge._select_diverse(
                rows,
                count=count,
                seed=seed,
                max_sections_per_family=1,
            )
        else:
            selected = []
            for document_type, target in sorted(normalized_type_targets.items()):
                candidates = [
                    row
                    for row in rows
                    if str(row.get("document_type") or "").strip() == document_type
                ]
                type_selected = public_challenge._select_diverse(
                    candidates,
                    count=target,
                    seed=f"{seed}:document_type:{document_type}",
                    max_sections_per_family=1,
                )
                if len(type_selected) != target:
                    available_families = len(
                        {
                            str(row.get("document_family_id") or "").strip()
                            for row in candidates
                        }
                    )
                    raise PublicTrainingAssemblyError(
                        "insufficient eligible records for document type quota: "
                        f"type={document_type}, needed={target}, "
                        f"selected={len(type_selected)}, families={available_families}"
                    )
                selected.extend(type_selected)
    except public_challenge.ChallengeAssemblyError as exc:
        raise PublicTrainingAssemblyError(str(exc)) from exc
    if len(selected) != count:
        families = len({str(row["document_family_id"]) for row in rows})
        raise PublicTrainingAssemblyError(
            "insufficient eligible public S3 training records after holdout blocking: "
            f"needed={count}, selected={len(selected)}, candidates={len(rows)}, "
            f"families={families}"
        )

    selected.sort(key=lambda row: str(row["doc_id"]))
    doc_ids = [str(row["doc_id"]) for row in selected]
    families = [str(row["document_family_id"]) for row in selected]
    text_hashes = [text_hash(record_text(row)) for row in selected]
    if not (len(set(doc_ids)) == len(set(families)) == len(set(text_hashes)) == count):
        raise PublicTrainingAssemblyError(
            "selected training output is not unique by doc_id, family, and text"
        )
    for row in selected:
        check = validate_proxy_record(row, stage="eligible", intended_use="training")
        attribution = row.get("source_attribution")
        if (
            not check.ok
            or row.get("artifact_evaluation_use_permitted") is not False
            or row.get("evaluation_pool_use_prohibited") is not True
            or not isinstance(attribution, Mapping)
            or attribution.get("schema") != ATTRIBUTION_SCHEMA
            or not str(attribution.get("rendered_text") or "").strip()
        ):
            raise PublicTrainingAssemblyError(
                f"selected row failed final training-only validation: {row['doc_id']}"
            )

    assembled_at = assembled_at or datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    manifest: dict[str, object] = {
        "schema": MANIFEST_SCHEMA,
        "artifact_kind": ARTIFACT_KIND,
        "status": "complete",
        "assembled_at": assembled_at,
        "intended_use": "training_only",
        "artifact_training_use_permitted": True,
        "artifact_evaluation_use_permitted": False,
        "evaluation_pool_use_prohibited": True,
        "claim_boundary": {
            "allowed": ["model training", "training corpus composition audit"],
            "forbidden": [
                "evaluation",
                "golden-set scoring",
                "model selection",
                "accuracy or customer-real accuracy claims",
            ],
            "must_not_overlap_evaluation_holdouts": True,
        },
        "selection": {
            "target_count": count,
            "seed": seed,
            "strategy": (
                "exact document-type strata, then seeded agency round-robin with "
                "rotating document-length bins"
                if normalized_type_targets is not None
                else "seeded agency round-robin with rotating document-length bins"
            ),
            "document_type_targets": (
                dict(sorted(normalized_type_targets.items()))
                if normalized_type_targets is not None
                else None
            ),
            "max_sections_per_family": 1,
            "one_record_per_family": True,
        },
        "validation": {
            "stages": ["candidate", "eligible"],
            "intended_use": "training",
            "stored_validation_fields_trusted": False,
            "item_level_page_ledger_binding_required": True,
            "raw_source_hash_and_offsets_reverified": True,
            "license_evidence_hash_reverified": True,
            "minimum_blocked_holdout_artifacts": MIN_BLOCKED_CORPORA,
        },
        "inputs": list(inputs.files),
        "blocked_corpora": {
            "identity_keys": [
                "doc_id",
                "document_family_id",
                "normalized_text_hash",
            ],
            "inputs": list(blocked.files),
            "input_artifacts": len(blocked.files),
            "input_rows": blocked.row_count,
            "union_uniqueness": {
                "unique_doc_ids": len(blocked.doc_ids),
                "unique_document_family_ids": len(blocked.document_family_ids),
                "unique_normalized_text_hashes": len(blocked.normalized_text_hashes),
            },
            "excluded_before_selection": {
                "records": blocked_records,
                "reason_counts": dict(sorted(blocked_reason_counts.items())),
                "reason_combination_counts": dict(
                    sorted(blocked_combination_counts.items())
                ),
            },
        },
        "input_summary": {
            "rows": len(inputs.loaded.rows),
            "eligible_unique_candidates_after_blocking": len(rows),
            "ineligible_rows_excluded": sum(ineligible_counts.values()),
            "ineligible_reason_counts": dict(sorted(ineligible_counts.items())),
            "blocked_overlap_records_excluded": blocked_records,
            "duplicate_doc_ids_excluded": duplicate_doc_ids,
            "duplicate_texts_excluded": duplicate_texts,
        },
        "distribution": {"S3": count},
        "document_origin_counts": {"public_real": count},
        "family_counts": {"records": count, "unique_document_families": count},
        "source_agency_counts": dict(
            sorted(Counter(str(row["source_agency"]) for row in selected).items())
        ),
        "document_type_counts": dict(
            sorted(Counter(str(row["document_type"]) for row in selected).items())
        ),
        "family_profile_counts": dict(
            sorted(Counter(str(row["family_profile_id"]) for row in selected).items())
        ),
        "published_at_range": {
            "minimum": min(str(row["published_at"]) for row in selected),
            "maximum": max(str(row["published_at"]) for row in selected),
        },
        "license_counts": dict(
            sorted(Counter(str(row["source_license"]) for row in selected).items())
        ),
        "attribution": {
            "schema": ATTRIBUTION_SCHEMA,
            "records_with_rendered_attribution": sum(
                bool(row["source_attribution"]["rendered_text"]) for row in selected
            ),
            "records_where_attribution_required": sum(
                row["source_attribution"]["required"] is True for row in selected
            ),
            "source_fields_preserved": [
                "source_agency",
                "source_title",
                "source_reference",
                "published_at",
                "source_license",
                "license_exact_html",
                "license_exact_snippet",
                "license_evidence_sha256",
            ],
        },
        "document_length_bin_counts": dict(
            sorted(
                Counter(
                    public_challenge._length_bin(record_text(row)) for row in selected
                ).items()
            )
        ),
        "uniqueness": {
            "unique_doc_ids": count,
            "unique_document_family_ids": count,
            "unique_normalized_text_hashes": count,
        },
    }
    return TrainingPoolAssembly(selected=tuple(selected), manifest=manifest)


def _atomic_publish_directory(
    output_dir: Path, *, files: Sequence[tuple[str, bytes]]
) -> None:
    if output_dir.exists():
        raise PublicTrainingAssemblyError(
            f"refusing to overwrite artifact directory: {output_dir}"
        )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
    )
    try:
        for name, payload in files:
            with (staging / name).open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        os.rename(staging, output_dir)
    finally:
        if staging.exists():
            for child in staging.iterdir():
                if child.is_file():
                    child.unlink()
            staging.rmdir()


def publish_training_pool(
    output_dir: Path, assembly: TrainingPoolAssembly
) -> dict[str, object]:
    records_payload = _canonical_jsonl_bytes(assembly.selected)
    manifest = dict(assembly.manifest)
    artifact_id = output_dir.name
    manifest.update(
        {
            "artifact_id": artifact_id,
            "artifact": {
                "records_path": "records.jsonl",
                "records": len(assembly.selected),
                "records_bytes": len(records_payload),
                "records_sha256": _sha256_bytes(records_payload),
            },
        }
    )
    manifest_payload = _canonical_json_bytes(manifest)
    complete = {
        "schema": COMPLETE_SCHEMA,
        "artifact_id": artifact_id,
        "status": "committed",
        "committed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "manifest_path": "manifest.json",
        "manifest_bytes": len(manifest_payload),
        "manifest_sha256": _sha256_bytes(manifest_payload),
        "records_path": "records.jsonl",
        "records": len(assembly.selected),
        "records_bytes": len(records_payload),
        "records_sha256": _sha256_bytes(records_payload),
        "intended_use": "training_only",
        "artifact_evaluation_use_permitted": False,
    }
    complete_payload = _canonical_json_bytes(complete)
    _atomic_publish_directory(
        output_dir,
        files=(
            ("records.jsonl", records_payload),
            ("manifest.json", manifest_payload),
            ("COMPLETE.json", complete_payload),
        ),
    )
    return manifest


def _require_nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PublicTrainingAssemblyError(f"invalid count: {field}")
    return value


def load_public_s3_training_pool(
    path: Path,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Verify and load one committed training-only artifact directory."""
    root = path.resolve()
    if path.is_symlink() or not root.is_dir():
        raise PublicTrainingAssemblyError(
            f"public S3 training pool must be a regular directory: {path}"
        )
    records_path = root / "records.jsonl"
    manifest_path = root / "manifest.json"
    complete_path = root / "COMPLETE.json"
    for artifact_path in (records_path, manifest_path, complete_path):
        if artifact_path.is_symlink() or not artifact_path.is_file():
            raise PublicTrainingAssemblyError(
                f"training pool envelope is incomplete: {artifact_path}"
            )
    try:
        located_rows, records_payload = public_challenge._read_records(records_path)
        manifest_payload = manifest_path.read_bytes()
        complete_payload = complete_path.read_bytes()
        manifest = _strict_json_object(manifest_payload, location=str(manifest_path))
        complete = _strict_json_object(complete_payload, location=str(complete_path))
    except (OSError, public_challenge.ChallengeAssemblyError) as exc:
        raise PublicTrainingAssemblyError(
            f"cannot read public S3 training pool: {exc}"
        ) from exc

    if (
        manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("artifact_kind") != ARTIFACT_KIND
        or manifest.get("status") != "complete"
        or manifest.get("intended_use") != "training_only"
        or manifest.get("artifact_training_use_permitted") is not True
        or manifest.get("artifact_evaluation_use_permitted") is not False
        or manifest.get("evaluation_pool_use_prohibited") is not True
    ):
        raise PublicTrainingAssemblyError(
            "public S3 training manifest contract is invalid"
        )
    artifact_id = str(manifest.get("artifact_id") or "")
    if not artifact_id or artifact_id != root.name:
        raise PublicTrainingAssemblyError("training artifact_id/path mismatch")
    artifact = manifest.get("artifact")
    if (
        not isinstance(artifact, Mapping)
        or artifact.get("records_path") != "records.jsonl"
    ):
        raise PublicTrainingAssemblyError(
            "training manifest artifact descriptor is invalid"
        )
    actual_records_hash = _sha256_bytes(records_payload)
    actual_manifest_hash = _sha256_bytes(manifest_payload)
    row_count = len(located_rows)
    if row_count < 1:
        raise PublicTrainingAssemblyError("training pool must not be empty")
    if (
        _require_nonnegative_int(artifact.get("records"), field="artifact.records")
        != row_count
        or _require_nonnegative_int(
            artifact.get("records_bytes"), field="artifact.records_bytes"
        )
        != len(records_payload)
        or _require_sha256(
            artifact.get("records_sha256"),
            field="artifact.records_sha256",
            location=str(manifest_path),
        )
        != actual_records_hash
    ):
        raise PublicTrainingAssemblyError(
            "training records do not match the manifest attestation"
        )
    if (
        complete.get("schema") != COMPLETE_SCHEMA
        or complete.get("artifact_id") != artifact_id
        or complete.get("status") != "committed"
        or complete.get("manifest_path") != "manifest.json"
        or complete.get("records_path") != "records.jsonl"
        or complete.get("intended_use") != "training_only"
        or complete.get("artifact_evaluation_use_permitted") is not False
        or complete.get("manifest_sha256") != actual_manifest_hash
        or complete.get("records_sha256") != actual_records_hash
        or complete.get("manifest_bytes") != len(manifest_payload)
        or complete.get("records_bytes") != len(records_payload)
        or complete.get("records") != row_count
    ):
        raise PublicTrainingAssemblyError(
            "training COMPLETE marker does not attest exact artifacts"
        )

    rows = [dict(row) for _, row in located_rows]
    doc_ids: set[str] = set()
    families: set[str] = set()
    hashes: set[str] = set()
    for index, row in enumerate(rows, 1):
        check = validate_proxy_record(row, stage="eligible", intended_use="training")
        attribution = row.get("source_attribution")
        licence_html = str(row.get("license_exact_html") or "")
        licence_hash = str(row.get("license_evidence_sha256") or "")
        permission_basis = str(row.get("source_permission_basis") or "")
        expected_attribution = (
            _attribution(row, {"permission_basis": permission_basis})
            if isinstance(attribution, Mapping)
            else None
        )
        permission_evidence = row.get("training_permission_evidence")
        if (
            not check.ok
            or row.get("label") != "S3"
            or row.get("document_origin") != "public_real"
            or row.get("source_license") not in ALLOWED_LICENSES
            or row.get("training_use_permitted") is not True
            or row.get("artifact_intended_use") != "training_only"
            or row.get("artifact_training_use_permitted") is not True
            or row.get("artifact_evaluation_use_permitted") is not False
            or row.get("evaluation_pool_use_prohibited") is not True
            or not isinstance(attribution, Mapping)
            or attribution.get("schema") != ATTRIBUTION_SCHEMA
            or dict(attribution) != expected_attribution
            or not licence_html
            or _sha256_bytes(licence_html.encode("utf-8")) != licence_hash
            or not str(row.get("license_exact_snippet") or "").strip()
            or not permission_basis
            or not isinstance(permission_evidence, Mapping)
            or permission_evidence.get("issuer") != TRAINING_PERMISSION_ISSUER
            or permission_evidence.get("title") != TRAINING_PERMISSION_TITLE
            or permission_evidence.get("url") != TRAINING_PERMISSION_URL
            or permission_evidence.get("rule") != TRAINING_PERMISSION_RULE
            or permission_evidence.get("attribution_required") is not True
        ):
            raise PublicTrainingAssemblyError(
                f"training pool row contract failed at row {index}"
            )
        doc_ids.add(str(row["doc_id"]).strip())
        families.add(str(row["document_family_id"]).strip())
        hashes.add(text_hash(record_text(row)))
    if not len(doc_ids) == len(families) == len(hashes) == row_count:
        raise PublicTrainingAssemblyError(
            "training pool rows are not unique by doc_id, family, and text"
        )
    uniqueness = manifest.get("uniqueness")
    expected_uniqueness = {
        "unique_doc_ids": row_count,
        "unique_document_family_ids": row_count,
        "unique_normalized_text_hashes": row_count,
    }
    if uniqueness != expected_uniqueness or manifest.get("distribution") != {
        "S3": row_count
    }:
        raise PublicTrainingAssemblyError(
            "training manifest uniqueness/distribution mismatch"
        )
    blocked = manifest.get("blocked_corpora")
    blocked_inputs = blocked.get("inputs") if isinstance(blocked, Mapping) else None
    if (
        not isinstance(blocked, Mapping)
        or blocked.get("identity_keys")
        != ["doc_id", "document_family_id", "normalized_text_hash"]
        or not isinstance(blocked_inputs, list)
        or blocked.get("input_artifacts") != len(blocked_inputs)
        or len(blocked_inputs) < MIN_BLOCKED_CORPORA
    ):
        raise PublicTrainingAssemblyError(
            "training manifest lacks both attested evaluation holdout blockers"
        )
    for blocker_index, blocker in enumerate(blocked_inputs, 1):
        if (
            not isinstance(blocker, Mapping)
            or not str(blocker.get("manifest_path") or "").strip()
            or not str(blocker.get("records_path") or "").strip()
            or not _SHA256.fullmatch(str(blocker.get("manifest_sha256") or ""))
            or not _SHA256.fullmatch(str(blocker.get("records_sha256") or ""))
        ):
            raise PublicTrainingAssemblyError(
                f"invalid blocked holdout attestation at index {blocker_index}"
            )

    expected_origin_counts = {"public_real": row_count}
    expected_family_counts = {
        "records": row_count,
        "unique_document_families": row_count,
    }
    expected_license_counts = dict(
        sorted(Counter(str(row["source_license"]) for row in rows).items())
    )
    expected_agency_counts = dict(
        sorted(Counter(str(row["source_agency"]) for row in rows).items())
    )
    expected_document_type_counts = dict(
        sorted(Counter(str(row["document_type"]) for row in rows).items())
    )
    expected_family_profile_counts = dict(
        sorted(Counter(str(row["family_profile_id"]) for row in rows).items())
    )
    expected_published_at_range = {
        "minimum": min(str(row["published_at"]) for row in rows),
        "maximum": max(str(row["published_at"]) for row in rows),
    }
    expected_length_counts = dict(
        sorted(
            Counter(
                public_challenge._length_bin(record_text(row)) for row in rows
            ).items()
        )
    )
    expected_attribution_counts = {
        "schema": ATTRIBUTION_SCHEMA,
        "records_with_rendered_attribution": row_count,
        "records_where_attribution_required": sum(
            row["source_attribution"]["required"] is True for row in rows
        ),
        "source_fields_preserved": [
            "source_agency",
            "source_title",
            "source_reference",
            "published_at",
            "source_license",
            "license_exact_html",
            "license_exact_snippet",
            "license_evidence_sha256",
        ],
    }
    if (
        manifest.get("document_origin_counts") != expected_origin_counts
        or manifest.get("family_counts") != expected_family_counts
        or manifest.get("license_counts") != expected_license_counts
        or manifest.get("source_agency_counts") != expected_agency_counts
        or manifest.get("document_length_bin_counts") != expected_length_counts
        or manifest.get("attribution") != expected_attribution_counts
    ):
        raise PublicTrainingAssemblyError(
            "training manifest row-derived counts mismatch"
        )
    selection = manifest.get("selection")
    type_targets = (
        selection.get("document_type_targets")
        if isinstance(selection, Mapping)
        else None
    )
    if type_targets is not None:
        if (
            not isinstance(type_targets, Mapping)
            or dict(type_targets) != expected_document_type_counts
            or manifest.get("document_type_counts")
            != expected_document_type_counts
            or manifest.get("family_profile_counts")
            != expected_family_profile_counts
            or manifest.get("published_at_range") != expected_published_at_range
        ):
            raise PublicTrainingAssemblyError(
                "training document-type diversity contract mismatch"
            )

    audit = {
        "schema": MANIFEST_SCHEMA,
        "artifact_kind": ARTIFACT_KIND,
        "path": str(root),
        "records_path": str(records_path),
        "records": row_count,
        "records_bytes": len(records_payload),
        "records_sha256": actual_records_hash,
        "manifest_path": str(manifest_path),
        "manifest_bytes": len(manifest_payload),
        "manifest_sha256": actual_manifest_hash,
        "complete_path": str(complete_path),
        "complete_bytes": len(complete_payload),
        "complete_sha256": _sha256_bytes(complete_payload),
        "intended_use": "training_only",
        "artifact_evaluation_use_permitted": False,
        **expected_uniqueness,
        "license_counts": expected_license_counts,
    }
    return rows, audit


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        type=Path,
        help="completed Korea policy collection run records.jsonl; repeatable",
    )
    parser.add_argument(
        "--blocked-corpus",
        action="append",
        required=True,
        type=Path,
        help=(
            "immutable public S3 evaluation challenge directory or records.jsonl; "
            "repeat for development and blind holdouts"
        ),
    )
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument(
        "--document-type-count",
        action="append",
        default=[],
        metavar="TYPE=COUNT",
        help=(
            "exact document-type stratum; repeat and make the counts sum to --count"
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("datasets/proxy_gold/public_s3_training/public-s3-train-300-v1"),
    )
    args = parser.parse_args(argv)

    try:
        if len(args.blocked_corpus) < MIN_BLOCKED_CORPORA:
            raise PublicTrainingAssemblyError(
                "at least two --blocked-corpus artifacts are required "
                "(development and blind holdouts)"
            )
        inputs = load_training_inputs(args.input)
        try:
            blocked = public_challenge.load_blocked_corpora(args.blocked_corpus)
        except public_challenge.ChallengeAssemblyError as exc:
            raise PublicTrainingAssemblyError(str(exc)) from exc
        type_targets: dict[str, int] | None = None
        if args.document_type_count:
            type_targets = {}
            for item in args.document_type_count:
                if "=" not in item:
                    raise PublicTrainingAssemblyError(
                        "--document-type-count must be TYPE=COUNT"
                    )
                document_type, raw_count = item.rsplit("=", 1)
                document_type = document_type.strip()
                try:
                    parsed_count = int(raw_count)
                except ValueError as exc:
                    raise PublicTrainingAssemblyError(
                        "--document-type-count COUNT must be an integer"
                    ) from exc
                if document_type in type_targets:
                    raise PublicTrainingAssemblyError(
                        f"duplicate document type target: {document_type}"
                    )
                type_targets[document_type] = parsed_count
        assembly = assemble_training_pool(
            inputs,
            blocked=blocked,
            count=args.count,
            seed=args.seed,
            document_type_targets=type_targets,
        )
        manifest = publish_training_pool(args.out_dir, assembly)
    except PublicTrainingAssemblyError as exc:
        print(json.dumps({"ready": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "ready": True,
                "output_dir": str(args.out_dir),
                "records": manifest["artifact"]["records"],
                "records_sha256": manifest["artifact"]["records_sha256"],
                "artifact_kind": ARTIFACT_KIND,
                "intended_use": "training_only",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
