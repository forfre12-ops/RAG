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

# 후보 목록 캐시 — 키에 (루트·파일수·최신 mtime·원장 mtime) 이 들어 있어 파일이 하나라도
# 바뀌면 자동 무효화된다. 캐시가 없으면 매 요청 30MB 본문을 다시 읽고 해시한다.
_CANDIDATE_CACHE: dict[tuple, list[dict[str, Any]]] = {}
_VALID_GRADES = {"TS", "S1", "S2", "S3"}
_VALID_ACTIONS = {"approve", "change", "defer", "reject", "discard", "reopen"}
# 본문에 등급 문자열이 그대로 남아 있으면 검수자가 읽기 전에 답을 본다.
_GRADE_TOKEN = re.compile(r"\b(TS|S1|S2|S3)\b")
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
        # 화면은 목록 응답에 실린 summary 로 KPI·품질 지표를 그린다(별도 /summary 를 안 부른다).
        # quality 를 여기 빼먹으면 품질 패널이 "지표를 낼 수 없습니다"로만 뜬다(실측).
        embedded = self._summary(all_candidates)
        embedded["quality"] = self._quality(all_candidates)
        return {
            "total": len(candidates),
            "summary": embedded,
            "candidates": [{k: v for k, v in c.items() if k != "text"} for c in candidates],
        }

    def summary(self) -> dict[str, Any]:
        candidates = self._candidates()
        out = self._summary(candidates)
        out["quality"] = self._quality(candidates)
        return out

    def recent_decisions(self, limit: int = 100) -> dict[str, Any]:
        """결정 원장 최근 기록 — 보류·폐기·번복까지 그대로 보인다.

        화면에는 문서 하나를 골라야 이력이 보였다. 보류·폐기가 왜 그렇게 됐는지 훑어보려면
        문서를 일일이 열어야 해서, 감사 목적으로는 쓸 수 없었다.
        """
        events: list[dict[str, Any]] = []
        if self.ledger_path.exists():
            for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        by_action = Counter(str(e.get("action") or "") for e in events)
        return {
            "total": len(events),
            "by_action": dict(sorted(by_action.items())),
            "events": list(reversed(events))[: max(1, min(int(limit), 500))],
        }

    @staticmethod
    def _quality(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """골든셋 자체의 건강도 — 등급을 맞히는 데 본문 말고 다른 단서가 섞였는지 본다.

        · length_only_1nn: 문서 **길이만** 보고 등급을 맞히는 비율. 무작위(0.25)를 크게 넘으면
          길이가 등급의 대리변수라는 뜻이고, 그 셋으로 잰 정확도는 부풀려진다.
          (실측: 기존 777건 패키지 0.793 — 짧으면 고등급 / 길면 S3)
        · grade_token_exposed: 본문에 자기 등급 문자열이 남아 있는 건수. 검수자가 문서를 읽기
          전에 답을 보면 검수가 검증이 아니라 확인 절차가 된다.
        """
        graded = [
            (len(c.get("text") or ""), c.get("final_grade") or c.get("proposed_grade"), c)
            for c in candidates
        ]
        graded = [(ln, g, c) for ln, g, c in graded if g in _VALID_GRADES and ln > 0]
        if not graded:
            return {"documents": 0}

        pairs = sorted((ln, g) for ln, g, _c in graded)
        hit = 0
        for i, (ln, lab) in enumerate(pairs):
            best, best_d = None, None
            for j in (i - 1, i + 1):
                if 0 <= j < len(pairs):
                    d = abs(pairs[j][0] - ln)
                    if best_d is None or d < best_d:
                        best_d, best = d, pairs[j][1]
            hit += best == lab
        leak = round(hit / len(pairs), 3)

        exposed = sum(1 for _ln, g, c in graded if _GRADE_TOKEN.search(c.get("text") or ""))
        per_grade: dict[str, dict[str, int]] = {}
        for g in sorted(_VALID_GRADES):
            v = sorted(ln for ln, gg, _c in graded if gg == g)
            if v:
                per_grade[g] = {"n": len(v), "min": v[0], "p50": v[len(v) // 2], "max": v[-1]}
        counts = [d["n"] for d in per_grade.values()]
        allv = sorted(ln for ln, _g, _c in graded)
        real = sum(1 for _ln, _g, c in graded if c.get("is_actual_document"))
        return {
            "documents": len(graded),
            "length": {"min": allv[0], "p50": allv[len(allv) // 2], "max": allv[-1]},
            "length_by_grade": per_grade,
            "length_only_1nn": leak,
            "length_only_random": 0.25,
            "grade_token_exposed": exposed,
            "real_documents": real,
            "real_ratio": round(real / len(graded), 3),
            "grade_balance_ratio": round(max(counts) / max(min(counts), 1), 2) if counts else None,
        }

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
            from koipa.modules.m2_preprocess.extractor import extract  # noqa: PLC0415
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

    def _scan(self) -> tuple[tuple, list[str], dict[str, list[str]]]:
        """디렉터리를 **한 번만** 훑어 (캐시키, metadata 목록, doc_id→본문파일) 을 만든다.

        종전에는 후보마다 `glob(f"{doc_id}_*.md")` 를 돌려 2,440개 엔트리 디렉터리를 272번
        재스캔했고(바인드 마운트에서 O(N×M)), 목록 응답에서 버릴 본문 30MB 를 매 요청 읽었다.
        실측: /golden/candidates 와 /summary 가 **120초 타임아웃**.
        """
        metas: list[str] = []
        docs: dict[str, list[str]] = {}
        newest = 0
        count = 0
        with os.scandir(self.root) as it:
            for entry in it:
                if not entry.is_file():
                    continue
                name = entry.name
                count += 1
                try:
                    mtime = entry.stat().st_mtime_ns
                except OSError:
                    mtime = 0
                if mtime > newest:
                    newest = mtime
                if name.endswith(".metadata.json"):
                    metas.append(name)
                elif name.endswith(".md") and "_" in name:
                    docs.setdefault(name.split("_", 1)[0], []).append(name)
        metas.sort()
        ledger_mtime = 0
        try:
            ledger_mtime = self.ledger_path.stat().st_mtime_ns
        except OSError:
            pass
        return (str(self.root), count, newest, ledger_mtime), metas, docs

    def _candidates(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        cache_key, metas, docs_by_id = self._scan()
        cached = _CANDIDATE_CACHE.get(cache_key)
        if cached is not None:
            return cached
        latest = self._latest_decisions()
        rows: list[dict[str, Any]] = []
        for meta_name in metas:
            meta_path = self.root / meta_name
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
                names = docs_by_id.get(doc_id) or []
                if len(names) != 1:
                    continue
                source = self.root / names[0]
            try:
                text = source.read_text(encoding="utf-8")
            except OSError:
                continue
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
        # 캐시키에 (파일수·최신 mtime·원장 mtime) 이 들어 있어 문서 추가·수정·결정 기록이
        # 생기면 자동으로 무효화된다. 폭주를 막기 위해 최근 몇 세대만 유지한다.
        if len(_CANDIDATE_CACHE) > 4:
            _CANDIDATE_CACHE.clear()
        _CANDIDATE_CACHE[cache_key] = rows
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
