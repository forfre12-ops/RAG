"""/classify/async + /classify/batch + /classify/jobs/{id} + /classify/{doc_id} (최근 결과)."""

# future annotations 비활성: slowapi limiter가 함수 시그니처 forward-ref 평가에서 fail.

import time
import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from koipa.api._jwt_auth import require_auth
from koipa.api.rate_limit import limiter
from koipa.db import session_scope
from koipa.repositories import ClassifyRepo
from koipa.schemas.classify import StoredClassificationResponse, kl_wire_projection
from koipa.schemas.classify_async import (
    ClassifyAsyncRequest,
    ClassifyAsyncResponse,
    ClassifyBatchRequest,
    ClassifyBatchResponse,
    ClassifyJobStatus,
)
from koipa.services.async_classify_service import AsyncClassifyService
from koipa.services.regulation_evidence_service import (
    regulation_reference_for_kl_wire,
    regulation_summary_for_kl_wire,
)

router = APIRouter(tags=["classify"], dependencies=[Depends(require_auth)])


def _regulation_kl_wire_budget_deadline(n_results: int) -> float | None:
    """배치 조회(GET /classify/jobs/{job_id})가 결과 수만큼 규정참고·요약 LLM 호출을 **순차로**
    부르다 무한정 늘어지지 않게 전체 예산을 둔다(2026-10-02, regulation_summary 추가로 문서당
    최대 지연이 거의 2배가 되면서 발견 — 배치 1,000건이면 이론상 이 GET 하나가 몇 시간 걸릴 수
    있었다). `regulation_evidence_service._find_many`(미리보기용 예산)와 같은 수치·같은 판단
    기준을 쓴다. 결과가 1건이면 예산을 두지 않는다(원래도 그 1건분 지연뿐이다).
    """
    from koipa.config import settings  # noqa: PLC0415

    if n_results <= 1:
        return None
    if not (getattr(settings, "regulation_llm_select_enabled", False)
            or getattr(settings, "regulation_llm_summary_enabled", False)):
        return None
    timeout_s = float(getattr(settings, "regulation_llm_timeout_s", 60.0))
    return time.monotonic() + 2.0 * timeout_s


@router.post("/classify/async", response_model=ClassifyAsyncResponse, status_code=202)
@limiter.limit("60/minute")
def classify_async(request: Request, req: ClassifyAsyncRequest):
    # tenant 제거: 격리는 KL 포털 전담 → 무스코프 제출.
    return AsyncClassifyService().submit_async(req)


@router.post("/classify/batch", response_model=ClassifyBatchResponse, status_code=202)
@limiter.limit("60/minute")
def classify_batch(request: Request, req: ClassifyBatchRequest):
    # M1 경계값 가드: 빈 배열은 의미 없는 요청 → 400, 1000건 초과는 페이로드 거부 → 413.
    if len(req.documents) == 0:
        raise HTTPException(status_code=400, detail="documents must not be empty")
    if len(req.documents) > 1000:
        raise HTTPException(status_code=413, detail="batch size > 1000")
    # tenant 제거: 격리는 KL 포털 전담 → 무스코프 배치 제출.
    return AsyncClassifyService().submit_batch(req)


@router.get("/classify/jobs/{job_id}", response_model=ClassifyJobStatus)
def classify_job_status(job_id: UUID, request: Request):
    # tenant 제거: 격리는 KL 포털 전담(인증된 KL만 접근) → job_id로 무스코프 조회.
    res = AsyncClassifyService().get_status(job_id)
    if res is None:
        raise HTTPException(status_code=404, detail="job not found")
    # [2026-09-29] kl_backend 역할로 부르면(=KL 이 실제로 받는 호출) 일부 진단 필드를 비우고
    # 규정참고(+요약, 2026-10-02)를 채운 사본으로 좁힌다. 그 외(admin·reviewer·system, 우리
    # 콘솔의 대용량 문서 테스트 화면 포함)는 지금까지와 동일하게 전체를 그대로 돌려준다 —
    # 같은 IF-05 엔드포인트를 내부에서도 재사용하고 있어(app.js:887) 여기를 바꾸면 그 화면이 깨진다.
    if getattr(request.state, "auth_role", None) == "kl_backend" and res.results:
        projected = []
        deadline = _regulation_kl_wire_budget_deadline(len(res.results))
        over_budget = False
        for r in res.results:
            if deadline is not None and (over_budget or time.monotonic() > deadline):
                # 예산 초과 — 남은 문서는 규정참고·요약 없이(둘 다 None) 그대로 내보낸다.
                # label·confidence 등 핵심 판정은 멀쩡하다, 참고용 부가정보만 생략될 뿐이다.
                over_budget = True
                projected.append(kl_wire_projection(r))
                continue
            reference = regulation_reference_for_kl_wire(r.doc_id)
            projected.append(kl_wire_projection(
                r, regulation_reference=reference,
                regulation_summary=regulation_summary_for_kl_wire(r.doc_id, reference),
            ))
        res.results = projected
    return res


@router.get("/classify/{doc_id}", response_model=StoredClassificationResponse)
def classify_recent_for_doc(doc_id: str):
    """doc_id의 최근 분류 결과 1건 (DB 진실 소스)."""
    try:
        doc_uuid = uuid.UUID(doc_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="doc_id must be a UUID") from exc
    # tenant 제거: 격리는 KL 포털 전담 → 무스코프 조회.
    try:
        with session_scope() as db:
            repo = ClassifyRepo(db)
            recent = repo.list_recent_for_doc(doc_uuid, limit=1)
            if not recent:
                raise HTTPException(status_code=404, detail="no classification for doc_id")
            cls = recent[0]
            # ClassificationLevel.level_code 조회
            from koipa.db.models import ClassificationLevel  # noqa: PLC0415
            lvl = db.get(ClassificationLevel, cls.predicted_level_id)
            label = lvl.level_code if lvl else "S3"
            scores: dict[str, float] = {label: float(cls.confidence)}
            for alt in cls.alternatives or []:
                code = alt.get("level_code")
                if code:
                    scores[code] = float(alt.get("confidence", 0.0))
            # [KL 연동] 확정 등급 — 사람이 확정했으면 교정 기록에서 읽어 함께 내려 준다.
            # label 은 예측 그대로 둔다(감사 증적 보존). 교정이 없으면 세 필드 모두 None 이라
            # 기존 응답과 동일하다.
            confirmed_label = confirmed_by = confirmed_at = None
            corr = repo.latest_correction_for_doc(doc_uuid)
            if corr is not None:
                clvl = db.get(ClassificationLevel, corr.corrected_level_id)
                confirmed_label = clvl.level_code if clvl else None
                confirmed_by = corr.corrected_by
                confirmed_at = corr.corrected_at.isoformat() if corr.corrected_at else None

            return StoredClassificationResponse(
                inference_id=cls.classification_id,
                doc_id=doc_id,
                label=label,
                confidence=float(cls.confidence),
                scores=scores,
                model_version=cls.model_version,
                status=cls.status,
                confirmed_label=confirmed_label,
                confirmed_by=confirmed_by,
                confirmed_at=confirmed_at,
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        # J2: 클라이언트에 내부 예외 메시지 노출하지 않음
        import logging as _logging
        _logging.getLogger(__name__).error("db unavailable: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail="service temporarily unavailable") from exc
