"""Sample document / prompt version 도메인 — FUN-003 합성 검수 큐."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lloydk.db.models import PromptVersion, SampleDocument


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
            review_status="pending_review",
        )
        self.db.add(sd)
        self.db.flush()
        return sd

    def list_pending_review(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[SampleDocument]:
        # tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진, 전역 조회).
        stmt = select(SampleDocument).where(SampleDocument.review_status == "pending_review")
        return list(
            self.db.execute(
                stmt.order_by(SampleDocument.created_at).limit(limit).offset(offset)
            ).scalars()
        )

    def count_pending_review(self) -> int:
        """페이지네이션 total — limit/offset과 무관한 전체 대기 건수."""
        # tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진, 전역 조회).
        stmt = select(func.count()).select_from(SampleDocument).where(
            SampleDocument.review_status == "pending_review"
        )
        return int(self.db.execute(stmt).scalar_one())

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
