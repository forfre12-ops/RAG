"""Assemble an immutable public-real S3 overclassification challenge.

This artifact is deliberately separate from the balanced proxy-gold set.  It
measures false positives / overclassification on real public documents only;
it cannot support overall accuracy, balanced accuracy, or customer accuracy
claims.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence
import urllib.parse


_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
sys.path.insert(0, str(_POC / "src"))

from lloydk.hygiene import text_hash  # noqa: E402
from lloydk.proxy_corpus import record_text, validate_proxy_record  # noqa: E402


SCHEMA = "public-s3-overclassification-challenge-manifest-v1"
ARTIFACT_KIND = "public_real_s3_overclassification_fpr_challenge"
SUITE_ID = "public-real-s3-overclassification-v1"
DEFAULT_COUNT = 300
DEFAULT_SEED = "public-real-s3-v1"
COLLECTION_SCHEMA = "korea-policy-public-proxy-v1"
RUN_SCHEMA = "korea-policy-public-proxy-run-v1"
SOURCE_ID = "korea-policy-briefing"
ALLOWED_LICENSES = frozenset({"KOGL-0", "KOGL-1", "KOGL-AI"})
ALLOWED_HOSTS = frozenset({"www.korea.kr", "m.korea.kr"})
ALLOWED_PATHS = frozenset(
    {
        "/briefing/pressReleaseView.do",
        "/news/policyNewsView.do",
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DOC_ID = re.compile(r"korea-policy-(\d+)-s(\d+)\Z")


class ChallengeAssemblyError(ValueError):
    """The challenge cannot be assembled without weakening its contract."""


class IneligibleChallengeRecord(ChallengeAssemblyError):
    """One input row is not eligible, but the run's provenance is still intact."""


@dataclass(frozen=True)
class LoadedRow:
    record: dict[str, object]
    input_path: str
    line_number: int
    run_id: str
    source_page: Mapping[str, object]


@dataclass(frozen=True)
class LoadedInputs:
    rows: tuple[LoadedRow, ...]
    files: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class LoadedBlockedCorpora:
    """Leakage identities and byte-level attestations for frozen challenges."""

    doc_ids: frozenset[str]
    document_family_ids: frozenset[str]
    normalized_text_hashes: frozenset[str]
    files: tuple[dict[str, object], ...]
    row_count: int


@dataclass(frozen=True)
class ChallengeAssembly:
    selected: tuple[dict[str, object], ...]
    manifest: dict[str, object]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _canonical_jsonl_bytes(rows: Iterable[Mapping[str, object]]) -> bytes:
    return b"".join(_canonical_json_bytes(dict(row)) for row in rows)


def _loads_strict(payload: str, *, location: str) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant {value}")

    try:
        return json.loads(payload, parse_constant=reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        detail = exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)
        raise ChallengeAssemblyError(f"malformed JSON at {location}: {detail}") from exc


def _require_sha256(value: object, *, field: str, location: str) -> str:
    digest = str(value or "").strip()
    if not _SHA256.fullmatch(digest):
        raise ChallengeAssemblyError(f"invalid {field} at {location}")
    return digest


def _read_run_manifest(records_path: Path) -> tuple[dict[str, object], bytes]:
    manifest_path = records_path.with_name("manifest.json")
    if not manifest_path.is_file():
        raise ChallengeAssemblyError(
            f"Korea policy run manifest is required beside {records_path}"
        )
    try:
        payload = manifest_path.read_bytes()
        manifest = _loads_strict(payload.decode("utf-8"), location=str(manifest_path))
    except (OSError, UnicodeError) as exc:
        raise ChallengeAssemblyError(f"cannot read {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ChallengeAssemblyError(f"run manifest must be an object: {manifest_path}")
    if manifest.get("schema") != RUN_SCHEMA:
        raise ChallengeAssemblyError(f"unexpected run schema: {manifest_path}")
    if manifest.get("mode") != "download":
        raise ChallengeAssemblyError(
            f"run is not a completed download: {manifest_path}"
        )
    run_id = str(manifest.get("run_id") or "").strip()
    if not run_id or run_id != records_path.parent.name:
        raise ChallengeAssemblyError(f"run_id/path mismatch: {manifest_path}")
    if not str(manifest.get("completed_at") or "").strip():
        raise ChallengeAssemblyError(f"run has no completion stamp: {manifest_path}")
    policy = manifest.get("policy")
    if (
        not isinstance(policy, Mapping)
        or policy.get("item_level_license_required") is not True
    ):
        raise ChallengeAssemblyError(
            f"run does not attest item-level licensing: {manifest_path}"
        )
    return manifest, payload


def _source_pages(manifest: Mapping[str, object], *, location: str) -> dict[str, dict]:
    pages = manifest.get("pages")
    if not isinstance(pages, list):
        raise ChallengeAssemblyError(f"run manifest has no page ledger: {location}")
    result: dict[str, dict] = {}
    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            raise ChallengeAssemblyError(
                f"page ledger row is not an object: {location}[{index}]"
            )
        news_id = str(page.get("news_id") or "").strip()
        if not news_id:
            raise ChallengeAssemblyError(
                f"page ledger row has no news_id: {location}[{index}]"
            )
        if news_id in result:
            raise ChallengeAssemblyError(
                f"duplicate page ledger news_id {news_id}: {location}"
            )
        result[news_id] = page
    return result


def _read_records(path: Path) -> tuple[list[tuple[int, dict[str, object]]], bytes]:
    if path.name != "records.jsonl" or not path.is_file():
        raise ChallengeAssemblyError(
            f"input must be a Korea policy run records.jsonl file: {path}"
        )
    try:
        payload = path.read_bytes()
        decoded = payload.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ChallengeAssemblyError(f"cannot read {path}: {exc}") from exc
    if not payload or not decoded.strip():
        raise ChallengeAssemblyError(f"empty input: {path}")
    if not payload.endswith(b"\n"):
        raise ChallengeAssemblyError(f"JSONL must end with a newline: {path}")
    rows: list[tuple[int, dict[str, object]]] = []
    for line_number, line in enumerate(decoded.splitlines(), 1):
        if not line:
            raise ChallengeAssemblyError(f"blank JSONL line at {path}:{line_number}")
        row = _loads_strict(line, location=f"{path}:{line_number}")
        if not isinstance(row, dict):
            raise ChallengeAssemblyError(
                f"record must be an object at {path}:{line_number}"
            )
        rows.append((line_number, row))
    return rows, payload


def _require_nonnegative_int(value: object, *, field: str, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ChallengeAssemblyError(f"invalid {field} at {location}")
    return value


def _resolve_blocked_artifact(path: Path) -> tuple[Path, Path]:
    if not path.exists():
        raise ChallengeAssemblyError(f"blocked corpus path does not exist: {path}")
    if path.is_dir():
        records_path = path / "records.jsonl"
        manifest_path = path / "manifest.json"
    elif path.is_file() and path.name == "records.jsonl":
        records_path = path
        manifest_path = path.with_name("manifest.json")
    else:
        raise ChallengeAssemblyError(
            "blocked corpus must be an immutable public S3 challenge directory "
            f"or its records.jsonl: {path}"
        )
    if records_path.is_symlink() or not records_path.is_file():
        raise ChallengeAssemblyError(
            f"blocked corpus records.jsonl is missing or not a regular file: {records_path}"
        )
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ChallengeAssemblyError(
            f"blocked corpus manifest.json is missing or not a regular file: {manifest_path}"
        )
    return records_path.resolve(), manifest_path.resolve()


def _read_blocked_manifest(path: Path) -> tuple[dict[str, object], bytes]:
    try:
        payload = path.read_bytes()
        decoded = payload.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ChallengeAssemblyError(
            f"cannot read blocked corpus manifest {path}: {exc}"
        ) from exc
    if not payload or not decoded.strip():
        raise ChallengeAssemblyError(f"empty blocked corpus manifest: {path}")
    manifest = _loads_strict(decoded, location=str(path))
    if not isinstance(manifest, dict):
        raise ChallengeAssemblyError(
            f"blocked corpus manifest must be an object: {path}"
        )
    return manifest, payload


def load_blocked_corpora(paths: Sequence[Path]) -> LoadedBlockedCorpora:
    """Load hash-attested public S3 challenges used as leakage boundaries."""
    all_doc_ids: set[str] = set()
    all_family_ids: set[str] = set()
    all_text_hashes: set[str] = set()
    files: list[dict[str, object]] = []
    seen_records_paths: set[Path] = set()
    total_rows = 0

    for requested_path in paths:
        records_path, manifest_path = _resolve_blocked_artifact(requested_path)
        if records_path in seen_records_paths:
            raise ChallengeAssemblyError(
                f"duplicate blocked corpus artifact: {requested_path}"
            )
        seen_records_paths.add(records_path)
        located_rows, records_payload = _read_records(records_path)
        manifest, manifest_payload = _read_blocked_manifest(manifest_path)
        location = str(manifest_path)

        if manifest.get("schema") != SCHEMA:
            raise ChallengeAssemblyError(
                f"unsupported blocked corpus manifest schema at {location}"
            )
        if manifest.get("artifact_kind") != ARTIFACT_KIND:
            raise ChallengeAssemblyError(
                f"unexpected blocked corpus artifact_kind at {location}"
            )
        if manifest.get("suite_id") != SUITE_ID:
            raise ChallengeAssemblyError(
                f"unexpected blocked corpus suite_id at {location}"
            )
        if (
            manifest.get("status") != "ready"
            or manifest.get("intended_use") != "evaluation_only"
        ):
            raise ChallengeAssemblyError(
                f"blocked corpus is not a ready evaluation artifact: {location}"
            )

        artifact = manifest.get("artifact")
        if not isinstance(artifact, Mapping):
            raise ChallengeAssemblyError(
                f"blocked corpus artifact descriptor is missing: {location}"
            )
        if artifact.get("records_path") != "records.jsonl":
            raise ChallengeAssemblyError(
                f"blocked corpus records_path is not canonical: {location}"
            )
        declared_records = _require_nonnegative_int(
            artifact.get("records"), field="artifact.records", location=location
        )
        declared_bytes = _require_nonnegative_int(
            artifact.get("records_bytes"),
            field="artifact.records_bytes",
            location=location,
        )
        declared_hash = _require_sha256(
            artifact.get("records_sha256"),
            field="artifact.records_sha256",
            location=location,
        )
        if declared_records != len(located_rows):
            raise ChallengeAssemblyError(
                f"blocked corpus record count mismatch: {location}"
            )
        if declared_bytes != len(records_payload):
            raise ChallengeAssemblyError(
                f"blocked corpus byte count mismatch: {location}"
            )
        if declared_hash != _sha256_bytes(records_payload):
            raise ChallengeAssemblyError(
                f"blocked corpus records SHA-256 mismatch: {location}"
            )
        if not located_rows:
            raise ChallengeAssemblyError(
                f"blocked corpus must not be empty: {records_path}"
            )

        doc_ids: list[str] = []
        family_ids: list[str] = []
        normalized_text_hashes: list[str] = []
        for line_number, row in located_rows:
            row_location = f"{records_path}:{line_number}"
            doc_id = row.get("doc_id")
            family_id = row.get("document_family_id")
            text = record_text(row)
            if not isinstance(doc_id, str) or not doc_id.strip():
                raise ChallengeAssemblyError(
                    f"blocked corpus record has no doc_id at {row_location}"
                )
            if not isinstance(family_id, str) or not family_id.strip():
                raise ChallengeAssemblyError(
                    f"blocked corpus record has no document_family_id at {row_location}"
                )
            if not text:
                raise ChallengeAssemblyError(
                    f"blocked corpus record has no text at {row_location}"
                )
            if (
                row.get("label") != "S3"
                or row.get("document_origin") != "public_real"
                or row.get("artifact_intended_use") != "evaluation_only"
                or row.get("evaluation_suite") != SUITE_ID
                or row.get("evaluation_suite_role")
                != "s3_overclassification_fpr_challenge_only"
            ):
                raise ChallengeAssemblyError(
                    f"blocked corpus record is outside the public S3 challenge schema at {row_location}"
                )
            check = validate_proxy_record(
                row, stage="eligible", intended_use="evaluation"
            )
            if not check.ok:
                raise ChallengeAssemblyError(
                    f"blocked corpus record failed evaluation validation at {row_location}"
                )
            doc_ids.append(doc_id.strip())
            family_ids.append(family_id.strip())
            normalized_text_hashes.append(text_hash(text))

        actual_uniqueness = {
            "unique_doc_ids": len(set(doc_ids)),
            "unique_text_hashes": len(set(normalized_text_hashes)),
            "unique_document_family_ids": len(set(family_ids)),
        }
        uniqueness = manifest.get("uniqueness")
        if not isinstance(uniqueness, Mapping):
            raise ChallengeAssemblyError(
                f"blocked corpus uniqueness attestation is missing: {location}"
            )
        declared_uniqueness = {
            key: _require_nonnegative_int(
                uniqueness.get(key), field=key, location=location
            )
            for key in actual_uniqueness
        }
        if declared_uniqueness != actual_uniqueness:
            raise ChallengeAssemblyError(
                f"blocked corpus uniqueness count mismatch: {location}"
            )
        if actual_uniqueness["unique_doc_ids"] != len(located_rows):
            raise ChallengeAssemblyError(
                f"blocked corpus doc_ids are not unique: {location}"
            )
        if actual_uniqueness["unique_text_hashes"] != len(located_rows):
            raise ChallengeAssemblyError(
                f"blocked corpus normalized texts are not unique: {location}"
            )

        selection = manifest.get("selection")
        family_counts = manifest.get("family_counts")
        if (
            not isinstance(selection, Mapping)
            or selection.get("target_count") != len(located_rows)
            or not isinstance(family_counts, Mapping)
            or family_counts.get("records") != len(located_rows)
            or family_counts.get("unique_document_families")
            != actual_uniqueness["unique_document_family_ids"]
        ):
            raise ChallengeAssemblyError(
                f"blocked corpus declared selection/family counts are inconsistent: {location}"
            )

        all_doc_ids.update(doc_ids)
        all_family_ids.update(family_ids)
        all_text_hashes.update(normalized_text_hashes)
        total_rows += len(located_rows)
        files.append(
            {
                "requested_path": str(requested_path),
                "records_path": str(records_path),
                "records_bytes": len(records_payload),
                "records_sha256": _sha256_bytes(records_payload),
                "manifest_path": str(manifest_path),
                "manifest_bytes": len(manifest_payload),
                "manifest_sha256": _sha256_bytes(manifest_payload),
                "manifest_schema": str(manifest["schema"]),
                "records": len(located_rows),
                **actual_uniqueness,
            }
        )

    return LoadedBlockedCorpora(
        doc_ids=frozenset(all_doc_ids),
        document_family_ids=frozenset(all_family_ids),
        normalized_text_hashes=frozenset(all_text_hashes),
        files=tuple(files),
        row_count=total_rows,
    )


def load_inputs(paths: Sequence[Path]) -> LoadedInputs:
    """Load complete Korea policy runs and bind each record to its page ledger."""
    if not paths:
        raise ChallengeAssemblyError("at least one --input is required")
    seen_paths: set[Path] = set()
    loaded_rows: list[LoadedRow] = []
    files: list[dict[str, object]] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen_paths:
            raise ChallengeAssemblyError(f"duplicate input path: {path}")
        seen_paths.add(resolved)
        rows, records_payload = _read_records(path)
        manifest, manifest_payload = _read_run_manifest(path)
        page_ledger = _source_pages(
            manifest, location=str(path.with_name("manifest.json"))
        )
        declared_sections = (
            (manifest.get("pilot") or {}).get("sections")
            if isinstance(manifest.get("pilot"), Mapping)
            else None
        )
        if declared_sections is not None and declared_sections != len(rows):
            raise ChallengeAssemblyError(f"manifest/records row mismatch: {path}")
        run_id = str(manifest["run_id"])
        for line_number, row in rows:
            match = _DOC_ID.fullmatch(str(row.get("doc_id") or ""))
            if match is None:
                raise ChallengeAssemblyError(
                    f"invalid Korea policy doc_id at {path}:{line_number}"
                )
            news_id = match.group(1)
            page = page_ledger.get(news_id)
            if page is None:
                raise ChallengeAssemblyError(
                    f"record has no source-page ledger entry at {path}:{line_number}"
                )
            loaded_rows.append(
                LoadedRow(
                    record=row,
                    input_path=str(path),
                    line_number=line_number,
                    run_id=run_id,
                    source_page=page,
                )
            )
        files.append(
            {
                "path": str(path),
                "records_sha256": _sha256_bytes(records_payload),
                "records_bytes": len(records_payload),
                "rows": len(rows),
                "run_id": run_id,
                "run_manifest_path": str(path.with_name("manifest.json")),
                "run_manifest_sha256": _sha256_bytes(manifest_payload),
            }
        )
    return LoadedInputs(rows=tuple(loaded_rows), files=tuple(files))


def _source_url_parts(
    record: Mapping[str, object], *, location: str
) -> tuple[str, str]:
    reference = str(record.get("source_reference") or "").strip()
    source_url = str(record.get("source_url") or "").strip()
    if not reference or source_url != reference:
        raise ChallengeAssemblyError(f"source URL/reference mismatch at {location}")
    parsed = urllib.parse.urlsplit(reference)
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or host not in ALLOWED_HOSTS
        or parsed.path not in ALLOWED_PATHS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or bool(parsed.fragment)
    ):
        raise ChallengeAssemblyError(
            f"source URL is outside the Korea policy allowlist at {location}"
        )
    query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
    news_ids = query.get("newsId", [])
    if set(query) != {"newsId"} or len(news_ids) != 1 or not news_ids[0].isdigit():
        raise ChallengeAssemblyError(
            f"source URL has no unique numeric newsId at {location}"
        )
    return reference, news_ids[0]


def _resolve_run_file(
    run_dir: Path, value: object, *, field: str, location: str
) -> Path:
    relative = Path(str(value or ""))
    if not str(value or "").strip() or relative.is_absolute():
        raise ChallengeAssemblyError(f"invalid {field} at {location}")
    root = run_dir.resolve()
    resolved = (run_dir / relative).resolve()
    if resolved == root or root not in resolved.parents or not resolved.is_file():
        raise ChallengeAssemblyError(f"unsafe or missing {field} at {location}")
    return resolved


def _validate_provenance(loaded: LoadedRow) -> None:
    row = loaded.record
    location = f"{loaded.input_path}:{loaded.line_number}"
    if row.get("collection_schema") != COLLECTION_SCHEMA:
        raise ChallengeAssemblyError(f"unexpected collection schema at {location}")
    if row.get("source_id") != SOURCE_ID:
        raise ChallengeAssemblyError(f"unexpected source_id at {location}")
    if row.get("document_origin") != "public_real" or row.get("label") != "S3":
        raise ChallengeAssemblyError(
            f"challenge accepts only public_real S3 at {location}"
        )
    if row.get("evaluation_use_permitted") is not True:
        raise IneligibleChallengeRecord(f"evaluation_use_not_permitted at {location}")
    reference, news_id = _source_url_parts(row, location=location)
    match = _DOC_ID.fullmatch(str(row.get("doc_id") or ""))
    if match is None or match.group(1) != news_id:
        raise ChallengeAssemblyError(f"doc_id/source newsId mismatch at {location}")
    if row.get("document_family_id") != f"korea-policy-{news_id}":
        raise ChallengeAssemblyError(f"family/source newsId mismatch at {location}")
    section_index = int(match.group(2))
    if row.get("section_index") != section_index:
        raise ChallengeAssemblyError(f"section index mismatch at {location}")

    source_hash = _require_sha256(
        row.get("source_sha256"), field="source_sha256", location=location
    )
    raw_hash = _require_sha256(
        row.get("raw_html_sha256"), field="raw_html_sha256", location=location
    )
    if source_hash != raw_hash:
        raise ChallengeAssemblyError(f"source/raw hash mismatch at {location}")
    licence_hash = _require_sha256(
        row.get("license_evidence_sha256"),
        field="license_evidence_sha256",
        location=location,
    )
    licence = str(row.get("source_license") or "")
    if (
        licence not in ALLOWED_LICENSES
        or not str(row.get("license_exact_snippet") or "").strip()
    ):
        raise ChallengeAssemblyError(
            f"unsupported or missing item-level licence at {location}"
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
        "license_status": str(row.get("license_status") or ""),
        "training_use_permitted": row.get("training_use_permitted"),
        "evaluation_use_permitted": True,
    }
    for key, value in expected.items():
        if page.get(key) != value:
            raise ChallengeAssemblyError(
                f"record/page provenance mismatch for {key} at {location}"
            )
    if (
        page.get("status") != "accepted"
        or int(page.get("section_count") or 0) < section_index
    ):
        raise ChallengeAssemblyError(
            f"source page did not accept this section at {location}"
        )

    run_dir = Path(loaded.input_path).parent
    raw_path = _resolve_run_file(
        run_dir, page.get("raw_html_path"), field="raw_html_path", location=location
    )
    try:
        raw_payload = raw_path.read_bytes()
    except OSError as exc:
        raise ChallengeAssemblyError(
            f"cannot read raw source at {location}: {exc}"
        ) from exc
    if _sha256_bytes(raw_payload) != raw_hash:
        raise ChallengeAssemblyError(f"raw source bytes/hash mismatch at {location}")

    text_path = _resolve_run_file(
        run_dir, page.get("text_path"), field="text_path", location=location
    )
    try:
        extracted_body = text_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ChallengeAssemblyError(
            f"cannot read extracted source text at {location}: {exc}"
        ) from exc
    if extracted_body.endswith("\n"):
        extracted_body = extracted_body[:-1]
    body_start = row.get("body_start")
    body_end = row.get("body_end")
    if not isinstance(body_start, int) or not isinstance(body_end, int):
        raise ChallengeAssemblyError(f"invalid source text offsets at {location}")
    if not 0 <= body_start < body_end <= len(extracted_body):
        raise ChallengeAssemblyError(
            f"source text offsets are outside the extracted body at {location}"
        )
    if extracted_body[body_start:body_end] != record_text(row):
        raise ChallengeAssemblyError(
            f"record text/source offsets mismatch at {location}"
        )

    candidate_check = validate_proxy_record(
        row, stage="candidate", intended_use="evaluation"
    )
    eligible_check = validate_proxy_record(
        row, stage="eligible", intended_use="evaluation"
    )
    errors = [*candidate_check.errors, *eligible_check.errors]
    if errors:
        raise IneligibleChallengeRecord(
            f"proxy_evaluation_validation_failed:{','.join(sorted(set(errors)))} at {location}"
        )


def _length_bin(text: str) -> str:
    length = len(text)
    if length < 1600:
        return "1200-1599"
    if length < 2200:
        return "1600-2199"
    if length < 2800:
        return "2200-2799"
    return "2800+"


def _seed_rank(seed: str, *values: object) -> str:
    material = "\0".join([seed, *(str(value) for value in values)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _assert_safe_family_sections(rows: Sequence[dict[str, object]]) -> None:
    by_family: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_family[str(row["document_family_id"])].append(row)
    for family, family_rows in by_family.items():
        source_hashes = {str(row.get("source_sha256") or "") for row in family_rows}
        ranges = sorted(
            (int(row.get("body_start") or 0), int(row.get("body_end") or 0))
            for row in family_rows
        )
        if len(source_hashes) != 1 or any(start >= end for start, end in ranges):
            raise ChallengeAssemblyError(
                f"unsafe multi-section source metadata for family {family}"
            )
        if any(
            right_start < left_end
            for (_, left_end), (right_start, _) in zip(ranges, ranges[1:])
        ):
            raise ChallengeAssemblyError(f"overlapping sections in family {family}")


def _select_diverse(
    rows: Sequence[dict[str, object]],
    *,
    count: int,
    seed: str,
    max_sections_per_family: int,
) -> list[dict[str, object]]:
    by_agency_bin: dict[str, dict[str, list[dict[str, object]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        agency = str(row.get("source_agency") or "").strip()
        if not agency:
            raise ChallengeAssemblyError(f"missing source_agency: {row.get('doc_id')}")
        by_agency_bin[agency][_length_bin(record_text(row))].append(row)
    for agency, bins in by_agency_bin.items():
        for length_bin, candidates in bins.items():
            candidates.sort(
                key=lambda row: _seed_rank(
                    seed,
                    agency,
                    length_bin,
                    row["doc_id"],
                    text_hash(record_text(row)),
                )
            )

    agencies = sorted(
        by_agency_bin, key=lambda value: _seed_rank(seed, "agency", value)
    )
    bin_names = {
        agency: sorted(
            bins,
            key=lambda value, current=agency: _seed_rank(
                seed, "length", current, value
            ),
        )
        for agency, bins in by_agency_bin.items()
    }
    bin_offsets: dict[tuple[str, str], int] = defaultdict(int)
    bin_cursor: dict[str, int] = defaultdict(int)
    family_counts: Counter[str] = Counter()
    selected: list[dict[str, object]] = []
    while len(selected) < count:
        progressed = False
        for agency in agencies:
            names = bin_names[agency]
            for step in range(len(names)):
                bin_index = (bin_cursor[agency] + step) % len(names)
                name = names[bin_index]
                candidates = by_agency_bin[agency][name]
                offset_key = (agency, name)
                while bin_offsets[offset_key] < len(candidates):
                    row = candidates[bin_offsets[offset_key]]
                    bin_offsets[offset_key] += 1
                    family = str(row["document_family_id"])
                    if family_counts[family] >= max_sections_per_family:
                        continue
                    selected.append(row)
                    family_counts[family] += 1
                    bin_cursor[agency] = (bin_index + 1) % len(names)
                    progressed = True
                    break
                if progressed and selected[-1].get("source_agency") == agency:
                    break
            if len(selected) == count:
                break
        if not progressed:
            break
    return selected


def assemble_challenge(
    loaded: LoadedInputs,
    *,
    count: int = DEFAULT_COUNT,
    seed: str = DEFAULT_SEED,
    max_sections_per_family: int = 1,
    blocked_corpora: LoadedBlockedCorpora | None = None,
    assembled_at: str | None = None,
) -> ChallengeAssembly:
    if count < 1:
        raise ChallengeAssemblyError("count must be positive")
    if not seed:
        raise ChallengeAssemblyError("seed must not be empty")
    if not 1 <= max_sections_per_family <= 3:
        raise ChallengeAssemblyError("max_sections_per_family must be between 1 and 3")

    blocked = blocked_corpora or LoadedBlockedCorpora(
        doc_ids=frozenset(),
        document_family_ids=frozenset(),
        normalized_text_hashes=frozenset(),
        files=(),
        row_count=0,
    )
    rows: list[dict[str, object]] = []
    seen_doc_ids: dict[str, str] = {}
    seen_text_hashes: dict[str, str] = {}
    source_bindings: dict[str, tuple[str, str, str]] = {}
    duplicate_doc_ids = 0
    duplicate_texts = 0
    ineligible_counts: Counter[str] = Counter()
    blocked_overlap_reason_counts: Counter[str] = Counter()
    blocked_overlap_combination_counts: Counter[str] = Counter()
    blocked_overlap_records = 0
    for loaded_row in loaded.rows:
        try:
            _validate_provenance(loaded_row)
        except IneligibleChallengeRecord as exc:
            reason = str(exc).split(" at ", 1)[0]
            ineligible_counts[reason] += 1
            continue
        row = dict(loaded_row.record)
        doc_id = str(row["doc_id"]).strip()
        family_id = str(row["document_family_id"]).strip()
        normalized_text_hash = text_hash(record_text(row))
        blocked_reasons: list[str] = []
        if doc_id in blocked.doc_ids:
            blocked_reasons.append("doc_id_overlap")
        if family_id in blocked.document_family_ids:
            blocked_reasons.append("document_family_id_overlap")
        if normalized_text_hash in blocked.normalized_text_hashes:
            blocked_reasons.append("normalized_text_overlap")
        if blocked_reasons:
            blocked_overlap_records += 1
            blocked_overlap_reason_counts.update(blocked_reasons)
            blocked_overlap_combination_counts["+".join(blocked_reasons)] += 1
            continue
        source_reference = str(row["source_reference"])
        binding = (
            str(row["source_sha256"]),
            str(row["source_license"]),
            str(row["license_evidence_sha256"]),
        )
        prior_binding = source_bindings.setdefault(source_reference, binding)
        if prior_binding != binding:
            raise ChallengeAssemblyError(
                f"conflicting provenance for {source_reference}"
            )
        if doc_id in seen_doc_ids:
            if seen_doc_ids[doc_id] != normalized_text_hash:
                raise ChallengeAssemblyError(f"conflicting duplicate doc_id: {doc_id}")
            duplicate_doc_ids += 1
            continue
        if normalized_text_hash in seen_text_hashes:
            duplicate_texts += 1
            continue
        seen_doc_ids[doc_id] = normalized_text_hash
        seen_text_hashes[normalized_text_hash] = doc_id
        row["artifact_intended_use"] = "evaluation_only"
        row["evaluation_suite"] = SUITE_ID
        row["evaluation_suite_role"] = "s3_overclassification_fpr_challenge_only"
        rows.append(row)

    if max_sections_per_family > 1:
        _assert_safe_family_sections(rows)
    selected = _select_diverse(
        rows,
        count=count,
        seed=seed,
        max_sections_per_family=max_sections_per_family,
    )
    if len(selected) != count:
        unique_families = len({str(row["document_family_id"]) for row in rows})
        raise ChallengeAssemblyError(
            "insufficient eligible public S3 records: "
            f"needed={count}, selected={len(selected)}, candidates={len(rows)}, "
            f"families={unique_families}, max_sections_per_family={max_sections_per_family}"
        )

    selected.sort(key=lambda row: str(row["doc_id"]))
    doc_ids = [str(row["doc_id"]) for row in selected]
    text_hashes = [text_hash(record_text(row)) for row in selected]
    families = [str(row["document_family_id"]) for row in selected]
    if len(set(doc_ids)) != count or len(set(text_hashes)) != count:
        raise ChallengeAssemblyError(
            "selected output is not unique by doc_id and text hash"
        )
    if max_sections_per_family == 1 and len(set(families)) != count:
        raise ChallengeAssemblyError(
            "default selection must contain one record per family"
        )
    for row in selected:
        check = validate_proxy_record(row, stage="eligible", intended_use="evaluation")
        if not check.ok:
            raise ChallengeAssemblyError(
                f"selected record failed final evaluation validation: {row['doc_id']}"
            )

    assembled_at = assembled_at or datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    manifest: dict[str, object] = {
        "schema": SCHEMA,
        "artifact_kind": ARTIFACT_KIND,
        "suite_id": SUITE_ID,
        "status": "ready",
        "assembled_at": assembled_at,
        "intended_use": "evaluation_only",
        "claim_boundary": {
            "allowed": [
                "S3 overclassification rate",
                "S3 false-positive grade distribution",
                "paired regression comparison on this fixed challenge",
            ],
            "forbidden": [
                "overall accuracy",
                "balanced accuracy",
                "customer accuracy",
                "customer or production real-world accuracy",
            ],
            "must_not_merge_with_primary_balanced_metrics": True,
            "reason": "all records are public-real S3; origin and class are intentionally confounded",
        },
        "selection": {
            "target_count": count,
            "seed": seed,
            "strategy": "seeded agency round-robin with rotating document-length bins",
            "max_sections_per_family": max_sections_per_family,
            "default_one_record_per_family": max_sections_per_family == 1,
        },
        "validation": {
            "stages": ["candidate", "eligible"],
            "intended_use": "evaluation",
            "stored_validation_fields_trusted": False,
            "item_level_page_ledger_binding_required": True,
        },
        "inputs": list(loaded.files),
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
                "records": blocked_overlap_records,
                "reason_counts": dict(sorted(blocked_overlap_reason_counts.items())),
                "reason_combination_counts": dict(
                    sorted(blocked_overlap_combination_counts.items())
                ),
            },
        },
        "input_summary": {
            "rows": len(loaded.rows),
            "eligible_unique_candidates": len(rows),
            "ineligible_rows_excluded": sum(ineligible_counts.values()),
            "ineligible_reason_counts": dict(sorted(ineligible_counts.items())),
            "duplicate_doc_ids_excluded": duplicate_doc_ids,
            "duplicate_texts_excluded": duplicate_texts,
            "blocked_overlap_records_excluded": blocked_overlap_records,
        },
        "distribution": {"S3": count},
        "document_origin_counts": dict(
            Counter(str(row["document_origin"]) for row in selected)
        ),
        "family_counts": {
            "unique_document_families": len(set(families)),
            "records": count,
        },
        "source_counts": dict(
            sorted(Counter(str(row["source_id"]) for row in selected).items())
        ),
        "source_agency_counts": dict(
            sorted(Counter(str(row["source_agency"]) for row in selected).items())
        ),
        "license_counts": dict(
            sorted(Counter(str(row["source_license"]) for row in selected).items())
        ),
        "permission_counts": {
            "evaluation_use_permitted": sum(
                row.get("evaluation_use_permitted") is True for row in selected
            ),
            "training_use_permitted": sum(
                row.get("training_use_permitted") is True for row in selected
            ),
            "evaluation_only_source_licence": sum(
                row.get("training_use_permitted") is not True for row in selected
            ),
        },
        "document_length_bin_counts": dict(
            sorted(Counter(_length_bin(record_text(row)) for row in selected).items())
        ),
        "uniqueness": {
            "unique_doc_ids": len(set(doc_ids)),
            "unique_text_hashes": len(set(text_hashes)),
            "unique_document_family_ids": len(set(families)),
        },
    }
    return ChallengeAssembly(selected=tuple(selected), manifest=manifest)


def _atomic_publish_directory(
    output_dir: Path,
    *,
    records_payload: bytes,
    manifest_payload: bytes,
) -> None:
    """Publish both files as one new directory; never replace an existing run."""
    if output_dir.exists():
        raise ChallengeAssemblyError(
            f"refusing to overwrite artifact directory: {output_dir}"
        )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
    )
    try:
        for name, payload in (
            ("records.jsonl", records_payload),
            ("manifest.json", manifest_payload),
        ):
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        type=Path,
        help="Korea policy run records.jsonl; repeatable",
    )
    parser.add_argument(
        "--blocked-corpus",
        action="append",
        default=[],
        type=Path,
        help=(
            "immutable public S3 challenge directory (or its records.jsonl) whose "
            "doc IDs, families, and normalized texts must be excluded; repeatable"
        ),
    )
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument(
        "--max-sections-per-family",
        type=int,
        choices=(1, 2, 3),
        default=1,
        help="explicit opt-in for multiple non-overlapping sections; default 1",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("datasets/proxy_gold/public_s3_challenge_300"),
    )
    args = parser.parse_args(argv)

    try:
        loaded = load_inputs(args.input)
        blocked_corpora = load_blocked_corpora(args.blocked_corpus)
        assembly = assemble_challenge(
            loaded,
            count=args.count,
            seed=args.seed,
            max_sections_per_family=args.max_sections_per_family,
            blocked_corpora=blocked_corpora,
        )
        records_payload = _canonical_jsonl_bytes(assembly.selected)
        manifest = dict(assembly.manifest)
        manifest["artifact"] = {
            "records_path": "records.jsonl",
            "records": len(assembly.selected),
            "records_bytes": len(records_payload),
            "records_sha256": _sha256_bytes(records_payload),
        }
        manifest_payload = _canonical_json_bytes(manifest)
        _atomic_publish_directory(
            args.out_dir,
            records_payload=records_payload,
            manifest_payload=manifest_payload,
        )
    except ChallengeAssemblyError as exc:
        print(json.dumps({"ready": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "ready": True,
                "output_dir": str(args.out_dir),
                "records": len(assembly.selected),
                "records_sha256": manifest["artifact"]["records_sha256"],
                "artifact_kind": ARTIFACT_KIND,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
