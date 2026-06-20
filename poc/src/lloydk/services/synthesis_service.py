"""Synthesis service — 합성 생성 큐 + 검수 워크플로우.

PoC: /synth/generate는 작업 등록만 (실 생성은 Celery synthesize_batch 또는 dryrun mode).
/synth/queue·/synth/{id}/review는 SampleDocument(DB) 진실 소스.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Optional

from sqlalchemy.exc import SQLAlchemyError

from lloydk.db import session_scope
from lloydk.repositories import ClassifyRepo, SynthRepo
from lloydk.schemas.synthesis import (
    SynthGenerateRequest,
    SynthGenerateResponse,
    SyntheticDocItem,
    SynthQueueResponse,
    SynthReviewRequest,
    SynthReviewResponse,
)
from lloydk.services.job_store import get_default_store

logger = logging.getLogger(__name__)

# 추정 비용 (development phase, USD/문서) — Claude Sonnet 4.6 기준
COST_PER_DOC_USD = {
    "anthropic": 0.012,
    "openai": 0.010,
    "google": 0.008,
    "vllm_qwen": 0.0,    # 자체호스팅
    "vllm_exaone": 0.0,
    "noop": 0.0,
}


class SynthesisService:
    def __init__(self):
        self.jobs = get_default_store()

    def submit(self, req: SynthGenerateRequest) -> SynthGenerateResponse:
        logger.debug(
            "synth submit enter: target_grade=%s domain=%s count=%d provider=%s actor=%s",
            req.target_grade, req.domain, req.count, req.llm_provider, req.actor.user_id,
        )
        job_id = uuid.uuid4()
        unit = COST_PER_DOC_USD.get(req.llm_provider, 0.01)
        self.jobs.create(
            job_id,
            payload={
                "target_grade": req.target_grade.value,
                "domain": req.domain,
                "count": req.count,
                "llm_provider": req.llm_provider,
                "submitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "actor": req.actor.user_id,
            },
        )
        # 운영: Celery synthesize_batch.delay(grade=..., count=...) 발사
        logger.info(
            "synth submit done: job_id=%s count=%d est_cost_usd=%.4f",
            job_id, req.count, round(unit * req.count, 4),
        )
        return SynthGenerateResponse(
            synth_job_id=job_id,
            expected_count=req.count,
            estimated_cost_usd=round(unit * req.count, 4),
        )

    def queue(
        self,
        *,
        status: str = "pending",
        tenant_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> SynthQueueResponse:
        logger.debug(
            "synth queue enter: status=%s tenant=%s limit=%d offset=%d",
            status, tenant_id, limit, offset,
        )
        # status 매핑: API 'pending' → DB 'pending_review'
        db_status = {"pending": "pending_review", "approved": "approved", "rejected": "rejected"}.get(status, "pending_review")

        try:
            with session_scope() as db:
                repo = SynthRepo(db)
                if db_status == "pending_review":
                    samples = repo.list_pending_review(tenant_id=tenant_id, limit=limit, offset=offset)
                    # total = limit/offset 무관 전체 건수 (페이지네이션 메타)
                    total = repo.count_pending_review(tenant_id=tenant_id)
                else:
                    # 승인/반려 조회는 PoC 미구현 — pending만 페이지네이션 지원
                    samples, total = [], 0
                items = [
                    SyntheticDocItem(
                        synth_id=s.sample_id,
                        target_grade=self._level_id_to_code(db, s.target_level_id),
                        domain=s.doc_type,
                        llm_provider=s.llm_provider,
                        llm_model=s.llm_model,
                        quality_score=float(s.quality_score) if s.quality_score is not None else None,
                        review_status=s.review_status,
                        preview=(s.generated_content or "")[:2000],
                        created_at=s.created_at.isoformat() if s.created_at else None,
                    )
                    for s in samples
                ]
                return SynthQueueResponse(total=total, items=items)
        except SQLAlchemyError as exc:
            logger.warning("synth queue skipped: %s", exc)
            return SynthQueueResponse(total=0, items=[])

    def review(self, synth_id: uuid.UUID, req: SynthReviewRequest) -> Optional[SynthReviewResponse]:
        logger.debug(
            "synth review enter: synth_id=%s decision=%s actor=%s",
            synth_id, req.decision, req.actor.user_id,
        )
        try:
            with session_scope() as db:
                repo = SynthRepo(db)
                approved = req.decision == "approve"
                sd = repo.review(
                    synth_id,
                    approved=approved,
                    reviewed_by=req.actor.user_id,
                    rejection_reason=req.comment if not approved else None,
                )
                if sd is None:
                    logger.info("synth review: sample not found — synth_id=%s", synth_id)
                    return None
                logger.info(
                    "synth review done: synth_id=%s final_status=%s",
                    synth_id, sd.review_status,
                )
                return SynthReviewResponse(
                    synth_id=synth_id,
                    final_status=sd.review_status,
                    added_to_dataset_version=None,  # W6 학습 데이터셋 빌드 시 연결
                )
        except SQLAlchemyError as exc:
            logger.warning("synth review skipped: synth_id=%s err=%s", synth_id, exc)
            return None

    @staticmethod
    def _level_id_to_code(db, level_id: int) -> str:
        repo = ClassifyRepo(db)
        for code in ("TS", "S1", "S2", "S3"):
            if repo.level_id_by_code(code) == level_id:
                return code
        return "S3"
