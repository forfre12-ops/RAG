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
from lloydk.services.async_classify_service import _celery_dispatch_available
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

# [W6] 학습 편입 금지 마커 — 검수 승인분이라도 이 본문 출처는 학습셋에서 배제한다.
#   noop_fallback = placeholder 본문(실 LLM 없이 파이프라인 연결 테스트), llm_nonjson = 실 LLM이
#   비-JSON 원문을 줘 raw를 body로 쓴 경우. 둘 다 "정상 생성 문서"가 아니므로 학습에 새면 합성
#   노이즈로 작용한다. 정상 생성은 label_source=None(그 외 값도 허용). P0#1에서 이 마커를 검수큐에
#   보존해 둔 목적이 바로 이 하드 위생 게이트다.
TRAINING_EXCLUDED_LABEL_SOURCES = frozenset({"noop_fallback", "llm_nonjson"})


def _is_training_admissible(label_source: str | None) -> bool:
    """검수 승인 합성 샘플이 학습셋에 편입 가능한지(본문 출처 위생) — noop_fallback/llm_nonjson 배제."""
    return label_source not in TRAINING_EXCLUDED_LABEL_SOURCES


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
        # 운영(브로커 가용): 실제 Celery synthesize_batch.delay() 발사 → worker가 생성·검수큐 적재.
        # 테스트/브로커 미가용/eager는 발사하지 않고 작업 등록만(동작·테스트 보존, dryrun 의미).
        if _celery_dispatch_available():
            try:
                from lloydk.workers.tasks import synthesize_batch  # noqa: PLC0415

                synthesize_batch.delay(
                    req.target_grade.value,
                    req.count,
                    domain=req.domain,
                    job_id=str(job_id),
                )
                logger.info("synth enqueued to celery: job_id=%s count=%d", job_id, req.count)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "celery enqueue failed for synth — left as registered: job_id=%s",
                    job_id, exc_info=True,
                )
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
        limit: int = 50,
        offset: int = 0,
    ) -> SynthQueueResponse:
        # tenant 제거: 격리는 KL 포털 전담 — 합성 큐는 전역 네임스페이스.
        logger.debug(
            "synth queue enter: status=%s limit=%d offset=%d",
            status, limit, offset,
        )
        # status 매핑: API 'pending' → DB 'pending_review'
        db_status = {"pending": "pending_review", "approved": "approved", "rejected": "rejected"}.get(status, "pending_review")

        try:
            with session_scope() as db:
                repo = SynthRepo(db)
                # [C1] pending/approved/rejected 모두 조회 지원. 종전엔 승인/반려가 silent
                # empty([],0)로 떨어져 운영자 검수 이력 조회가 무음 함정이었다.
                samples = repo.list_by_status(db_status, limit=limit, offset=offset)
                # total = limit/offset 무관 전체 건수 (페이지네이션 메타)
                total = repo.count_by_status(db_status)
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

    def build_training_rows(self, *, limit: int | None = None) -> dict:
        """[W6] 검수 승인 + 정상 산출 합성 샘플 → 학습 행. generate→queue→review→train 루프 마감.

        사람이 승인한 합성 문서를 학습셋 행({doc_id,text,label,source,domain})으로 변환한다.
        본문 출처 마커로 placeholder(noop_fallback)·비-JSON(llm_nonjson) 산출을 배제해 합성
        노이즈의 학습 유입을 차단한다(하드 위생 게이트 — P0#1이 보존한 label_source의 소비처).
        스냅샷 시맨틱: 매 호출이 승인 전체에서 재빌드(결정적·중복 없음).

        Returns: {"rows":[...], "approved_total":n, "included":n,
                  "excluded_noise":n, "excluded_empty":n} — 무음 드롭 없이 사유별 카운트 노출.
        """
        try:
            with session_scope() as db:
                repo = SynthRepo(db)
                approved_total = repo.count_by_status("approved")

                # 승인분 전량 로드(검수 큐는 사람 처리량에 묶여 규모 작음) — 페이지네이션 방어.
                samples = []
                page, page_size = 0, 1000
                while True:
                    batch = repo.list_by_status(
                        "approved", limit=page_size, offset=page * page_size
                    )
                    if not batch:
                        break
                    samples.extend(batch)
                    if len(batch) < page_size:
                        break
                    page += 1

                # level_id → code 매핑 1회 선적재(행마다 4쿼리 방지).
                crepo = ClassifyRepo(db)
                code_by_level: dict[int, str] = {}
                for code in ("TS", "S1", "S2", "S3"):
                    lid = crepo.level_id_by_code(code)
                    if lid is not None:
                        code_by_level[lid] = code

                rows: list[dict] = []
                excluded_noise = 0
                excluded_empty = 0
                for s in samples:
                    if not _is_training_admissible(s.label_source):
                        excluded_noise += 1  # noop_fallback/llm_nonjson = 학습 편입 금지
                        continue
                    text = (s.generated_content or "").strip()
                    if not text:
                        excluded_empty += 1  # 빈 본문 방어(무음 드롭 금지·카운트 노출)
                        continue
                    rows.append({
                        "doc_id": str(s.sample_id),
                        "text": text,
                        "label": code_by_level.get(s.target_level_id, "S3"),
                        "source": "synthetic",
                        "domain": s.doc_type,
                        "label_source": s.label_source,
                    })
                    if limit is not None and len(rows) >= limit:
                        break

                logger.info(
                    "synth training rows built: approved=%d included=%d "
                    "excluded_noise=%d excluded_empty=%d",
                    approved_total, len(rows), excluded_noise, excluded_empty,
                )
                return {
                    "rows": rows,
                    "approved_total": approved_total,
                    "included": len(rows),
                    "excluded_noise": excluded_noise,
                    "excluded_empty": excluded_empty,
                }
        except SQLAlchemyError as exc:
            logger.warning("build_training_rows skipped: %s", exc)
            return {
                "rows": [],
                "approved_total": 0,
                "included": 0,
                "excluded_noise": 0,
                "excluded_empty": 0,
            }

    @staticmethod
    def _level_id_to_code(db, level_id: int) -> str:
        repo = ClassifyRepo(db)
        for code in ("TS", "S1", "S2", "S3"):
            if repo.level_id_by_code(code) == level_id:
                return code
        return "S3"
