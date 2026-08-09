"""Synthetic proxy-gold candidate inventory and administrative decision ledger.

This service deliberately keeps curated synthetic candidates separate from the
real-document golden corpus.  An administrative approval produces only
``approved_proxy``; it never creates a locked evaluation record.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4


_POC_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_ROOT = _POC_ROOT / "datasets" / "proxy_gold" / "single_document_candidates"
_LEDGER_NAME = "candidate_decisions.jsonl"
_VALID_GRADES = {"TS", "S1", "S2", "S3"}
_VALID_ACTIONS = {"approve", "change", "defer", "reject", "discard", "reopen"}
_DOCUMENT_ORIGINS = {"uploaded_document", "public_real", "organization_real"}


@contextlib.contextmanager
def _exclusive_ledger_lock(lock_path: Path) -> Iterator[None]:
    """Cross-process advisory lock for the small append-only decision ledger."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    try:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        if os.name == "nt":
            import msvcrt  # type: ignore[attr-defined]
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name != "nt":
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            # Windows releases msvcrt byte-range locks on handle close.  Explicit
            # LK_UNLCK has proved unreliable after buffered writes on Windows.
        finally:
            handle.close()


class ProxyGoldCandidateService:
    """Read synthetic candidates and record append-only manager decisions."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root).resolve() if root else _DEFAULT_ROOT.resolve()
        self.ledger_path = self.root / _LEDGER_NAME
        self.lock_path = self.root / f"{_LEDGER_NAME}.lock"

    def list_candidates(
        self, *, status: str | None = None, grade: str | None = None,
        origin: str | None = None, query: str | None = None,
    ) -> dict[str, Any]:
        all_candidates = self._candidates()
        candidates = all_candidates
        if status:
            candidates = [c for c in candidates if c["status"] == status]
        if grade:
            candidates = [c for c in candidates if c["final_grade"] == grade or c["proposed_grade"] == grade]
        if origin:
            candidates = [c for c in candidates if c["document_origin"] == origin]
        if query:
            needle = query.strip().lower()
            if needle:
                candidates = [c for c in candidates if needle in c["doc_id"].lower() or needle in c["title"].lower()]
        candidates.sort(key=lambda c: c["doc_id"])
        return {
            "total": len(candidates),
            "summary": self._summary(all_candidates),
            "candidates": [{k: v for k, v in c.items() if k != "text"} for c in candidates],
        }

    def summary(self) -> dict[str, Any]:
        return self._summary(self._candidates())

    def get_candidate(self, doc_id: str) -> dict[str, Any] | None:
        candidate = next((c for c in self._candidates() if c["doc_id"] == doc_id), None)
        if candidate is not None:
            candidate = dict(candidate)
            candidate["decision_history"] = self._history(doc_id)
        return candidate

    def decide(
        self,
        *,
        doc_id: str,
        action: str,
        actor_id: str,
        grade: str | None = None,
        reason: str = "",
    ) -> dict[str, Any] | None:
        if action not in _VALID_ACTIONS:
            raise ValueError("unsupported decision action")
        reason = reason.strip()
        if action in {"change", "defer", "reject", "discard"} and not reason:
            raise ValueError("reason is required for change, defer, reject, and discard")
        if action == "change" and grade not in _VALID_GRADES:
            raise ValueError("grade is required for change")
        if action != "change" and grade is not None:
            raise ValueError("grade is allowed only for change")
        candidate = self.get_candidate(doc_id)
        if candidate is None:
            return None
        is_synthetic = candidate["document_origin"] == "synthetic"
        if action == "approve" and (not is_synthetic or candidate["proposed_grade"] not in _VALID_GRADES):
            raise ValueError("approve is allowed only for a synthetic candidate with a proposed grade")

        final_grade = candidate["proposed_grade"] if action == "approve" else grade
        status = {
            "approve": "approved_proxy",  # synthetic only (guarded above)
            "change": "approved_proxy" if is_synthetic else "grade_fixed_unlocked",
            "defer": "deferred",
            "reject": "discarded",        # legacy API action retained
            "discard": "discarded",
            "reopen": "proposed" if is_synthetic else "under_review",
        }[action]
        if action in {"defer", "reject", "discard", "reopen"}:
            final_grade = None
        event = {
            "schema_version": 1,
            "event_id": str(uuid4()),
            "doc_id": doc_id,
            "action": action,
            "status": status,
            "proposed_grade": candidate["proposed_grade"],
            "final_grade": final_grade,
            "reason": reason,
            "actor_id": actor_id,
            "decided_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "document_sha256": candidate["document_sha256"],
            "document_origin": candidate["document_origin"],
            "claim_scope": candidate["claim_scope"],
        }
        self.root.mkdir(parents=True, exist_ok=True)
        with _exclusive_ledger_lock(self.lock_path):
            with self.ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return self.get_candidate(doc_id)

    def create_uploaded_candidate(
        self, *, filename: str, content: bytes, actor_id: str,
        document_origin: str = "uploaded_document", source_reference: str = "",
        authorization_basis: str = "", title: str = "",
    ) -> dict[str, Any]:
        """Store an uploaded document as an ungraded review item.

        ``public_real`` and ``organization_real`` are *intake* origins, not a
        locked-gold claim.  They require an explicit source reference and usage
        authorization so real documents cannot silently enter under the generic
        upload label.  Upload never sends content to an LLM.
        """
        if not content:
            raise ValueError("empty file")
        document_origin = document_origin.strip()
        if document_origin not in _DOCUMENT_ORIGINS:
            raise ValueError("unsupported document origin")
        source_reference = source_reference.strip()
        authorization_basis = authorization_basis.strip()
        is_actual_intake = document_origin in {"public_real", "organization_real"}
        if is_actual_intake and not source_reference:
            raise ValueError("source reference is required for an actual-document intake")
        if is_actual_intake and not authorization_basis:
            raise ValueError("usage authorization is required for an actual-document intake")
        safe_name = Path(filename or "uploaded_document").name
        suffix = Path(safe_name).suffix.lower()
        if not suffix:
            raise ValueError("filename extension is required")
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
            temp.write(content)
            temp_path = Path(temp.name)
        try:
            from lloydk.modules.m2_preprocess.extractor import extract  # noqa: PLC0415
            extracted = extract(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)
        if extracted.error:
            raise ValueError(f"extraction failed: {extracted.error}")
        text = (extracted.text or "").strip()
        if len(text) < 80:
            raise ValueError("extracted text is too short; scan/OCR or source file review is required")

        doc_id = f"GOLD-UPL-{uuid4().hex[:12].upper()}"
        safe_stem = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", Path(safe_name).stem).strip("._") or "uploaded"
        title = title.strip() or safe_stem
        source_dir = self.root / "uploaded_originals"
        source_path = source_dir / f"{doc_id}_{safe_stem}{suffix}"
        markdown_path = self.root / f"{doc_id}_{safe_stem}.md"
        meta_path = self.root / f"{doc_id}.metadata.json"
        raw_hash = hashlib.sha256(content).hexdigest()
        metadata = {
            "doc_id": doc_id,
            "intended_label": None,
            "document_origin": document_origin,
            "document_type": title,
            "authoring_method": "operator_upload",
            "requires_manual_audit": True,
            "candidate_status": "under_review",
            "uploaded_by": actor_id,
            "uploaded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_filename": safe_name,
            "source_file_sha256": raw_hash,
            "provenance": {
                "source_reference": source_reference or None,
                "authorization_basis": authorization_basis or None,
                "recorded_by": actor_id,
                "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "status": "recorded" if is_actual_intake else "not_declared",
            },
            "extraction": {
                "method": extracted.method,
                "quality": extracted.quality,
                "ocr_used": extracted.ocr_used,
                "pages_processed": extracted.pages,
                "pages_total": extracted.total_pages,
                "warnings": extracted.warnings,
                "table_coverage": extracted.table_coverage,
            },
            "claim_scope": (
                "actual-document intake with recorded provenance; awaiting human review; "
                "not locked gold and not a claim of operational accuracy"
                if is_actual_intake else
                "operator-uploaded document awaiting provenance and human review; "
                "not locked gold and not a claim of operational accuracy"
            ),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(parents=True, exist_ok=True)
        with _exclusive_ledger_lock(self.lock_path):
            if source_path.exists() or markdown_path.exists() or meta_path.exists():
                raise RuntimeError("generated upload identifier collision")
            source_path.write_bytes(content)
            markdown_path.write_text(text + "\n", encoding="utf-8")
            meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        candidate = self.get_candidate(doc_id)
        assert candidate is not None
        return candidate

    def _candidates(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        latest = self._latest_decisions()
        rows: list[dict[str, Any]] = []
        for meta_path in sorted(self.root.glob("*.metadata.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            doc_id = str(meta.get("doc_id") or "")
            if not doc_id:
                continue
            revision = str(meta.get("content_revision_path") or "").strip()
            revision_path = (self.root / revision).resolve() if revision else None
            if revision_path and revision_path.is_relative_to(self.root) and revision_path.is_file():
                source = revision_path
            else:
                docs = list(self.root.glob(f"{doc_id}_*.md"))
                if len(docs) != 1:
                    continue
                source = docs[0]
            text = source.read_text(encoding="utf-8")
            decision = latest.get(doc_id, {})
            document_origin = str(meta.get("document_origin") or "unknown")
            proposed = str(meta.get("intended_label") or "") or None
            proposed_basis = None
            if proposed is None and document_origin == "public_real":
                proposed = "S3"
                proposed_basis = "public source recorded; human confirmation pending"
            try:
                document_path = str(source.relative_to(_POC_ROOT))
            except ValueError:
                # 테스트/운영 도구가 별도 루트를 주입한 경우에도 목록 자체는 제공한다.
                document_path = str(source)
            rows.append({
                "doc_id": doc_id,
                "title": str(meta.get("document_type") or source.stem),
                "proposed_grade": proposed,
                "proposed_grade_basis": proposed_basis,
                "final_grade": decision.get("final_grade"),
                "status": decision.get("status") or str(meta.get("candidate_status") or "proposed"),
                "document_origin": document_origin,
                "requires_manual_audit": bool(meta.get("requires_manual_audit")),
                "claim_scope": str(meta.get("claim_scope") or ""),
                "document_path": document_path,
                "content_revision": str(meta.get("content_revision") or "v1"),
                "characters": len(text),
                "document_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "latest_decision": decision or None,
                "grade_fixed": bool(decision.get("final_grade")),
                "extraction": meta.get("extraction"),
                "provenance": meta.get("provenance") or {},
                "source_file_sha256": str(meta.get("source_file_sha256") or "") or None,
                "is_actual_document": document_origin in {"public_real", "organization_real"},
                "text": text,
            })
        return rows

    @staticmethod
    def _summary(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        actual = [c for c in candidates if c["is_actual_document"]]
        return {
            "total": len(candidates),
            "fixed": sum(1 for c in candidates if c["grade_fixed"]),
            "unfixed": sum(1 for c in candidates if not c["grade_fixed"] and c["status"] != "discarded"),
            "deferred": sum(1 for c in candidates if c["status"] == "deferred"),
            "discarded": sum(1 for c in candidates if c["status"] == "discarded"),
            "by_status": dict(sorted(Counter(c["status"] for c in candidates).items())),
            "by_origin": dict(sorted(Counter(c["document_origin"] for c in candidates).items())),
            "by_final_grade": dict(sorted(Counter(c["final_grade"] for c in candidates if c["final_grade"]).items())),
            "by_proposed_grade": dict(sorted(Counter(c["proposed_grade"] or "unassigned" for c in candidates).items())),
            "actual_document_intake": len(actual),
            "actual_provenance_recorded": sum(
                1 for c in actual if c.get("provenance", {}).get("status") == "recorded"
            ),
            "actual_grade_fixed_unlocked": sum(
                1 for c in actual if c["status"] == "grade_fixed_unlocked"
            ),
        }

    def _latest_decisions(self) -> dict[str, dict[str, Any]]:
        if not self.ledger_path.exists():
            return {}
        latest: dict[str, dict[str, Any]] = {}
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            doc_id = str(row.get("doc_id") or "")
            if doc_id:
                latest[doc_id] = row
        return latest

    def _history(self, doc_id: str) -> list[dict[str, Any]]:
        if not self.ledger_path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("doc_id") or "") == doc_id:
                events.append(row)
        return events
