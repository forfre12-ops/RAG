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
    SynthProvidersStatusResponse,
    SynthQueueResponse,
    SynthReviewRequest,
    SynthReviewResponse,
)
from koipa.services.synth_coverage import DEFAULT_MIN_PER_CELL, coverage_report
from koipa.services.synth_provider_status import provider_status
from koipa.services.synthesis_service import SynthesisService

router = APIRouter(tags=["synthesis"], dependencies=[Depends(require_auth)])


# 합성 데이터 생성은 학습셋에 편입되는 변경성 작업 → 시스템/관리 역할로 제한
# (train과 동일 정책). 검수(review)는 reviewer 권한. 조회(queue)는 broad.
@router.post(
    "/synth/generate",
    response_model=SynthGenerateResponse,
    status_code=202,
)
@limiter.limit("10/minute")
def synth_generate(
    request: Request,
    req: SynthGenerateRequest,
    auth: dict = Depends(require_role("admin", "kl_backend", "system")),
):
    # [#13] created_by 감사 신원 = 인증 principal(body 자칭 위조 차단; JWT sub 우선).
    bind_authenticated_actor(req.actor, auth)
    return SynthesisService().submit(req)


@router.get("/synth/queue", response_model=SynthQueueResponse)
def synth_queue(
    status: str = Query(default="pending", pattern=r"^(pending|approved|rejected)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return SynthesisService().queue(status=status, limit=limit, offset=offset)


# 조회는 broad(라우터의 require_auth 만). 학습셋의 등급×도메인 분포일 뿐 본문을 내지 않는다.
@router.get("/synth/coverage", response_model=SynthCoverageResponse)
def synth_coverage(
    min_per_cell: int = Query(
        default=DEFAULT_MIN_PER_CELL, ge=1, le=1000,
        description="이 수 미만인 칸을 '얇은 칸'으로 본다",
    ),
):
    """합성으로 채울 자리 — 등급 × 도메인 격자.

    [2026-09-06] 왜 이 경로가 생겼나. 계산은 scripts/synth_coverage_gaps.py 안에만 있어서
    사람이 터미널에서 표를 읽고 → 조합을 외우고 → 콘솔 폼에 손으로 다시 넣어야 했다.
    필요한 정보가 이미 있는데 화면이 그것을 몰랐다. 이제 화면이 격자를 띄우고, 칸을
    누르면 생성 폼(등급·도메인)이 채워진다.

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
    비용 없이 빠르게 재확인할 수 있어야 한다). 조회는 broad(라우터의 require_auth 만) —
    /synth/coverage 와 같은 이유로 본문을 내지 않고 가용성 불리언뿐이다.
    """
    from koipa.config import settings  # noqa: PLC0415

    return {
        "active": getattr(settings, "llm_provider", "") or "",
        "providers": provider_status(),
    }


@router.get("/synth/playbook", response_model=SynthPlaybookResponse)
def synth_playbook():
    """지금 생성에 기본 적용되는 규칙 — 관리자가 화면에서 확인한다.

    [2026-09-28] 규칙(m1_synthesis/generation_playbook.py)은 이제 리포에 추적되고
    생성기가 기본으로 참고하지만, "무엇이 적용되고 있는지"를 관리자가 화면 밖에서 소스를
    열어야만 알 수 있었다. 이 경로가 그 자리를 메운다. 조회는 broad — 비밀이 아니라
    생성 안내문이다.
    """
    from koipa.config import settings  # noqa: PLC0415
    from koipa.modules.m1_synthesis.generation_playbook import (  # noqa: PLC0415
        AVOID_PHRASES,
        GENERATION_RULES,
        playbook_text,
        playbook_version,
    )

    return {
        "enabled": bool(getattr(settings, "synth_use_generation_playbook", True)),
        "version": playbook_version(),
        "rules": list(GENERATION_RULES),
        "avoid_phrases": list(AVOID_PHRASES),
        "text": playbook_text(),
    }


@router.get("/synth/jobs/{synth_job_id}", response_model=SynthJobStatus)
def synth_job_status(synth_job_id: UUID):
    """생성 작업 1건의 상태와 그 작업이 만든 문서.

    [2026-09-05] 종전에는 응답이 synth_job_id 를 주는데 조회 경로가 없었다 —
    "이 작업이 성공했나 · 몇 건 만들었나 · 어느 문서인가"를 물을 수 없었다.
    """
    return SynthesisService().job_status(synth_job_id)


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
