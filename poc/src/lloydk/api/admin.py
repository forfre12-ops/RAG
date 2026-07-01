"""POST /admin/model/reload — 서빙 모델 핫리로드 (활성 ModelVersion 적용).

접근 제한: admin 역할만 허용.

tenant 제거: 테넌트 API 키 발급·교체(rotate-api-key) 엔드포인트는 제거됨.
인증은 KL 서명 JWT 검증만 사용(tb_tenants·api_key_hash 개념 제거); 격리는 KL 포털 전담.
"""
from __future__ import annotations

import datetime as _dt
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lloydk.api._rbac import require_role

logger = logging.getLogger(__name__)

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


# ── [G1] 특정 ModelVersion 수동 활성화 (deploy gate 적용·force 우회 감사) ──────────
class ActivateModelRequest(BaseModel):
    version_label: str
    force: bool = False


class ActivateModelResponse(BaseModel):
    activated: bool
    blocked: bool
    forced: bool
    version_label: str
    version_id: str | None = None
    reason: str
    gate: dict | None = None
    reloaded: bool = False
    model_version: str | None = None
    model_loaded: bool | None = None


@router.post(
    "/model/activate",
    response_model=ActivateModelResponse,
    summary="특정 ModelVersion 활성화 (deploy gate 적용·force 우회 감사)",
    description=(
        "등록된 ModelVersion을 admin이 명시적으로 활성화한다(G1 — 수동 배포 경로). 현재 활성본 "
        "대비 deploy gate(고등급 미탐 fnr·f1 회귀)를 적용해 회귀 모델의 무심한 활성을 막고, "
        "게이트 실패 시 force=true로만 우회한다(HTTP 요청은 감사 미들웨어가 기록). 활성 후 "
        "무중단 리로드까지 수행한다. 게이트 미통과+force=false면 activated=false·blocked=true로 응답."
    ),
)
def activate_model(
    req: ActivateModelRequest,
    auth: dict = Depends(require_role("admin")),
) -> ActivateModelResponse:
    from lloydk.services.training_service import activate_model_manually  # noqa: PLC0415

    # require_auth 반환 dict은 actor_id 키가 없다(jwt=claims.sub, api_key=공유키·actor 없음).
    # 과거 auth.get("actor_id")는 항상 None이라 강제활성 감사가 익명으로 기록됐다.
    # jwt subject를 실제 actor로 기록하고, api-key 모드는 사용자 신원이 없어 None(정직).
    _claims = auth.get("claims")
    actor_id = _claims.sub if _claims is not None else None
    res = activate_model_manually(
        req.version_label,
        force=req.force,
        actor_id=actor_id,
        actor_role=auth.get("actor_role"),
    )
    reloaded = False
    model_version = model_loaded = None
    if res.get("activated"):
        from lloydk.services.classify_service import ClassifyService  # noqa: PLC0415
        info = ClassifyService.get_instance().reload_model()
        reloaded = bool(info["reloaded"])
        model_version = str(info.get("model_version"))
        model_loaded = bool(info.get("model_loaded"))
    return ActivateModelResponse(
        activated=bool(res.get("activated")),
        blocked=bool(res.get("blocked")),
        forced=bool(res.get("forced")),
        version_label=str(res.get("version_label", req.version_label)),
        version_id=res.get("version_id"),
        reason=str(res.get("reason", "")),
        gate=res.get("gate"),
        reloaded=reloaded,
        model_version=model_version,
        model_loaded=model_loaded,
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


# ── 운영 검수 대시보드 — 분산된 운영 신호를 한 화면용 JSON으로 종합 ──────────────
# 그동안 추가한 가시성 신호(보류큐·locked readiness·교정 backlog·kill-gate·드리프트)가
# 엔드포인트별로 흩어져 있어 운영자가 여러 곳을 봐야 했다. 이 엔드포인트가 한 번에 모은다.
# 각 섹션은 독립 best-effort — 한 소스가 죽어도 500이 아니라 그 섹션만 비고 degraded에 표기.
# 모두 가벼운 신호(드리프트는 마지막 게이지 값 read, 재계산 안 함)라 대시보드 폴링에 안전.
class DashboardResponse(BaseModel):
    generated_at: str
    escalation_held: dict
    locked_readiness: dict
    active_learning: dict
    kill_gate: dict
    drift: dict
    degraded: list[str]  # 로드 실패한 섹션명 — 부분 가용 가시화


def _section(name: str, fn, degraded: list[str]) -> dict:
    """섹션 best-effort 로더 — 실패 시 {} + degraded에 이름 기록(500 방지)."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        logger.debug("dashboard section %s failed: %s", name, exc)
        degraded.append(name)
        return {}


def _drift_snapshot() -> dict:
    """드리프트 — 재계산 없이 마지막 게이지 값만 read(주기 refresh가 갱신)."""
    from lloydk.api import prom_metrics as _pm  # noqa: PLC0415

    def _g(gauge) -> float:
        try:
            return float(gauge._value.get())
        except Exception:  # noqa: BLE001
            return 0.0
    return {
        "kl_divergence": _g(_pm.DRIFT_KL_DIVERGENCE),
        "cosine_mean": _g(_pm.DRIFT_COSINE_MEAN),
        "alert": _g(_pm.DRIFT_ALERT),
        "sample_size": _g(_pm.DRIFT_SAMPLE_SIZE),
    }


@router.get(
    "/dashboard",
    response_model=DashboardResponse,
    dependencies=_ADMIN_ONLY,
    summary="운영 검수 대시보드 — 보류큐·readiness·교정 backlog·kill-gate·드리프트 종합",
    description=(
        "운영자가 한 화면에서 보는 종합 상태: 이중검토 보류(등급별), locked_gold_eval 배포 "
        "readiness, active-learning 교정 backlog/재학습 권고, kill-gate 발동 여부, 임베딩 드리프트. "
        "각 섹션은 독립 best-effort — 일부 소스 미가용 시 그 섹션만 비고 degraded에 표기(500 없음)."
    ),
)
def dashboard() -> DashboardResponse:
    from lloydk.modules.m6_evaluation.active_learning import (  # noqa: PLC0415
        evaluate_retraining_need,
    )
    from lloydk.modules.m6_evaluation.kill_gate import run_kill_gate_check  # noqa: PLC0415
    from lloydk.modules.m6_evaluation.locked_readiness import (  # noqa: PLC0415
        locked_eval_readiness,
    )
    from lloydk.services.confirm_service import (  # noqa: PLC0415
        count_needs_second_review_by_grade,
    )

    degraded: list[str] = []

    def _held() -> dict:
        c = count_needs_second_review_by_grade()
        return {"by_grade": c, "total": sum(c.values())}

    return DashboardResponse(
        generated_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        escalation_held=_section("escalation_held", _held, degraded),
        locked_readiness=_section("locked_readiness", locked_eval_readiness, degraded),
        active_learning=_section(
            "active_learning", lambda: evaluate_retraining_need().to_dict(), degraded
        ),
        kill_gate=_section("kill_gate", run_kill_gate_check, degraded),
        drift=_section("drift", _drift_snapshot, degraded),
        degraded=degraded,
    )
