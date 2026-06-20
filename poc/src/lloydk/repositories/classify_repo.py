"""Classification + ClassificationEvidence + Correction CRUD.

ClassifyService가 사용. 모든 메서드는 외부 Session을 주입받고
트랜잭션 commit은 호출자가 책임집니다.
"""

from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from lloydk.db.models import (
    Classification,
    ClassificationEvidence,
    ClassificationLevel,
    Correction,
    Document,
    DocumentLabel,
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

    def document_exists(self, doc_id: uuid.UUID, tenant_id: str | None = None) -> bool:
        """문서 존재 확인.

        보안: tenant_id가 주어지면 그 tenant 소유일 때만 True(객체 수준 권한).
        교차테넌트 doc_id로 분류가 엉뚱한 tenant 밑에 영속화(orphan)되는 것을 차단.
        tenant_id=None은 하위호환(스코프 미적용).
        """
        doc = self.db.get(Document, doc_id)
        if doc is None:
            return False
        if tenant_id is not None and doc.tenant_id != tenant_id:
            return False
        return True

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
        self,
        doc_id: uuid.UUID,
        *,
        tenant_id: str | None = None,
        limit: int = 10,
        for_update: bool = False,
    ) -> list[Classification]:
        """doc_id의 최근 분류 결과.

        보안: tenant_id가 주어지면 그 tenant의 분류만 반환(교차테넌트 결과 열람 차단).
        tenant_id=None은 하위호환(스코프 미적용).

        for_update=True면 반환 행을 잠근다(동시 검수 확정 직렬화). doc_id 경로의
        confirm/relabel이 최신 분류 1건을 안전하게 갱신하도록 쓴다.
        """
        stmt = select(Classification).where(Classification.doc_id == doc_id)
        if tenant_id is not None:
            stmt = stmt.where(Classification.tenant_id == tenant_id)
        stmt = stmt.order_by(Classification.classified_at.desc()).limit(limit)
        if for_update:
            stmt = stmt.with_for_update()
        return list(self.db.execute(stmt).scalars())

    def get(
        self, classification_id: uuid.UUID, *, for_update: bool = False
    ) -> Classification | None:
        """classification 단건 조회.

        for_update=True면 행 잠금(SELECT ... FOR UPDATE)으로 조회한다 — 동시 검수
        확정(confirm/relabel)이 같은 분류를 건드릴 때 트랜잭션을 직렬화해 status
        race·중복 correction을 막는다. SELECT FOR UPDATE를 지원하지 않는 백엔드
        (SQLite 테스트)에서는 SQLAlchemy가 잠금 절을 안전하게 생략한다.
        """
        if for_update:
            stmt = (
                select(Classification)
                .where(Classification.classification_id == classification_id)
                .with_for_update()
            )
            return self.db.execute(stmt).scalar_one_or_none()
        return self.db.get(Classification, classification_id)

    def update_status(self, classification_id: uuid.UUID, status: str) -> None:
        cls = self.db.get(Classification, classification_id)
        if cls is not None:
            cls.status = status

    # ------------------------------------------------------------
    # DocumentLabel (Human Review / Verified Gold)
    # ------------------------------------------------------------

    def get_verified_document_label(
        self, doc_id: uuid.UUID, *, tenant_id: str | None = None
    ) -> DocumentLabel | None:
        """doc_id에 대한 검증된 라벨 조회 (is_verified=True 우선).

        human_review → llm_judge_consensus → llm_judge_primary 우선순위로 반환.
        없으면 None → 모델 추론으로 계속 진행.

        보안: tenant_id가 주어지면 그 tenant 소유 문서의 라벨만 반환(Document 조인).
        DocumentLabel은 자체 tenant_id가 없으므로 documents.tenant_id로 스코프한다.
        교차테넌트 검증 등급(human_review 결과) 유출 차단. tenant_id=None은 하위호환.
        """
        _LABEL_SOURCE_PRIORITY = {
            "human_review": 1,
            "koipa_case_based": 2,
            "nkt_designated": 2,
            "codex_review": 3,
            "public_definitive": 4,
            "llm_judge_consensus": 5,
            "llm_judge_primary": 6,
        }

        verified_cond = (
            DocumentLabel.review_status == "accepted"
            if hasattr(DocumentLabel, "review_status")
            else DocumentLabel.is_verified.is_(True)
        )
        stmt = select(DocumentLabel).where(DocumentLabel.doc_id == doc_id).where(verified_cond)
        if tenant_id is not None:
            stmt = stmt.join(Document, DocumentLabel.doc_id == Document.doc_id).where(
                Document.tenant_id == tenant_id
            )
        rows = list(self.db.execute(stmt).scalars())
        if not rows:
            return None

        # 우선순위 정렬 후 최우선 반환
        rows.sort(key=lambda r: _LABEL_SOURCE_PRIORITY.get(r.labeled_by, 99))
        return rows[0]

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
        # #27(풀스캔 제거): ClassificationLevel 전체를 dict로 적재하지 않고
        # direction 산출에 필요한 두 level_id(원본·교정)만 WHERE level_id IN (...)으로
        # 조회한다. 등급 수가 늘어도(다중 등급체계) 조회 비용이 일정하게 유지된다.
        # original==corrected(confirm) 같은 경우 IN 집합이 1건으로 줄어드는 것도 OK.
        wanted = {original_level_id, corrected_level_id}
        levels = {
            level_id: level_order
            for level_id, level_order in self.db.execute(
                select(
                    ClassificationLevel.level_id, ClassificationLevel.level_order
                ).where(ClassificationLevel.level_id.in_(wanted))
            ).all()
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

    def correction_exists(
        self,
        classification_id: uuid.UUID,
        corrected_level_id: int,
        corrected_by: str,
    ) -> bool:
        """동일 (classification, 교정등급, 교정자) correction 존재 여부 — 멱등성 가드.

        같은 검수자가 같은 분류를 같은 등급으로 두 번 확정/정정해도(중복 클릭·재시도)
        correction이 2건 쌓이지 않게 한다. 행 잠금(get for_update)과 함께 쓰면 동시
        확정에서도 단일 correction만 남는다.
        """
        row = self.db.execute(
            select(Correction.correction_id)
            .where(
                Correction.classification_id == classification_id,
                Correction.corrected_level_id == corrected_level_id,
                Correction.corrected_by == corrected_by,
            )
            .limit(1)
        ).first()
        return row is not None

    def distinct_reviewers_for_level(
        self, classification_id: uuid.UUID, corrected_level_id: int
    ) -> set[str]:
        """이 분류를 해당 등급으로 교정한 **서로 다른 검수자** 집합 (C-cons 2인검토용).

        고등급 변경의 합의(2인 이상)를 판정하는 근거. 같은 검수자가 여러 번 교정해도 1명으로 센다.
        """
        rows = self.db.execute(
            select(Correction.corrected_by)
            .where(
                Correction.classification_id == classification_id,
                Correction.corrected_level_id == corrected_level_id,
            )
            .distinct()
        ).scalars()
        return {r for r in rows if r}

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

    def count_recent_corrections(self, actor_id: str | None = None, limit_days: int = 1) -> int:
        """최근 N일간 corrections 건수 — PSH S3 KPI용."""
        import datetime as _dt
        cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=limit_days)
        q = select(func.count()).select_from(Correction).where(Correction.corrected_at >= cutoff)
        if actor_id:
            q = q.where(Correction.corrected_by == actor_id)
        return self.db.scalar(q) or 0

    def mark_corrections_consumed(
        self, run_id: uuid.UUID, correction_ids: Iterable[int]
    ) -> int:
        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc)
        # #27(N+1 제거): id별 db.get→객체 갱신 루프를 단일 bulk UPDATE 1쿼리로 대체.
        # 반환값 의미(실제 갱신된 correction 건수)는 result.rowcount로 보존한다.
        # 빈 id 집합이면 IN () 빈 UPDATE를 피하고 0 반환.
        ids = list(correction_ids)
        if not ids:
            return 0
        # synchronize_session="fetch"로 세션 동기화를 요청하되, 일부 백엔드/버전
        # (예: SQLite + SA 2.0.x)에서 identity map에 이미 로드된 인스턴스가
        # 즉시 만료되지 않는 경우가 있어, 갱신 대상 중 세션에 로드된 객체는
        # 명시적으로 expire하여 후속 db.get이 신선한 consumed_* 값을 보게 한다.
        # (SQLite/PG 양쪽에서 동작 — 기존 루프 방식의 "갱신 반영" 의미 보존.)
        result = self.db.execute(
            update(Correction)
            .where(Correction.correction_id.in_(ids))
            .values(consumed_in_run=run_id, consumed_at=now)
            .execution_options(synchronize_session="fetch")
        )
        id_set = set(ids)
        for corr in list(self.db.identity_map.values()):
            if isinstance(corr, Correction) and corr.correction_id in id_set:
                self.db.expire(corr)
        return result.rowcount or 0
