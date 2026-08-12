"""Sample document / prompt version 도메인 — FUN-003 합성 검수 큐."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from koipa.db.models import PromptVersion, SampleDocument


class SynthRepo:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------
    # SampleDocument
    # ------------------------------------------------------------

    def create_sample(
        self,
        *,
        target_level_id: int,
        llm_provider: str,
        llm_model: str,
        generated_content: str,
        doc_type: str | None = None,
        generated_outline: str | None = None,
        quality_score: float | None = None,
        quality_report: dict | None = None,
        outline_prompt_version: str | None = None,
        body_prompt_version: str | None = None,
        qc_prompt_version: str | None = None,
        label_source: str | None = None,
        parse_error: str | None = None,
    ) -> SampleDocument:
        sd = SampleDocument(
            target_level_id=target_level_id,
            llm_provider=llm_provider,
            llm_model=llm_model,
            generated_content=generated_content,
            doc_type=doc_type,
            generated_outline=generated_outline,
            quality_score=quality_score,
            quality_report=quality_report,
            outline_prompt_version=outline_prompt_version,
            body_prompt_version=body_prompt_version,
            qc_prompt_version=qc_prompt_version,
            # [P0#1] 생성기 본문 출처 마커 보존 — 학습 데이터 위생 필터(noop_fallback/llm_nonjson) 근거.
            label_source=label_source,
            parse_error=parse_error,
            review_status="pending_review",
        )
        self.db.add(sd)
        self.db.flush()
        return sd

    # 검수 큐 상태 — DB review_status 값. (API 'pending'은 'pending_review'로 매핑된다.)
    VALID_REVIEW_STATUSES = ("pending_review", "approved", "rejected")

    def list_by_status(
        self, status: str, *, limit: int = 50, offset: int = 0
    ) -> list[SampleDocument]:
        """review_status별 합성 샘플 조회. pending/approved/rejected 모두 지원.

        tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진, 전역 조회).
        """
        stmt = select(SampleDocument).where(SampleDocument.review_status == status)
        return list(
            self.db.execute(
                stmt.order_by(SampleDocument.created_at).limit(limit).offset(offset)
            ).scalars()
        )

    def count_by_status(self, status: str) -> int:
        """페이지네이션 total — limit/offset과 무관한 상태별 전체 건수."""
        stmt = select(func.count()).select_from(SampleDocument).where(
            SampleDocument.review_status == status
        )
        return int(self.db.execute(stmt).scalar_one())

    # 하위호환 — 기존 호출부/테스트 보존. 내부적으로 status 일반화 메서드에 위임.
    def list_pending_review(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[SampleDocument]:
        return self.list_by_status("pending_review", limit=limit, offset=offset)

    def count_pending_review(self) -> int:
        return self.count_by_status("pending_review")

    def review(
        self,
        sample_id: uuid.UUID,
        *,
        approved: bool,
        reviewed_by: str,
        rejection_reason: str | None = None,
        promoted_doc_id: uuid.UUID | None = None,
    ) -> SampleDocument | None:
        sd = self.db.get(SampleDocument, sample_id)
        if sd is None:
            return None
        sd.review_status = "approved" if approved else "rejected"
        sd.reviewed_by = reviewed_by
        sd.reviewed_at = dt.datetime.now(dt.timezone.utc)
        if not approved:
            sd.rejection_reason = rejection_reason
        if approved and promoted_doc_id is not None:
            sd.doc_id = promoted_doc_id
        return sd

    def get(self, sample_id: uuid.UUID) -> SampleDocument | None:
        return self.db.get(SampleDocument, sample_id)

    # ------------------------------------------------------------
    # PromptVersion
    # ------------------------------------------------------------

    def upsert_prompt(
        self,
        version: str,
        *,
        chain_stage: str,
        template: str,
        created_by: str | None = None,
        notes: str | None = None,
    ) -> PromptVersion:
        existing = self.db.get(PromptVersion, version)
        if existing is not None:
            existing.template = template
            existing.notes = notes
            return existing
        pv = PromptVersion(
            prompt_version=version,
            chain_stage=chain_stage,
            template=template,
            created_by=created_by,
            notes=notes,
        )
        self.db.add(pv)
        self.db.flush()
        return pv

    def get_prompt(self, version: str) -> PromptVersion | None:
        return self.db.get(PromptVersion, version)
