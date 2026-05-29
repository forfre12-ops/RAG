"""Classification + ClassificationEvidence + Correction CRUD.

ClassifyService가 사용. 모든 메서드는 외부 Session을 주입받고
트랜잭션 commit은 호출자가 책임집니다.
"""

from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from lloydk.db.models import (
    Classification,
    ClassificationEvidence,
    ClassificationLevel,
    Correction,
    Document,
    Tenant,
)
from lloydk.schemas.classify import EvidenceSpan, RagContextHit
from lloydk.schemas.common import Grade


def _try_uuid(value: str | None) -> uuid.UUID | None:
    """문자열 → UUID. 실패하면 None (DB 컬럼이 nullable)."""
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        return None


class ClassifyRepo:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------

    def tenant_exists(self, tenant_id: str) -> bool:
        return self.db.get(Tenant, tenant_id) is not None

    def document_exists(self, doc_id: uuid.UUID) -> bool:
        return self.db.get(Document, doc_id) is not None

    def level_id_by_code(self, code: Grade | str) -> int | None:
        """Grade 열거형 또는 코드 문자열을 level_id로 변환."""
        code_str = code.value if isinstance(code, Grade) else code
        row = self.db.execute(
            select(ClassificationLevel.level_id).where(
                ClassificationLevel.level_code == code_str
            )
        ).first()
        return row[0] if row else None

    # ------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------

    def create_classification(
        self,
        *,
        doc_id: uuid.UUID,
        tenant_id: str,
        model_version: str,
        predicted_level_id: int,
        confidence: float,
        alternatives: list[dict],
        chunk_count: int | None = None,
        rag_used: bool = False,
        rag_top_k: int | None = None,
        aggregation_method: str = "hybrid",
        status: str = "staging",
        inference_ms: int | None = None,
    ) -> Classification:
        cls = Classification(
            doc_id=doc_id,
            tenant_id=tenant_id,
            model_version=model_version,
            predicted_level_id=predicted_level_id,
            confidence=confidence,
            alternatives=alternatives,
            chunk_count=chunk_count,
            rag_used=rag_used,
            rag_top_k=rag_top_k,
            aggregation_method=aggregation_method,
            status=status,
            inference_ms=inference_ms,
        )
        self.db.add(cls)
        self.db.flush()
        return cls

    def add_evidence(
        self,
        classification_id: uuid.UUID,
        *,
        chunk_id: uuid.UUID,
        evidence_type: str,
        excerpt: str,
        contribution: float,
        excerpt_start: int | None = None,
        excerpt_end: int | None = None,
        factor_id: int | None = None,
        rag_ref_doc_id: uuid.UUID | None = None,
        rag_similarity: float | None = None,
    ) -> ClassificationEvidence:
        """rag_ref_doc_id/rag_similarity는 evidence_type='rag_context'에서 사용."""
        ev = ClassificationEvidence(
            classification_id=classification_id,
            chunk_id=chunk_id,
            evidence_type=evidence_type,
            excerpt=excerpt,
            contribution=contribution,
            excerpt_start=excerpt_start,
            excerpt_end=excerpt_end,
            factor_id=factor_id,
            rag_ref_doc_id=rag_ref_doc_id,
            rag_similarity=rag_similarity,
        )
        self.db.add(ev)
        return ev

    def add_evidence_from_spans(
        self,
        classification_id: uuid.UUID,
        *,
        spans: Iterable[EvidenceSpan],
        default_chunk_id: uuid.UUID,
    ) -> int:
        """EvidenceSpan(스키마) → ClassificationEvidence(DB) 변환 후 일괄 insert.

        InferencePipeline 결과의 evidence_spans를 그대로 받기 위한 헬퍼.
        chunks 테이블은 파티션이라 chunk_id FK가 없음 — 무결성은 app 레이어 책임.

        §5 (2026-05-29): N+1 라운드트립 제거 — 단건 add_evidence 호출 대신
        ORM 객체 리스트를 한 번에 add_all. 분류당 evidence 7~11개 환경에서
        DB 라운드트립 50ms→10ms 추정. add_all 미구현 stub session에서는 단건
        폴백으로 호환성 유지.
        """
        objs: list[ClassificationEvidence] = []
        for span in spans:
            objs.append(ClassificationEvidence(
                classification_id=classification_id,
                chunk_id=default_chunk_id,
                evidence_type=span.tag or "keyword_match",
                excerpt=span.text,
                contribution=float(span.weight) if span.weight else 0.0,
                excerpt_start=span.start,
                excerpt_end=span.end,
            ))
        self._bulk_or_add(objs)
        return len(objs)

    def add_rag_evidence_from_hits(
        self,
        classification_id: uuid.UUID,
        *,
        hits: Iterable[RagContextHit],
        default_chunk_id: uuid.UUID,
        excerpt_max_len: int = 500,
    ) -> int:
        """RagContextHit(스키마) → ClassificationEvidence(DB)로 RAG 검색 결과를 영속화.

        - evidence_type='rag_context' 고정. RAG 출처 식별 + 검수자가 인용 정확도 검증 가능.
        - source_doc·chunk_id 문자열을 UUID로 파싱(실패 시 None — DB는 nullable이라 OK).
        - excerpt는 RagContextHit가 본문을 별도로 안 들고 있어 source_doc 식별자를 보존만 함.
          본문이 필요하면 호출자가 별도 lookup하여 excerpt 갱신.
        - contribution은 hit.score를 [0,1]로 clamp.

        §5: 동일하게 add_all로 묶음 insert. add_all 미구현 stub session에서는
        단건 폴백.
        """
        objs: list[ClassificationEvidence] = []
        for hit in hits:
            ref_doc = _try_uuid(hit.source_doc)
            chunk = _try_uuid(hit.chunk_id) or default_chunk_id
            score = max(0.0, min(1.0, float(hit.score)))
            excerpt = f"[rag] doc={hit.source_doc} chunk={hit.chunk_id}"[:excerpt_max_len]
            objs.append(ClassificationEvidence(
                classification_id=classification_id,
                chunk_id=chunk,
                evidence_type="rag_context",
                excerpt=excerpt,
                contribution=score,
                rag_ref_doc_id=ref_doc,
                rag_similarity=score,
            ))
        self._bulk_or_add(objs)
        return len(objs)

    def _bulk_or_add(self, objs: list) -> None:
        """§5 helper: add_all 우선, 미구현 시 단건 add 루프 폴백."""
        if not objs:
            return
        add_all = getattr(self.db, "add_all", None)
        if callable(add_all):
            add_all(objs)
            return
        for o in objs:
            self.db.add(o)

    def list_recent_for_doc(
        self, doc_id: uuid.UUID, *, limit: int = 10
    ) -> list[Classification]:
        return list(
            self.db.execute(
                select(Classification)
                .where(Classification.doc_id == doc_id)
                .order_by(Classification.classified_at.desc())
                .limit(limit)
            ).scalars()
        )

    def get(self, classification_id: uuid.UUID) -> Classification | None:
        return self.db.get(Classification, classification_id)

    def update_status(self, classification_id: uuid.UUID, status: str) -> None:
        cls = self.db.get(Classification, classification_id)
        if cls is not None:
            cls.status = status

    # ------------------------------------------------------------
    # Correction (Active Learning truth source)
    # ------------------------------------------------------------

    def add_correction(
        self,
        *,
        classification_id: uuid.UUID,
        original_level_id: int,
        corrected_level_id: int,
        corrected_by: str,
        reason: str | None = None,
    ) -> Correction:
        """direction은 등급 order 비교로 자동 결정."""
        levels = {
            lv.level_id: lv.level_order
            for lv in self.db.execute(select(ClassificationLevel)).scalars()
        }
        orig_order = levels.get(original_level_id)
        new_order = levels.get(corrected_level_id)
        direction = self._direction(orig_order, new_order)

        corr = Correction(
            classification_id=classification_id,
            original_level_id=original_level_id,
            corrected_level_id=corrected_level_id,
            direction=direction,
            reason=reason,
            corrected_by=corrected_by,
        )
        self.db.add(corr)
        self.db.flush()
        return corr

    @staticmethod
    def _direction(orig_order: int | None, new_order: int | None) -> str:
        """등급 order 비교 → direction.

        등급 order: 낮을수록 높은 등급 (TS=1, S3=4).
        - original=S1(2), corrected=TS(1): new < orig ⇒ 보안 미탐(고등급으로 격상) = underclass
        - original=TS(1), corrected=S2(3): new > orig ⇒ 과탐 = overclass
        - 같음 = confirm
        """
        if orig_order is None or new_order is None:
            return "lateral"
        if new_order < orig_order:
            return "underclass"
        if new_order > orig_order:
            return "overclass"
        return "confirm"

    def unconsumed_corrections(self) -> list[Correction]:
        return list(
            self.db.execute(
                select(Correction).where(Correction.consumed_in_run.is_(None))
            ).scalars()
        )

    def mark_corrections_consumed(
        self, run_id: uuid.UUID, correction_ids: Iterable[int]
    ) -> int:
        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc)
        count = 0
        for cid in correction_ids:
            corr = self.db.get(Correction, cid)
            if corr is None:
                continue
            corr.consumed_in_run = run_id
            corr.consumed_at = now
            count += 1
        return count
