"""POST /admin/model/reload — 서빙 모델 핫리로드 (활성 ModelVersion 적용).

접근 제한: admin 역할만 허용.

tenant 제거: 테넌트 API 키 발급·교체(rotate-api-key) 엔드포인트는 제거됨.
인증은 KL 서명 JWT 검증만 사용(tb_tenants·api_key_hash 개념 제거); 격리는 KL 포털 전담.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lloydk.api._rbac import require_role

router = APIRouter(prefix="/admin", tags=["admin"])

_ADMIN_ONLY = [Depends(require_role("admin"))]


class ReloadModelResponse(BaseModel):
    reloaded: bool
    model_dir: str | None
    model_version: str
    model_loaded: bool


@router.post(
    "/model/reload",
    response_model=ReloadModelResponse,
    dependencies=_ADMIN_ONLY,
    summary="서빙 모델 핫리로드 (활성 ModelVersion 적용)",
    description=(
        "활성 ModelVersion(model_uri)을 재해석해 추론 파이프라인을 재구성합니다. "
        "activate_model_version/rollback 후 프로세스 재기동 없이 새 모델을 서빙에 반영 (C-ver). "
        "활성 버전이 없거나 로컬 디렉토리가 아니면 env CLASSIFIER_MODEL_DIR로 폴백."
    ),
)
def reload_model() -> ReloadModelResponse:
    from lloydk.services.classify_service import ClassifyService  # noqa: PLC0415
    info = ClassifyService.get_instance().reload_model()
    return ReloadModelResponse(
        reloaded=bool(info["reloaded"]),
        model_dir=info.get("model_dir"),
        model_version=str(info.get("model_version")),
        model_loaded=bool(info.get("model_loaded")),
    )


# ── [번들 B] 고등급 이중검토 보류 가시화 ──────────────────────────────────────
class EscalationHeldResponse(BaseModel):
    by_grade: dict[str, int]
    total: int


@router.get(
    "/escalation-held",
    response_model=EscalationHeldResponse,
    dependencies=_ADMIN_ONLY,
    summary="이중검토 보류(needs_second_review) 등급별 건수",
    description=(
        "high_grade_dual_review ON 시 1인 동의 고등급 교정은 needs_second_review로 보류된다. "
        "운영자가 능동 쿼리해야만 보이던 '안 보이는 보류큐'를 등급별 건수로 노출(가시화). "
        "DB 미가용 시 빈 집계."
    ),
)
def escalation_held() -> EscalationHeldResponse:
    from lloydk.services.confirm_service import (  # noqa: PLC0415
        count_needs_second_review_by_grade,
    )
    counts = count_needs_second_review_by_grade()
    return EscalationHeldResponse(by_grade=counts, total=sum(counts.values()))


# ── [번들 D] locked_gold_eval readiness 가시화 ───────────────────────────────
class LockedReadinessResponse(BaseModel):
    ready: bool
    per_grade: dict[str, int]
    missing: list[str]
    min_per_grade: int
    require_locked_eval: bool
    deploy_locked_gate_passed: bool
    reason: str


@router.get(
    "/locked-readiness",
    response_model=LockedReadinessResponse,
    dependencies=_ADMIN_ONLY,
    summary="locked_gold_eval(사람서명 평가정답) 배포 readiness",
    description=(
        "deploy gate가 자동활성 시 요구하는 등급별 min_locked_per_grade 충족 여부를 노출 — "
        "등급별 locked 보유/부족·배포 가능 여부. 무실데이터 단계엔 비어 ready=false(진실), "
        "사람서명으로 채워지면 자동으로 켜진다. settings.locked_eval_jsonl 경로 기준(읽기 전용)."
    ),
)
def locked_readiness() -> LockedReadinessResponse:
    from lloydk.modules.m6_evaluation.locked_readiness import (  # noqa: PLC0415
        locked_eval_readiness,
    )
    s = locked_eval_readiness()
    return LockedReadinessResponse(
        ready=bool(s["ready"]),
        per_grade=s["per_grade"],
        missing=list(s["missing"]),
        min_per_grade=int(s["min_per_grade"]),
        require_locked_eval=bool(s["require_locked_eval"]),
        deploy_locked_gate_passed=bool(s["deploy_locked_gate_passed"]),
        reason=str(s["reason"]),
    )
