"""Offline, receipt-gated intake for AI-Hub dataset 71813.

The script never logs in, downloads, or contacts AI-Hub.  It accepts only a
user-provided extracted directory and a separately stored approval receipt.
Training records are emitted only after the receipt contract and its evidence
hash validate.  The resulting records are training-only: redistribution,
third-party access, evaluation, and golden-set use are explicitly disabled.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import unicodedata
import uuid
from urllib.parse import parse_qs, urlparse


_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
sys.path.insert(0, str(_POC))
sys.path.insert(0, str(_POC / "src"))

from koipa.proxy_corpus import validate_proxy_record  # noqa: E402


DATASET_ID = "71813"
DATASET_TITLE = "멀티모달 정보검색 데이터"
SOURCE_ID = "aihub-71813-multimodal-information-retrieval"
DATASET_PAGE_URL = (
    "https://www.aihub.or.kr/aihubdata/data/view.do?"
    "aihubDataSe=realm&currMenu=115&dataSetSn=71813&topMenu=100"
)
TERMS_URL = "https://www.aihub.or.kr/intrcn/guid/usagepolicy.do"
RECEIPT_SCHEMA = "aihub-approval-receipt-v1"
RUN_SCHEMA = "aihub-71813-training-intake-v1"
RECORD_SCHEMA = "aihub-71813-training-candidate-v1"
PERMISSION_VALIDATOR = "aihub-71813-receipt-contract-v1"
SUPPORTED_SUFFIXES = frozenset({".pdf", ".txt", ".json"})
MIN_CHARS = 1_200
MAX_CHARS = 3_200
ATOM_MAX_CHARS = 800
MAX_METADATA_BYTES = 16 * 1024 * 1024
MAX_TEXT_BYTES = 32 * 1024 * 1024
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_RECEIPT_EVIDENCE_BYTES = 32 * 1024 * 1024
MAX_FILES = 300_000
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PLACEHOLDER = re.compile(r"(?i)(?:replace|todo|example|placeholder|입력|작성)")
_STRONG_PII_PATTERNS = {
    "resident_registration_number": re.compile(
        r"(?<!\d)\d{6}[ -]?[1-8]\d{6}(?!\d)"
    ),
    "foreign_registration_number": re.compile(
        r"(?<!\d)\d{6}[ -]?[5-8]\d{6}(?!\d)"
    ),
}
REQUIRED_RESTRICTIONS = frozenset(
    {
        "model_training_only",
        "no_redistribution",
        "no_third_party_access",
        "no_foreign_transfer_without_separate_agreement",
        "no_reidentification",
        "report_personal_data_and_delete_dataset",
        "no_dataset_sale_without_separate_agreement",
        "nia_attribution_required",
    }
)
_PROHIBITED_PERMISSION_FLAGS = (
    "redistribution_permitted",
    "third_party_access_permitted",
    "foreign_transfer_permitted",
    "evaluation_use_permitted",
    "golden_set_use_permitted",
    "dataset_sale_permitted",
)


class IntakeError(ValueError):
    """The requested intake violates a provenance or permission contract."""


@dataclass(frozen=True)
class ApprovalContract:
    receipt_sha256: str
    evidence_sha256: str
    contract_sha256: str
    recipient_sha256: str
    approval_reference_sha256: str
    dataset_version: str
    downloaded_at: str
    attribution_text: str
    restrictions: tuple[str, ...]


@dataclass(frozen=True)
class FileEntry:
    path: Path
    relative_path: str
    suffix: str
    size_bytes: int
    sha256: str

    def audit_dict(self) -> dict[str, object]:
        return {
            "path": self.relative_path,
            "suffix": self.suffix,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class PageSource:
    family_key: str
    page_number: int
    text: str
    title: str
    document_type: str
    publisher: str
    raw_data_name: str
    source_pdf_name: str
    json_file: FileEntry
    text_file: FileEntry | None
    pdf_file: FileEntry | None
    text_origin: str

    @property
    def normalized_sha256(self) -> str:
        return _sha256_bytes(self.text.encode("utf-8"))


@dataclass(frozen=True)
class TextAtom:
    text: str
    page: PageSource
    start: int
    end: int


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _require_nonempty(raw: Mapping[str, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise IntakeError(f"approval receipt missing non-empty field: {field}")
    value = value.strip()
    if _PLACEHOLDER.search(value):
        raise IntakeError(f"approval receipt contains placeholder: {field}")
    return value


def _require_true(raw: Mapping[str, object], field: str) -> None:
    if raw.get(field) is not True:
        raise IntakeError(f"approval receipt requires true: {field}")


def _require_false(raw: Mapping[str, object], field: str) -> None:
    if raw.get(field) is not False:
        raise IntakeError(f"approval receipt requires explicit false: {field}")


def _parse_aware_datetime(value: str, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IntakeError(f"approval receipt invalid datetime: {field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IntakeError(f"approval receipt datetime must include timezone: {field}")
    return parsed.isoformat()


def _validate_dataset_url(value: str) -> None:
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    query = parse_qs(parsed.query)
    if (
        parsed.scheme != "https"
        or host not in {"aihub.or.kr", "www.aihub.or.kr"}
        or query.get("dataSetSn") != [DATASET_ID]
    ):
        raise IntakeError("approval receipt dataset_page_url is not AI-Hub 71813")


def _validate_terms_url(value: str) -> None:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold()
        not in {"aihub.or.kr", "www.aihub.or.kr"}
        or not parsed.path.endswith("/intrcn/guid/usagepolicy.do")
    ):
        raise IntakeError("approval receipt terms_url is not the official AI-Hub policy")


def validate_approval_receipt(receipt_path: Path) -> ApprovalContract:
    """Validate a recipient-specific receipt and its separate evidence file.

    This is a syntactic and cryptographic evidence gate.  It does not represent
    an independent legal opinion or authenticate the issuer of the evidence.
    """
    if receipt_path.is_symlink():
        raise IntakeError("approval receipt must be a regular non-symlink file")
    receipt_path = receipt_path.resolve()
    if not receipt_path.is_file():
        raise IntakeError("approval receipt must be a regular non-symlink file")
    if receipt_path.stat().st_size > MAX_RECEIPT_BYTES:
        raise IntakeError("approval receipt exceeds size cap")
    receipt_bytes = receipt_path.read_bytes()
    try:
        raw = json.loads(receipt_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntakeError("approval receipt is not valid UTF-8 JSON") from exc
    if not isinstance(raw, Mapping):
        raise IntakeError("approval receipt root must be an object")
    if raw.get("schema") != RECEIPT_SCHEMA:
        raise IntakeError(f"approval receipt schema must be {RECEIPT_SCHEMA}")
    if str(raw.get("dataset_id") or "") != DATASET_ID:
        raise IntakeError(f"approval receipt dataset_id must be {DATASET_ID}")
    if str(raw.get("dataset_title") or "").strip() != DATASET_TITLE:
        raise IntakeError("approval receipt dataset_title mismatch")
    _validate_dataset_url(_require_nonempty(raw, "dataset_page_url"))
    _validate_terms_url(_require_nonempty(raw, "terms_url"))

    for field in ("approval_granted", "terms_accepted", "training_use_approved"):
        _require_true(raw, field)
    for field in _PROHIBITED_PERMISSION_FLAGS:
        _require_false(raw, field)
    if raw.get("use_scope") != "model_training_only":
        raise IntakeError("approval receipt use_scope must be model_training_only")

    recipient = _require_nonempty(raw, "approved_recipient_legal_name")
    approval_reference = _require_nonempty(raw, "approval_reference")
    dataset_version = _require_nonempty(raw, "dataset_version")
    downloaded_at = _parse_aware_datetime(
        _require_nonempty(raw, "download_completed_at"), "download_completed_at"
    )
    approval_issued_at = _parse_aware_datetime(
        _require_nonempty(raw, "approval_issued_at"), "approval_issued_at"
    )
    terms_accepted_at = _parse_aware_datetime(
        _require_nonempty(raw, "terms_accepted_at"), "terms_accepted_at"
    )
    downloaded_time = datetime.fromisoformat(downloaded_at)
    if downloaded_time < datetime.fromisoformat(approval_issued_at):
        raise IntakeError("download_completed_at precedes approval_issued_at")
    if downloaded_time < datetime.fromisoformat(terms_accepted_at):
        raise IntakeError("download_completed_at precedes terms_accepted_at")

    if raw.get("attribution_required") is not True:
        raise IntakeError("approval receipt must require NIA attribution")
    attribution = _require_nonempty(raw, "attribution_text")
    if "한국지능정보사회진흥원" not in attribution or "NIA" not in attribution:
        raise IntakeError("attribution_text must name 한국지능정보사회진흥원 (NIA)")

    restrictions_raw = raw.get("restrictions")
    if not isinstance(restrictions_raw, list) or not all(
        isinstance(value, str) and value.strip() for value in restrictions_raw
    ):
        raise IntakeError("approval receipt restrictions must be a non-empty string list")
    restrictions = frozenset(value.strip() for value in restrictions_raw)
    missing_restrictions = sorted(REQUIRED_RESTRICTIONS - restrictions)
    if missing_restrictions:
        raise IntakeError(
            "approval receipt missing restrictions: " + ",".join(missing_restrictions)
        )

    evidence = raw.get("receipt_evidence")
    if not isinstance(evidence, Mapping):
        raise IntakeError("approval receipt missing receipt_evidence object")
    evidence_relative = _require_nonempty(evidence, "path")
    evidence_path_raw = Path(evidence_relative)
    if evidence_path_raw.is_absolute():
        raise IntakeError("receipt evidence path must be relative to the receipt")
    unresolved_evidence_path = receipt_path.parent / evidence_path_raw
    if unresolved_evidence_path.is_symlink():
        raise IntakeError("receipt evidence must be a regular non-symlink file")
    evidence_path = unresolved_evidence_path.resolve()
    try:
        evidence_path.relative_to(receipt_path.parent)
    except ValueError as exc:
        raise IntakeError("receipt evidence path escapes the receipt directory") from exc
    if not evidence_path.is_file():
        raise IntakeError("receipt evidence must be a regular non-symlink file")
    if evidence_path.stat().st_size > MAX_RECEIPT_EVIDENCE_BYTES:
        raise IntakeError("receipt evidence exceeds size cap")
    declared_evidence_hash = str(evidence.get("sha256") or "").strip().casefold()
    if not _HEX_SHA256.fullmatch(declared_evidence_hash):
        raise IntakeError("receipt evidence sha256 must be 64 lowercase hex characters")
    observed_evidence_hash = _sha256_file(evidence_path)
    if observed_evidence_hash != declared_evidence_hash:
        raise IntakeError("receipt evidence sha256 mismatch")

    contract_material = {
        "dataset_id": DATASET_ID,
        "dataset_version": dataset_version,
        "recipient": recipient,
        "approval_reference": approval_reference,
        "approval_granted": True,
        "training_use_approved": True,
        "use_scope": "model_training_only",
        "prohibited_permission_flags": {
            field: False for field in _PROHIBITED_PERMISSION_FLAGS
        },
        "attribution": attribution,
        "restrictions": sorted(restrictions),
        "evidence_sha256": observed_evidence_hash,
    }
    return ApprovalContract(
        receipt_sha256=_sha256_bytes(receipt_bytes),
        evidence_sha256=observed_evidence_hash,
        contract_sha256=_sha256_bytes(_canonical_bytes(contract_material)),
        recipient_sha256=_sha256_bytes(recipient.encode("utf-8")),
        approval_reference_sha256=_sha256_bytes(
            approval_reference.encode("utf-8")
        ),
        dataset_version=dataset_version,
        downloaded_at=downloaded_at,
        attribution_text=attribution,
        restrictions=tuple(sorted(restrictions)),
    )


def _assert_separate_paths(
    source_root: Path, receipt_path: Path, output_root: Path
) -> tuple[Path, Path, Path]:
    if source_root.is_symlink():
        raise IntakeError("source root must be a regular extracted directory")
    if receipt_path.is_symlink():
        raise IntakeError("approval receipt must be a regular non-symlink file")
    if output_root.is_symlink():
        raise IntakeError("output root must not be a symlink")
    source_root = source_root.resolve()
    receipt_path = receipt_path.resolve()
    output_root = output_root.resolve()
    if not source_root.is_dir() or source_root.is_symlink():
        raise IntakeError("source root must be a regular extracted directory")
    try:
        receipt_path.relative_to(source_root)
    except ValueError:
        pass
    else:
        raise IntakeError("approval receipt must be stored outside the extracted data")
    try:
        output_root.relative_to(source_root)
    except ValueError:
        pass
    else:
        raise IntakeError("output root must be outside the extracted data")
    return source_root, receipt_path, output_root


def discover_files(source_root: Path, *, max_files: int = MAX_FILES) -> list[FileEntry]:
    """Hash supported files without following symlinks or reading document text."""
    entries: list[FileEntry] = []
    for path in sorted(source_root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise IntakeError(f"source tree contains a symlink: {path.relative_to(source_root)}")
        if not path.is_file() or path.suffix.casefold() not in SUPPORTED_SUFFIXES:
            continue
        if len(entries) >= max_files:
            raise IntakeError(f"source tree exceeds supported file count: {max_files}")
        relative = path.relative_to(source_root).as_posix()
        entries.append(
            FileEntry(
                path=path,
                relative_path=relative,
                suffix=path.suffix.casefold(),
                size_bytes=path.stat().st_size,
                sha256=_sha256_file(path),
            )
        )
    if not entries:
        raise IntakeError("source root contains no PDF, TXT, or JSON files")
    return entries


def _read_json(entry: FileEntry) -> Mapping[str, object]:
    if entry.size_bytes > MAX_METADATA_BYTES:
        raise IntakeError(f"JSON metadata exceeds size cap: {entry.relative_path}")
    try:
        payload = entry.path.read_bytes()
        if _sha256_bytes(payload) != entry.sha256:
            raise IntakeError(f"JSON metadata changed during intake: {entry.relative_path}")
        raw = json.loads(payload.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntakeError(f"invalid JSON metadata: {entry.relative_path}") from exc
    if not isinstance(raw, Mapping):
        raise IntakeError(f"JSON metadata root is not an object: {entry.relative_path}")
    return raw


def _decode_text(entry: FileEntry) -> tuple[str, str]:
    if entry.size_bytes > MAX_TEXT_BYTES:
        raise IntakeError(f"TXT source exceeds size cap: {entry.relative_path}")
    payload = entry.path.read_bytes()
    if _sha256_bytes(payload) != entry.sha256:
        raise IntakeError(f"TXT source changed during intake: {entry.relative_path}")
    for encoding in ("utf-8-sig", "utf-16", "cp949"):
        try:
            return payload.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise IntakeError(f"TXT source has unsupported encoding: {entry.relative_path}")


def normalize_document_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(
        char for char in text if char in "\n\t" or unicodedata.category(char) != "Cc"
    )
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _nested_mapping(raw: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = raw.get(key)
    return value if isinstance(value, Mapping) else {}


def _basename(value: object) -> str:
    return Path(str(value or "").replace("\\", "/")).name


def _family_key(raw_name: str, pdf_name: str, title: str, publisher: str) -> str:
    identity_name = Path(pdf_name or raw_name).stem.casefold()
    identity_name = re.sub(r"^(?:mi[123]_)", "", identity_name)
    identity_name = re.sub(r"_(?:page|p)?\d+\Z", "", identity_name)
    material = [identity_name, title.strip(), publisher.strip()]
    return _sha256_bytes(_canonical_bytes(material))[:24]


def _choose_named_file(
    name: str,
    *,
    index: Mapping[str, Sequence[FileEntry]],
    metadata: FileEntry,
) -> FileEntry | None:
    if not name:
        return None
    matches = list(index.get(Path(name).name.casefold(), ()))
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    unique_hashes = {match.sha256 for match in matches}
    if len(unique_hashes) == 1:
        return sorted(matches, key=lambda row: row.relative_path)[0]

    metadata_parts = metadata.path.parent.parts

    def common_parent_score(entry: FileEntry) -> tuple[int, str]:
        score = 0
        for left, right in zip(metadata_parts, entry.path.parent.parts, strict=False):
            if left != right:
                break
            score += 1
        return (-score, entry.relative_path)

    ranked = sorted(matches, key=common_parent_score)
    first_score = common_parent_score(ranked[0])[0]
    second_score = common_parent_score(ranked[1])[0]
    if first_score == second_score:
        raise IntakeError(
            f"ambiguous source filename {name!r} for {metadata.relative_path}"
        )
    return ranked[0]


def _page_number(value: object, metadata: FileEntry) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        match = re.search(r"_(\d+)(?:\.[^.]+)?\Z", metadata.path.name)
        if not match:
            raise IntakeError(f"metadata has no valid page number: {metadata.relative_path}")
        number = int(match.group(1))
    if number <= 0:
        raise IntakeError(f"metadata page number must be positive: {metadata.relative_path}")
    return number


def build_pages(
    entries: Sequence[FileEntry],
) -> tuple[list[PageSource], list[dict[str, str]], dict[str, str]]:
    """Join page JSON to its declared TXT/PDF source without filename guessing."""
    by_basename: dict[str, list[FileEntry]] = defaultdict(list)
    for entry in entries:
        by_basename[entry.path.name.casefold()].append(entry)

    pages: list[PageSource] = []
    holds: list[dict[str, str]] = []
    text_encodings: dict[str, str] = {}
    seen_json_hashes: set[str] = set()
    for metadata in (entry for entry in entries if entry.suffix == ".json"):
        if metadata.sha256 in seen_json_hashes:
            holds.append(
                {"path": metadata.relative_path, "reason": "duplicate_json_file_hash"}
            )
            continue
        seen_json_hashes.add(metadata.sha256)
        try:
            raw = _read_json(metadata)
            metadata_privacy = _privacy_hits(
                json.dumps(raw, ensure_ascii=False, sort_keys=True)
            )
            if metadata_privacy:
                raise IntakeError(
                    "strong_pii_detected_in_metadata:" + ",".join(metadata_privacy)
                )
            raw_info = _nested_mapping(raw, "raw_data_info")
            source_info = _nested_mapping(raw, "source_data_info")
            learning_info = _nested_mapping(raw, "learning_data_info")
            if not raw_info or not source_info or not learning_info:
                raise IntakeError("missing AI-Hub raw/source/learning metadata objects")
            raw_name = _basename(raw_info.get("raw_data_name"))
            pdf_name = _basename(source_info.get("source_data_name_pdf"))
            txt_name = _basename(source_info.get("source_data_name_txt"))
            title = str(raw_info.get("doc_name") or "").strip()
            publisher = str(raw_info.get("publisher") or "").strip()
            document_type = str(raw_info.get("doc_type") or "report").strip()
            if not raw_name or not title:
                raise IntakeError("missing raw_data_name or doc_name")
            page_number = _page_number(learning_info.get("page_num"), metadata)
            text_file = _choose_named_file(
                txt_name, index=by_basename, metadata=metadata
            )
            pdf_file = _choose_named_file(
                pdf_name, index=by_basename, metadata=metadata
            )
            if text_file is not None:
                decoded, encoding = _decode_text(text_file)
                text = normalize_document_text(decoded)
                text_origin = "declared_txt"
                text_encodings[text_file.relative_path] = encoding
            else:
                text = normalize_document_text(learning_info.get("visual_context"))
                text_origin = "json_visual_context"
            if not text:
                raise IntakeError("page has no usable declared TXT or visual_context")
            pages.append(
                PageSource(
                    family_key=_family_key(raw_name, pdf_name, title, publisher),
                    page_number=page_number,
                    text=text,
                    title=title,
                    document_type=document_type,
                    publisher=publisher,
                    raw_data_name=raw_name,
                    source_pdf_name=pdf_name,
                    json_file=metadata,
                    text_file=text_file,
                    pdf_file=pdf_file,
                    text_origin=text_origin,
                )
            )
        except IntakeError as exc:
            holds.append({"path": metadata.relative_path, "reason": str(exc)})
    return pages, holds, text_encodings


def _privacy_hits(text: str) -> list[str]:
    return sorted(name for name, pattern in _STRONG_PII_PATTERNS.items() if pattern.search(text))


def _cut_position(text: str, start: int) -> int:
    hard_end = min(len(text), start + ATOM_MAX_CHARS)
    if hard_end == len(text):
        return hard_end
    soft_start = min(hard_end, start + ATOM_MAX_CHARS // 2)
    window = text[soft_start:hard_end]
    candidates = [window.rfind(marker) for marker in ("\n\n", ". ", "다. ", "요. ")]
    offset = max(candidates)
    return soft_start + offset + 1 if offset >= 0 else hard_end


def _page_atoms(page: PageSource) -> list[TextAtom]:
    atoms: list[TextAtom] = []
    cursor = 0
    while cursor < len(page.text):
        end = _cut_position(page.text, cursor)
        raw_piece = page.text[cursor:end]
        leading = len(raw_piece) - len(raw_piece.lstrip())
        trailing = len(raw_piece.rstrip())
        piece_start = cursor + leading
        piece_end = cursor + trailing
        piece = page.text[piece_start:piece_end]
        if piece:
            atoms.append(TextAtom(piece, page, piece_start, piece_end))
        cursor = end
    return atoms


def _atoms_text(atoms: Sequence[TextAtom]) -> str:
    return "\n\n".join(atom.text for atom in atoms).strip()


def _pack_atoms(atoms: Sequence[TextAtom]) -> tuple[list[list[TextAtom]], int]:
    chunks: list[list[TextAtom]] = []
    current: list[TextAtom] = []
    for atom in atoms:
        candidate = [*current, atom]
        if current and len(_atoms_text(candidate)) > MAX_CHARS:
            chunks.append(current)
            current = [atom]
        else:
            current = candidate
    if current:
        chunks.append(current)

    if len(chunks) >= 2 and len(_atoms_text(chunks[-1])) < MIN_CHARS:
        previous = chunks[-2]
        tail = chunks[-1]
        while len(_atoms_text(tail)) < MIN_CHARS and len(previous) > 1:
            moved = previous[-1]
            proposed_previous = previous[:-1]
            proposed_tail = [moved, *tail]
            if len(_atoms_text(proposed_previous)) < MIN_CHARS:
                break
            if len(_atoms_text(proposed_tail)) > MAX_CHARS:
                break
            previous[:] = proposed_previous
            tail[:] = proposed_tail
        if len(_atoms_text(tail)) < MIN_CHARS:
            combined = [*previous, *tail]
            if len(_atoms_text(combined)) <= MAX_CHARS:
                chunks[-2:] = [combined]

    held_chars = 0
    if chunks and len(_atoms_text(chunks[-1])) < MIN_CHARS:
        held_chars = len(_atoms_text(chunks[-1]))
        chunks.pop()
    return chunks, held_chars


def _lineage(atoms: Sequence[TextAtom]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for atom in atoms:
        page = atom.page
        key = (
            page.page_number,
            page.json_file.relative_path,
            page.text_file.relative_path if page.text_file else None,
        )
        if rows and rows[-1]["_key"] == key and int(rows[-1]["char_end"]) <= atom.start:
            rows[-1]["char_end"] = atom.end
            continue
        rows.append(
            {
                "_key": key,
                "page_number": page.page_number,
                "normalized_page_sha256": page.normalized_sha256,
                "char_start": atom.start,
                "char_end": atom.end,
                "text_origin": page.text_origin,
                "json_path": page.json_file.relative_path,
                "json_sha256": page.json_file.sha256,
                "txt_path": page.text_file.relative_path if page.text_file else None,
                "txt_sha256": page.text_file.sha256 if page.text_file else None,
            }
        )
    for row in rows:
        row.pop("_key")
    return rows


def _document_type(value: str) -> str:
    normalized = value.strip().casefold()
    if "보도" in normalized or "press" in normalized:
        return "government_press_release"
    return "public_report"


def materialize_records(
    pages: Sequence[PageSource], contract: ApprovalContract
) -> tuple[list[dict[str, object]], dict[str, object]]:
    by_family: dict[str, list[PageSource]] = defaultdict(list)
    for page in pages:
        by_family[page.family_key].append(page)

    records: list[dict[str, object]] = []
    seen_page_hashes: set[str] = set()
    seen_candidate_hashes: set[str] = set()
    counters: Counter[str] = Counter()
    held_families: list[dict[str, object]] = []
    for family_key in sorted(by_family):
        family_pages = sorted(
            by_family[family_key],
            key=lambda row: (row.page_number, row.json_file.relative_path),
        )
        privacy = sorted(
            {
                hit
                for page in family_pages
                for hit in _privacy_hits(page.text)
            }
        )
        if privacy:
            raise IntakeError(
                "strong PII pattern detected; stop use, report to AI-Hub, and "
                "delete the source dataset according to policy: "
                + ",".join(privacy)
            )

        unique_pages: list[PageSource] = []
        for page in family_pages:
            if page.normalized_sha256 in seen_page_hashes:
                counters["duplicate_pages_dropped"] += 1
                continue
            seen_page_hashes.add(page.normalized_sha256)
            unique_pages.append(page)
        if not unique_pages:
            counters["empty_after_page_dedupe_families"] += 1
            continue

        atoms = [atom for page in unique_pages for atom in _page_atoms(page)]
        chunks, held_chars = _pack_atoms(atoms)
        if held_chars:
            counters["short_tail_chars_held"] += held_chars
        if not chunks:
            counters["too_short_families"] += 1
            continue

        family_files = {
            (entry.relative_path, entry.sha256)
            for page in unique_pages
            for entry in (page.json_file, page.text_file, page.pdf_file)
            if entry is not None
        }
        family_file_rows = [
            {"path": path, "sha256": digest}
            for path, digest in sorted(family_files)
        ]
        family_sha256 = _sha256_bytes(_canonical_bytes(family_file_rows))
        pdf_hashes = sorted(
            {page.pdf_file.sha256 for page in unique_pages if page.pdf_file is not None}
        )
        source_sha256 = (
            pdf_hashes[0]
            if len(pdf_hashes) == 1
            else _sha256_bytes(_canonical_bytes(pdf_hashes or family_file_rows))
        )
        family_id = f"aihub-{DATASET_ID}-{family_key}"
        exemplar = unique_pages[0]
        for chunk_index, chunk_atoms in enumerate(chunks, start=1):
            text = _atoms_text(chunk_atoms)
            text_sha256 = _sha256_bytes(text.encode("utf-8"))
            if text_sha256 in seen_candidate_hashes:
                counters["duplicate_candidates_dropped"] += 1
                continue
            seen_candidate_hashes.add(text_sha256)
            record = {
                "schema": RECORD_SCHEMA,
                "doc_id": f"{family_id}-s{chunk_index:03d}",
                "text": text,
                "label": "S3",
                "document_origin": "public_real",
                "proxy_role": "public_document",
                "document_family_id": family_id,
                "document_family_sha256": family_sha256,
                "document_type": _document_type(exemplar.document_type),
                "domain": "public_document",
                "industry": "public_sector",
                "source_id": SOURCE_ID,
                "source_reference": DATASET_PAGE_URL,
                "source_license": "EXPLICIT-ML-TRAINING",
                "source_sha256": source_sha256,
                "source_document_sha256": source_sha256,
                "source_file_hashes": family_file_rows,
                "candidate_text_sha256": text_sha256,
                "source_title": exemplar.title,
                "source_publisher": exemplar.publisher,
                "source_raw_data_name": exemplar.raw_data_name,
                "source_pdf_name": exemplar.source_pdf_name,
                "page_lineage": _lineage(chunk_atoms),
                "retrieved_at": contract.downloaded_at,
                "training_use_permitted": True,
                "evaluation_use_permitted": False,
                "golden_set_use_permitted": False,
                "redistribution_permitted": False,
                "third_party_access_permitted": False,
                "foreign_transfer_permitted": False,
                "dataset_sale_permitted": False,
                "source_use_scope": "model_training_only",
                "attribution_required": True,
                "attribution_text": contract.attribution_text,
                "usage_restrictions": list(contract.restrictions),
                "permission_validator": PERMISSION_VALIDATOR,
                "permission_contract_status": "validated",
                "approval_receipt_sha256": contract.receipt_sha256,
                "approval_evidence_sha256": contract.evidence_sha256,
                "approval_contract_sha256": contract.contract_sha256,
                "license_evidence_sha256": contract.evidence_sha256,
                "aihub_dataset_id": DATASET_ID,
                "aihub_dataset_version": contract.dataset_version,
            }
            check = validate_proxy_record(
                record, stage="candidate", intended_use="training"
            )
            if not check.ok:
                counters["proxy_quality_held"] += 1
                for error in check.errors:
                    counters[f"proxy_error:{error.split(':', 1)[0]}"] += 1
                continue
            records.append(record)
    records.sort(key=lambda row: str(row["doc_id"]))
    return records, {
        "family_count": len(by_family),
        "eligible_record_count": len(records),
        "counters": dict(sorted(counters.items())),
        "held_families": held_families,
    }


def _jsonl_bytes(rows: Sequence[Mapping[str, object]]) -> bytes:
    return b"".join(_canonical_bytes(dict(row)) for row in rows)


def _write_new(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _verify_inventory_unchanged(entries: Sequence[FileEntry]) -> None:
    for entry in entries:
        if not entry.path.is_file() or entry.path.is_symlink():
            raise IntakeError(f"source file changed during intake: {entry.relative_path}")
        if entry.path.stat().st_size != entry.size_bytes:
            raise IntakeError(f"source file changed during intake: {entry.relative_path}")
        if _sha256_file(entry.path) != entry.sha256:
            raise IntakeError(f"source file changed during intake: {entry.relative_path}")


def run_intake(
    *,
    source_root: Path,
    receipt_path: Path,
    output_root: Path,
    run_id: str,
    max_files: int = MAX_FILES,
) -> tuple[Path, dict[str, object]]:
    """Create one immutable train-only run and return its path and manifest."""
    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise IntakeError("run_id must contain only safe filename characters")
    source_root, receipt_path, output_root = _assert_separate_paths(
        source_root, receipt_path, output_root
    )
    contract = validate_approval_receipt(receipt_path)
    final_dir = output_root / run_id
    if final_dir.exists():
        raise IntakeError(f"refusing to replace immutable run: {final_dir}")

    started_at = datetime.now(timezone.utc).isoformat()
    entries = discover_files(source_root, max_files=max_files)
    pages, metadata_holds, text_encodings = build_pages(entries)
    if any(
        "strong_pii_detected" in str(hold.get("reason") or "")
        for hold in metadata_holds
    ):
        raise IntakeError(
            "strong PII pattern detected in metadata; stop use, report to AI-Hub, "
            "and delete the source dataset according to policy"
        )
    records, record_stats = materialize_records(pages, contract)
    if not records:
        raise IntakeError("no training candidates passed lineage and quality gates")

    inventory_rows = [entry.audit_dict() for entry in entries]
    inventory_bytes = _jsonl_bytes(inventory_rows)
    records_bytes = _jsonl_bytes(records)
    inventory_sha256 = _sha256_bytes(inventory_bytes)
    records_sha256 = _sha256_bytes(records_bytes)
    root_fingerprint = _sha256_bytes(
        _canonical_bytes(
            [{"path": row["path"], "sha256": row["sha256"]} for row in inventory_rows]
        )
    )
    completed_at = datetime.now(timezone.utc).isoformat()
    manifest: dict[str, object] = {
        "schema": RUN_SCHEMA,
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "dataset": {
            "id": DATASET_ID,
            "title": DATASET_TITLE,
            "version": contract.dataset_version,
            "official_page": DATASET_PAGE_URL,
            "source_id": SOURCE_ID,
        },
        "input": {
            "root_name": source_root.name,
            "root_fingerprint_sha256": root_fingerprint,
            "supported_file_count": len(entries),
            "file_counts": dict(sorted(Counter(row.suffix for row in entries).items())),
            "text_encodings": dict(sorted(text_encodings.items())),
        },
        "approval": {
            "validator": PERMISSION_VALIDATOR,
            "status": "validated",
            "receipt_sha256": contract.receipt_sha256,
            "evidence_sha256": contract.evidence_sha256,
            "contract_sha256": contract.contract_sha256,
            "recipient_sha256": contract.recipient_sha256,
            "approval_reference_sha256": contract.approval_reference_sha256,
            "validation_limit": (
                "syntactic and hash validation only; not independent issuer authentication "
                "or legal advice"
            ),
        },
        "permission_scope": {
            "training_use_permitted": True,
            "evaluation_use_permitted": False,
            "golden_set_use_permitted": False,
            "redistribution_permitted": False,
            "third_party_access_permitted": False,
            "foreign_transfer_permitted": False,
            "dataset_sale_permitted": False,
            "attribution_required": True,
            "attribution_text": contract.attribution_text,
            "restrictions": list(contract.restrictions),
        },
        "normalization": {
            "unicode": "NFKC",
            "minimum_chars": MIN_CHARS,
            "maximum_chars": MAX_CHARS,
            "page_order_preserved": True,
            "family_atomicity_required_downstream": True,
            "strong_pii_patterns_fail_closed": sorted(_STRONG_PII_PATTERNS),
        },
        "summary": {
            "parsed_page_count": len(pages),
            "metadata_hold_count": len(metadata_holds),
            "metadata_holds": metadata_holds,
            **record_stats,
        },
        "artifacts": {
            "inventory": {"path": "inventory.jsonl", "sha256": inventory_sha256},
            "records": {"path": "records.jsonl", "sha256": records_sha256},
        },
        "prohibitions": [
            "Never publish, redistribute, email, or expose source or derived records.",
            "Never place these records in frozen evaluation or a golden set.",
            "Never permit a different legal entity or unapproved person to access them.",
            "Never transfer them abroad without a separate NIA/provider agreement.",
            "If personal data is found, stop use, report it to AI-Hub, and delete the dataset.",
        ],
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    complete = {
        "schema": "immutable-run-complete-v1",
        "run_id": run_id,
        "completed_at": completed_at,
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "inventory_sha256": inventory_sha256,
        "records_sha256": records_sha256,
        "record_count": len(records),
    }
    complete_bytes = (
        json.dumps(complete, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    _verify_inventory_unchanged(entries)
    if validate_approval_receipt(receipt_path) != contract:
        raise IntakeError("approval receipt changed during intake")
    output_root.mkdir(parents=True, exist_ok=True)
    staging = output_root / f".{run_id}.tmp-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        _write_new(staging / "inventory.jsonl", inventory_bytes)
        _write_new(staging / "records.jsonl", records_bytes)
        _write_new(staging / "manifest.json", manifest_bytes)
        _write_new(staging / "COMPLETE.json", complete_bytes)
        if final_dir.exists():
            raise IntakeError(f"refusing to replace immutable run: {final_dir}")
        staging.rename(final_dir)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return final_dir, manifest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Offline, approval-receipt-gated AI-Hub 71813 training intake. "
            "This command performs no network access or download."
        )
    )
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--approval-receipt", required=True)
    parser.add_argument(
        "--output-root", default=str(_POC / "datasets" / "proxy_gold" / "aihub_runs")
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--max-files", type=int, default=MAX_FILES)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run_dir, manifest = run_intake(
            source_root=Path(args.source_root),
            receipt_path=Path(args.approval_receipt),
            output_root=Path(args.output_root),
            run_id=args.run_id,
            max_files=args.max_files,
        )
    except IntakeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "run_dir": str(run_dir),
                "record_count": manifest["summary"]["eligible_record_count"],
                "permission_scope": "model_training_only",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
