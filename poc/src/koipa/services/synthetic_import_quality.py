"""Read-only quality audit for synthetic-document imports and review batches.

``synth_quality.screen_batch`` is intentionally a small, generation-time gate.  It
receives a list of ``(grade, body)`` tuples while a worker is creating documents.
That is the right place to reject an explicit label or telephone number before a
candidate is queued, but it cannot answer questions about an *existing* JSON/JSONL
delivery:

* are document types and grades present and distributed as declared?
* did a supplier copy a prompt phrase, a whole document, or a template paragraph?
* are Korean ``name + job title`` forms present even when no telephone/email exists?
* which records can be retained, which must be replaced, and which have no usable
  metadata at all?

This module is deliberately deterministic and read-only.  It makes no LLM call,
does not alter input rows, and produces enough manifest information to reproduce
an import decision later.  It is suitable for both KOIPA generated documents and
external DataGen deliveries; a caller may supply a different prompt-echo lexicon
when an external generator used a different prompt.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from koipa.services.synth_quality import (
    _contains_pii,
    _exposes_grade_token,
    _exposes_self_grade_declaration,
)


AUDIT_SCHEMA_VERSION = "synthetic-import-quality-v1"
VALID_GRADES = ("TS", "S1", "S2", "S3")

# These are *observed prompt phrases*, not a policy keyword dictionary.  They are
# deliberately narrow: broad business words such as "계약" or "보도자료" would
# punish ordinary document types and make the checker a source of false positives.
# The values can be replaced per run with ``--prompt-echo-lexicon`` in the CLI.
DEFAULT_PROMPT_ECHO_LEXICON: dict[str, tuple[str, ...]] = {
    "TS": ("인수협상", "진행 중인 인수", "합병 협상"),
    "S1": ("팀장급", "핵심 공정 노하우", "원가 구조"),
    "S2": ("협상력 약화",),
    "S3": (),
}

# A high-severity PII decision needs to be conservative.  Generic organisation
# phrases such as "구매팀 대리" and "전까지 대표이사" look superficially like
# three Hangul syllables followed by a role.  A general Korean NER model would
# be needed to decide those cases accurately; this offline gate must not pretend
# a broad regex is one.  It therefore recognises a maintained set of common
# two-syllable given-name forms plus a surname and requires a space before the
# role.  This catches the prohibited form "김민수 부장" without turning role-only
# writing into 100% false positives.  A customer can add a stronger NER review
# upstream, but it must not silently change this deterministic release decision.
_COMMON_KOREAN_SURNAMES = "김이박최정강조윤장임한오서신권황안송전홍유고문양손배백허남심노하곽성차주우구민진지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어"
_COMMON_GIVEN_NAMES = (
    "민수|민준|민지|민석|민재|민호|민영|지민|지훈|지수|지은|지연|지영|지현|"
    "서준|서연|서윤|서현|서진|서영|현우|현수|현준|현정|현진|준호|준영|준혁|"
    "수진|수현|수빈|수연|수민|은지|은정|은영|유진|유정|유나|유민|도윤|도현|"
    "예은|예준|예진|하윤|하늘|주원|주영|주현|성민|성호|성수|성진|동현|동욱|"
    "건우|시우|우진|우성|재현|재훈|재민|태훈|태영|정우|정민|정훈|영수|영희|"
    "경수|경희|철수|창훈|창민|승현|승우|승민|혜진|혜원|혜정"
)
_KOREAN_NAME_AND_ROLE = re.compile(
    rf"(?<![가-힣])([{_COMMON_KOREAN_SURNAMES}](?:{_COMMON_GIVEN_NAMES}))\s+"
    # Korean particles immediately following a title ("김민수 부장이") are allowed.
    r"(대표이사|사장|전무|상무|이사|부장|차장|과장|대리|사원|주임|팀장|실장|연구원|매니저)"
    r"(?=$|[\s,.·:;()\[\]{}]|[이가은는을를의])"
)
_NUMBERED_HEADING = re.compile(r"(?m)^\s*(?:\d{1,2}[.)]|[가-하][.)])\s+\S+")
_EMAIL_LIKE = re.compile(r"(?im)^\s*(?:from|to|보낸 사람|받는 사람|수신|발신)\s*[:：]")
_CONTRACT_LIKE = re.compile(r"(?m)^\s*(?:제\s*\d+\s*조|계약\s*당사자|서명|날인)")


@dataclass(frozen=True)
class AuditRecord:
    """Normalized, source-addressable record used by the checker."""

    source_path: str
    source_index: int
    document_id: str
    title: str
    body: str
    grade: str | None
    document_type: str | None
    raw: Mapping[str, Any] = field(repr=False, compare=False)


def _fold(text: object) -> str:
    return unicodedata.normalize("NFKC", str(text or ""))


def _first_text(row: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def normalize_record(row: Mapping[str, Any], *, source_path: str, source_index: int) -> AuditRecord:
    """Normalize common KOIPA/DataGen JSON fields without modifying the source row."""
    document_id = _first_text(row, ("review_id", "synth_id", "document_id", "id", "doc_id", "uuid"))
    if not document_id:
        document_id = f"{Path(source_path).name}:{source_index + 1}"
    grade = _first_text(row, ("target_grade", "true_grade", "grade", "expected_grade", "label")) or None
    if grade:
        grade = _fold(grade).upper()
    document_type = _first_text(
        row, ("document_type", "doc_format", "doc_type", "type", "documentType")
    ) or None
    return AuditRecord(
        source_path=source_path,
        source_index=source_index,
        document_id=document_id,
        title=_first_text(row, ("title", "document_title", "name", "subject")),
        body=_first_text(row, ("body", "generated_content", "content", "text", "document_body")),
        grade=grade,
        document_type=document_type,
        raw=row,
    )


def _normalized_body(text: str) -> str:
    return re.sub(r"\s+", " ", _fold(text)).strip().casefold()


def _paragraphs(text: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", _fold(part)).strip().casefold()
        for part in re.split(r"\n\s*\n", text)
        if len(re.sub(r"\s+", " ", _fold(part)).strip()) >= 80
    ]


def _structure(text: str) -> str:
    """A transparent coarse shape label; it is a report metric, not a document type."""
    if len(_NUMBERED_HEADING.findall(text)) >= 3:
        return "numbered_outline"
    if len(_EMAIL_LIKE.findall(text)) >= 2:
        return "email"
    if len(_CONTRACT_LIKE.findall(text)) >= 2:
        return "contract"
    return "narrative_or_other"


def _echo_terms(grade: str | None, text: str, lexicon: Mapping[str, Sequence[str]]) -> list[str]:
    if not grade:
        return []
    folded = _fold(text).casefold()
    return sorted({term for term in lexicon.get(grade, ()) if _fold(term).casefold() in folded})


def _sha256_json(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def report_checksum(report: Mapping[str, Any]) -> str:
    """Checksum the full report payload except its self-referential checksum."""
    return _sha256_json({key: value for key, value in report.items() if key != "report_sha256"})


def _issue(code: str, *, severity: str, detail: str | None = None) -> dict[str, str]:
    result = {"code": code, "severity": severity}
    if detail:
        result["detail"] = detail
    return result


def audit_records(
    records: Sequence[AuditRecord],
    *,
    prompt_echo_lexicon: Mapping[str, Sequence[str]] | None = None,
    template_min_documents: int = 3,
    structure_concentration_threshold: float = 0.80,
) -> dict[str, Any]:
    """Audit records and return a stable report with per-document dispositions.

    ``exclude`` means the source is malformed enough that it cannot be repaired
    without finding the original record. ``replace`` is an otherwise usable record
    with content that must not enter a reviewer/training batch. ``keep`` means no
    direct document-level blocker was found. Batch-level distribution findings are
    separately reported as release blockers and never silently convert all records
    to ``replace``.
    """
    if template_min_documents < 2:
        raise ValueError("template_min_documents must be >= 2")
    if not 0 < structure_concentration_threshold <= 1:
        raise ValueError("structure_concentration_threshold must be in (0, 1]")

    lexicon = prompt_echo_lexicon or DEFAULT_PROMPT_ECHO_LEXICON
    body_groups: dict[str, list[int]] = defaultdict(list)
    paragraph_docs: dict[str, set[int]] = defaultdict(set)
    structures: list[str] = []
    for index, rec in enumerate(records):
        normalized = _normalized_body(rec.body)
        if normalized:
            body_groups[normalized].append(index)
        for paragraph in set(_paragraphs(rec.body)):
            paragraph_docs[paragraph].add(index)
        structures.append(_structure(rec.body))

    duplicate_indices = {
        index for indices in body_groups.values() if len(indices) > 1 for index in indices[1:]
    }
    template_indices = {
        index
        for doc_indices in paragraph_docs.values()
        if len(doc_indices) >= template_min_documents
        for index in doc_indices
    }

    # A first copy remains available for a human to inspect. Exact later duplicates
    # are replace candidates, preventing arbitrary loss of all copies.
    decisions: list[dict[str, Any]] = []
    for index, rec in enumerate(records):
        text = f"{rec.title}\n{rec.body}"
        issues: list[dict[str, str]] = []
        if not rec.body.strip():
            issues.append(_issue("missing_body", severity="exclude"))
        if not rec.grade or rec.grade not in VALID_GRADES:
            detail = rec.grade or "missing"
            issues.append(_issue("invalid_or_missing_grade", severity="exclude", detail=detail))
        if not rec.document_type or not rec.document_type.strip():
            issues.append(_issue("missing_document_type", severity="exclude"))
        if _exposes_grade_token(text):
            issues.append(_issue("grade_token_exposed", severity="replace"))
        if _exposes_self_grade_declaration(text):
            issues.append(_issue("self_grade_declaration", severity="replace"))
        if _contains_pii(text):
            issues.append(_issue("pii_detected", severity="replace"))
        if _KOREAN_NAME_AND_ROLE.search(_fold(text)):
            issues.append(_issue("korean_name_and_role", severity="replace"))
        echoed = _echo_terms(rec.grade, text, lexicon)
        if echoed:
            issues.append(_issue("prompt_echo", severity="replace", detail=", ".join(echoed)))
        if index in duplicate_indices:
            issues.append(_issue("duplicate_document", severity="replace"))
        if index in template_indices:
            issues.append(_issue("repeated_template_paragraph", severity="replace"))

        severity = {issue["severity"] for issue in issues}
        disposition = "exclude" if "exclude" in severity else "replace" if "replace" in severity else "keep"
        decisions.append(
            {
                "document_id": rec.document_id,
                "source_path": rec.source_path,
                "source_index": rec.source_index,
                "title": rec.title,
                "grade": rec.grade,
                "document_type": rec.document_type,
                "structure": structures[index],
                "disposition": disposition,
                "issues": issues,
            }
        )

    by_grade = Counter(rec.grade or "missing" for rec in records)
    by_document_type = Counter(rec.document_type or "missing" for rec in records)
    by_type_grade: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for rec in records:
        by_type_grade[rec.document_type or "missing"][rec.grade or "missing"] += 1
    by_structure = Counter(structures)
    release_blockers: list[dict[str, Any]] = []
    if records:
        dominant_structure, dominant_count = by_structure.most_common(1)[0]
        share = dominant_count / len(records)
        if len(records) >= template_min_documents and share >= structure_concentration_threshold:
            release_blockers.append(
                {
                    "code": "structure_distribution_concentrated",
                    "structure": dominant_structure,
                    "count": dominant_count,
                    "share": round(share, 4),
                    "threshold": structure_concentration_threshold,
                }
            )

    disposition_counts = Counter(item["disposition"] for item in decisions)
    issue_counts = Counter(issue["code"] for item in decisions for issue in item["issues"])
    report = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "audit_config": {
            "prompt_echo_lexicon": {
                grade: list(terms) for grade, terms in sorted(lexicon.items())
            },
            "template_min_documents": template_min_documents,
            "structure_concentration_threshold": structure_concentration_threshold,
        },
        "summary": {
            "documents": len(records),
            "keep": disposition_counts["keep"],
            "replace": disposition_counts["replace"],
            "exclude": disposition_counts["exclude"],
            "release_ready": not release_blockers and not disposition_counts["replace"] and not disposition_counts["exclude"],
        },
        "distribution": {
            "by_grade": dict(sorted(by_grade.items())),
            "by_document_type": dict(sorted(by_document_type.items())),
            "by_document_type_grade": {
                doc_type: dict(sorted(grades.items()))
                for doc_type, grades in sorted(by_type_grade.items())
            },
            "by_structure": dict(sorted(by_structure.items())),
        },
        "issue_counts": dict(sorted(issue_counts.items())),
        "release_blockers": release_blockers,
        "decisions": decisions,
    }
    report["report_sha256"] = report_checksum(report)
    return report


def _rows_from_json(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, Mapping)]
    if isinstance(value, Mapping):
        for key in ("documents", "items", "data", "rows"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [row for row in nested if isinstance(row, Mapping)]
        if any(key in value for key in ("body", "content", "generated_content", "text")):
            return [value]
    return []


def _load_rows_from_file(path: Path) -> list[Mapping[str, Any]]:
    content = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".jsonl":
        rows: list[Any] = [json.loads(line) for line in content.splitlines() if line.strip()]
    else:
        rows = _rows_from_json(json.loads(content))
    if not all(isinstance(row, Mapping) for row in rows):
        raise ValueError("JSON rows must be objects")
    return rows


def load_records(paths: Iterable[Path]) -> tuple[list[AuditRecord], list[dict[str, str]]]:
    """Read JSON/JSONL files and return normalized records plus non-fatal read errors."""
    records: list[AuditRecord] = []
    errors: list[dict[str, str]] = []
    for path in paths:
        try:
            rows = _load_rows_from_file(path)
            records.extend(
                normalize_record(row, source_path=str(path), source_index=index)
                for index, row in enumerate(rows)
            )
        except Exception as exc:  # Input inventories should report all bad files, not stop at first.
            errors.append({"path": str(path), "error": str(exc)})
    return records, errors


def load_metadata_index(path: Path) -> tuple[dict[str, Mapping[str, Any]], list[dict[str, str]]]:
    """Load an internal manifest keyed by review/document ID without exposing it to reviewers.

    A reviewer delivery commonly contains only ``review_id`` and blind text, while
    its internal manifest holds ``true_grade`` and ``doc_format``.  Auditing the
    delivery therefore needs an explicit server-side join; the checker never
    writes a label back into the reviewer file.
    """
    try:
        rows = _load_rows_from_file(path)
    except Exception as exc:  # keep a failed manifest visible in a report
        return {}, [{"path": str(path), "error": str(exc)}]
    index: dict[str, Mapping[str, Any]] = {}
    errors: list[dict[str, str]] = []
    for row_number, row in enumerate(rows, start=1):
        key = _first_text(row, ("review_id", "synth_id", "document_id", "id", "doc_id", "uuid"))
        if not key:
            errors.append({"path": str(path), "error": f"row {row_number}: missing metadata ID"})
        elif key in index:
            errors.append({"path": str(path), "error": f"row {row_number}: duplicate metadata ID {key}"})
        else:
            index[key] = row
    return index, errors


def merge_metadata(records: Sequence[AuditRecord], metadata: Mapping[str, Mapping[str, Any]]) -> list[AuditRecord]:
    """Overlay grade/type metadata by ID while preserving the reviewer text source."""
    merged: list[AuditRecord] = []
    for record in records:
        meta = metadata.get(record.document_id)
        row = dict(meta or {})
        row.update(record.raw)  # text/title from the review file always wins
        if meta is None:
            row["_metadata_manifest_missing"] = True
        merged.append(normalize_record(row, source_path=record.source_path, source_index=record.source_index))
    return merged


def discover_input_files(input_path: Path) -> list[Path]:
    """Discover JSON/JSONL input in a stable order, without writing anywhere."""
    if input_path.is_file():
        return [input_path]
    if not input_path.is_dir():
        raise FileNotFoundError(input_path)
    return sorted(
        path for path in input_path.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".jsonl"}
    )


def build_manifest(input_files: Sequence[Path], report: Mapping[str, Any], *, read_errors: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    """Create a reproducible manifest; hashes inputs and report, never writes input."""
    files = []
    for path in input_files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append({"path": str(path), "sha256": digest, "bytes": path.stat().st_size})
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "input_files": files,
        "read_errors": list(read_errors),
        "report_sha256": report["report_sha256"],
        "documents": report["summary"]["documents"],
    }
