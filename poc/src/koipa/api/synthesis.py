"""POST /synth/generate + GET /synth/queue + GET /synth/coverage + POST /synth/{id}/review."""

# future annotations 비활성: slowapi/pydantic forward-ref 평가에서 fail.

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from koipa.api._jwt_auth import require_auth
from koipa.api._rbac import require_role
from koipa.api.confirm import bind_authenticated_actor
from koipa.api.rate_limit import limiter
from koipa.schemas.synthesis import (
    SynthCoverageResponse,
    SynthGenerateRequest,
    SynthGenerateResponse,
    SynthJobStatus,
    SynthPlaybookResponse,
    SynthPlaybookUpdateRequest,
    SynthProviderCredentialRequest,
    SynthProviderCredentialStatus,
    SynthProvidersStatusResponse,
    SyntheticDocDetail,
    SynthQueueResponse,
    SynthReviewRequest,
    SynthReviewResponse,
    SynthTrainingCandidateResponse,
)
from koipa.services import llm_credential_store
from koipa.services.synth_coverage import DEFAULT_MIN_PER_CELL, coverage_report
from koipa.services.synth_provider_status import provider_status
from koipa.services.synthesis_service import SynthesisDispatchUnavailable, SynthesisService

router = APIRouter(tags=["synthesis"], dependencies=[Depends(require_auth)])


# 합성 데이터 생성은 학습셋에 편입되는 변경성 작업 → 시스템/관리 역할로 제한
# (train과 동일 정책). 검수(review)는 reviewer 권한. 조회(queue)는 broad.
@router.post(
    "/synth/generate",
    response_model=SynthGenerateResponse,
    status_code=202,
    responses={503: {"description": "생성 제공자 또는 워커가 준비되지 않아 접수되지 않음"}},
)
@limiter.limit("10/minute")
def synth_generate(
    request: Request,
    req: SynthGenerateRequest,
    auth: dict = Depends(require_role("admin", "kl_backend", "system")),
):
    # [#13] created_by 감사 신원 = 인증 principal(body 자칭 위조 차단; JWT sub 우선).
    bind_authenticated_actor(req.actor, auth)
    try:
        return SynthesisService().submit(req)
    except SynthesisDispatchUnavailable as exc:
        # 등록만 하고 "성공"으로 보이는 길을 닫는다. 호출자는 실제 검토 후보가 하나도
        # 생성되지 않았다는 사실과 고칠 대상을 즉시 알 수 있다.
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/synth/queue", response_model=SynthQueueResponse)
def synth_queue(
    status: str = Query(default="pending", pattern=r"^(pending|approved|rejected)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return SynthesisService().queue(status=status, limit=limit, offset=offset)


@router.get(
    "/synth/training-candidate",
    response_model=SynthTrainingCandidateResponse,
)
def synth_training_candidate():
    """Return whether approved synthetic documents may be included in a new train job."""
    _, candidate = SynthesisService().training_candidate()
    return candidate


@router.get("/synth/coverage", response_model=SynthCoverageResponse)
def synth_coverage(
    min_per_cell: int = Query(
        default=DEFAULT_MIN_PER_CELL, ge=1, le=1000,
        description="이 수 미만인 칸을 '얇은 칸'으로 본다",
    ),
):
    """합성으로 채울 자리 — 등급 × 도메인 격자.

    이 경로는 데이터셋 구성과 개발용 균형 진단을 위한 읽기 전용 정보다. 운영 콘솔은
    train·val·test 합계와 출처를 설명하는 데만 쓰고, 외부 산업 카테고리 격자를 생성
    주제 선택지로 강요하지 않는다.

    합성의 쓸모를 '빈 칸 채우기'로 좁히는 근거는 실측이다 — 합성-only 로 학습해 실문서를
    재면 F1 0.26 이라 양을 늘려도 실문서 성능이 그만큼 오르지 않는다. 남는 쓸모는
    실데이터가 구조적으로 못 주는 칸(지금은 전부 고등급·산업 도메인)이다.

    ⚠ 응답은 **후보**다. 빈 칸이라고 다 채울 것은 아니며(현실에 없는 조합이 있다) 판단은
    사람이 한다 — caveat 를 화면에 그대로 띄운다.
    """
    return coverage_report(min_per_cell=min_per_cell)


@router.get("/synth/providers/status", response_model=SynthProvidersStatusResponse)
def synth_providers_status():
    """제공자별 가용성 — 콘솔이 "우리가 실제로 가진 것만" 드롭다운에 보이는 데 쓴다.

    [2026-09-28] healthz.llm_providers_supported 는 스키마가 받는 값 9개를 전부 내려줘서
    "고를 수 있다"와 "지금 이 서버가 실제로 그 provider 로 생성할 수 있는가"가 갈렸다.
    상용은 키 설정 여부, 로컬은 그 주소가 응답하는지만 본다(실제 생성 호출은 하지 않는다 —
    비용 없이 빠르게 재확인할 수 있어야 한다).
    """
    from koipa.config import settings  # noqa: PLC0415

    return {
        "active": getattr(settings, "llm_provider", "") or "",
        "providers": provider_status(),
    }


def _actor(auth: dict) -> tuple[str | None, str | None]:
    claims = auth.get("claims")
    sub = getattr(claims, "sub", None) if claims is not None else None
    return sub, auth.get("actor_role")


def _credential_status_response(provider: str) -> SynthProviderCredentialStatus:
    from koipa.config import settings  # noqa: PLC0415

    env_default = getattr(settings, f"{provider}_api_key", "") or ""
    s = llm_credential_store.status(provider, env_default=env_default)
    return SynthProviderCredentialStatus(provider=provider, **s)


@router.put("/synth/providers/{provider}/credential", response_model=SynthProviderCredentialStatus,
            summary="상용 LLM 공급자(anthropic·openai) API 키 설정 — 콘솔, 재시작 불필요")
def set_provider_credential(
    provider: str,
    req: SynthProviderCredentialRequest,
    auth: dict = Depends(require_role("admin")),
):
    """[2026-10-11] 사용자 명시적 지시로 신설 — 전까지는 콘솔에 키 입력칸을 두지 않는 것이
    확정 정책이었다([[no-credential-prompt-ever-2026-08-24]]). 이 경로에 한해 뒤집혔다
    ([[llm-credential-console-input-reversal-2026-10-11]]). 응답은 마스킹된 값만 담는다 —
    저장 직후에도 평문을 돌려주지 않는다.
    """
    if provider not in llm_credential_store.SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"지원하지 않는 공급자: {provider}")
    actor_id, actor_role = _actor(auth)
    try:
        llm_credential_store.set_credential(req.api_key, provider=provider, actor_id=actor_id, actor_role=actor_role)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _credential_status_response(provider)


@router.delete("/synth/providers/{provider}/credential", response_model=SynthProviderCredentialStatus,
               summary="콘솔에서 설정한 키 삭제 — .env 기본값으로 복귀")
def clear_provider_credential(provider: str, auth: dict = Depends(require_role("admin"))):
    if provider not in llm_credential_store.SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"지원하지 않는 공급자: {provider}")
    actor_id, actor_role = _actor(auth)
    llm_credential_store.clear_credential(provider, actor_id=actor_id, actor_role=actor_role)
    return _credential_status_response(provider)


@router.get("/synth/playbook", response_model=SynthPlaybookResponse)
def synth_playbook():
    """지금 생성에 기본 적용되는 규칙 — 관리자가 화면에서 확인한다.

    [2026-09-28] 규칙(m1_synthesis/generation_playbook.py)은 이제 리포에 추적되고
    생성기가 기본으로 참고하지만, "무엇이 적용되고 있는지"를 관리자가 화면 밖에서 소스를
    열어야만 알 수 있었다. 이 경로가 그 자리를 메운다.
    """
    from koipa.config import settings  # noqa: PLC0415
    from koipa.services.synth_playbook_service import view  # noqa: PLC0415

    return {"enabled": bool(getattr(settings, "synth_use_generation_playbook", True)), **view()}


@router.put("/synth/playbook", response_model=SynthPlaybookResponse)
def update_synth_playbook(
    req: SynthPlaybookUpdateRequest,
    auth: dict = Depends(require_role("admin", "kl_backend", "system")),
):
    """조직별 생성 규칙을 저장한다.

    기본 안전 규칙은 코드에 남기고, 여기서는 조직별 업무 문서 규칙·반복 금지 표현만
    수정한다. API·worker가 공유하는 영속 경로에 원자적으로 저장한다.
    """
    from koipa.config import settings  # noqa: PLC0415
    from koipa.services.synth_playbook_service import (  # noqa: PLC0415
        PlaybookValidationError,
        save_override,
        view,
    )

    bind_authenticated_actor(req.actor, auth)
    try:
        save_override(
            custom_rules=req.custom_rules,
            custom_avoid_phrases=req.custom_avoid_phrases,
            updated_by=req.actor.user_id,
        )
    except PlaybookValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="생성 규칙 저장소에 쓸 수 없습니다") from exc
    return {"enabled": bool(getattr(settings, "synth_use_generation_playbook", True)), **view()}


@router.get("/synth/jobs/{synth_job_id}", response_model=SynthJobStatus)
def synth_job_status(synth_job_id: UUID):
    """생성 작업 1건의 상태와 그 작업이 만든 문서.

    [2026-09-05] 종전에는 응답이 synth_job_id 를 주는데 조회 경로가 없었다 —
    "이 작업이 성공했나 · 몇 건 만들었나 · 어느 문서인가"를 물을 수 없었다.
    """
    return SynthesisService().job_status(synth_job_id)


@router.get("/synth/{synth_id}", response_model=SyntheticDocDetail)
def synth_document(synth_id: UUID):
    """검수 화면에서 합성 문서의 전체 본문을 연다.

    큐 목록은 발췌문만 내려 전송량을 제한한다. 이 경로는 사용자가 특정 문서를 열었을
    때만 전체 본문을 반환하며, router의 ``require_auth`` 경계를 그대로 사용한다.
    """
    item = SynthesisService().document(synth_id)
    if item is None:
        raise HTTPException(status_code=404, detail="synth_id not found")
    return item


@router.post(
    "/synth/{synth_id}/review",
    response_model=SynthReviewResponse,
)
def synth_review(
    synth_id: UUID,
    req: SynthReviewRequest,
    auth: dict = Depends(require_role("admin", "reviewer")),
):
    # [#13] reviewed_by 감사 신원 = 인증 principal(body 자칭 위조 차단; JWT sub 우선).
    bind_authenticated_actor(req.actor, auth)
    try:
        res = SynthesisService().review(synth_id, req)
    except ValueError as exc:
        # corrected_grade 가 현재 등급체계에 없는 코드(비활성화 등). 조용히 무시하면
        # 검수자는 고친 줄 알고 넘어가는데 학습행은 원래 등급으로 만들어진다.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if res is None:
        raise HTTPException(status_code=404, detail="synth_id not found")
    return res
