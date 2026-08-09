"""POST /admin/model/reload — 서빙 모델 핫리로드 (활성 ModelVersion 적용).

접근 제한: admin 역할만 허용.

tenant 제거: 테넌트 API 키 발급·교체(rotate-api-key) 엔드포인트는 제거됨.
인증은 KL 서명 JWT 검증만 사용(tb_tenants·api_key_hash 개념 제거); 격리는 KL 포털 전담.
"""
from __future__ import annotations

import datetime as _dt
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

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
    # [P0#①-b] force 우회 사유. 하드닝 프로파일(manual_activate_force_requires_reason=True)에선
    # force=true 로 게이트를 우회할 때 필수 — 미검증 GA 강제활성에 '왜'를 남긴다(감사).
    reason: str = ""


class ActivateModelResponse(BaseModel):
    activated: bool
    blocked: bool
    forced: bool
    version_label: str
    version_id: str | None = None
    reason: str
    gate: dict | None = None
    # [배포전 하드닝 P0#①] locked_gold_eval readiness — 하드닝 프로파일에선 수동 활성도 이 축을
    # 요구(force로만 우회). 요구 여부와 무관하게 항상 노출(운영자 가시화·미검증 활성 감사).
    locked_readiness: dict | None = None
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
        "하드닝 프로파일(onprem-local·full-train)에선 사람서명 locked_gold_eval readiness까지 "
        "요구한다(미검증 모델 GA 활성 차단). 게이트 미충족 시 force=true로만 우회한다(HTTP 요청은 "
        "감사 미들웨어가, force 우회는 forced=true로 기록). 활성 후 무중단 리로드까지 수행한다. "
        "게이트 미통과+force=false면 activated=false·blocked=true, locked_readiness에 배포 축 노출."
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
        reason=req.reason,
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
        locked_readiness=res.get("locked"),
        reloaded=reloaded,
        model_version=model_version,
        model_loaded=model_loaded,
    )


# ── [G2] 활성 모델 롤백 (직전 활성으로 복귀) ─────────────────────────────────────
class RollbackModelRequest(BaseModel):
    # 롤백은 되돌리기 어려운 운영 조치라 '왜'를 남긴다 — 활성화의 force reason 과 같은 취지.
    # 활성화와 달리 게이트가 없다(회귀를 되돌리는 방향이므로 막을 이유가 없다). 그래서 감사에는
    # 사유가 유일한 맥락이다.
    reason: str = Field(min_length=1, max_length=500)


class RollbackModelResponse(BaseModel):
    rolled_back: bool
    from_version: str | None = None
    to_version: str | None = None
    reason: str = ""
    reloaded: bool = False
    model_version: str | None = None
    model_loaded: bool | None = None


@router.post(
    "/model/rollback",
    response_model=RollbackModelResponse,
    dependencies=_ADMIN_ONLY,
    summary="활성 모델을 직전 활성본으로 롤백 + 무중단 리로드",
    description=(
        "잘못 활성된 모델이나 운영에서 미탐 회귀를 보이는 모델을 직전 활성본으로 되돌린다(C-ver). "
        "활성 전환과 **같은 advisory lock** 으로 직렬화되므로 동시 activate/rollback 이 엇갈리지 "
        "않는다. 활성화와 달리 deploy gate 를 적용하지 않는다 — 회귀를 되돌리는 방향이라 막을 "
        "이유가 없고, 게이트로 막으면 정작 사고 시 복구가 불가능해진다. 복귀 후보가 없으면 "
        "rolled_back=false·reason=no_previous_version. 성공 시 프로세스 재기동 없이 서빙을 "
        "리로드한다(활성화 경로와 동일)."
    ),
)
def rollback_model(
    req: RollbackModelRequest,
    auth: dict = Depends(require_role("admin")),
) -> RollbackModelResponse:
    from lloydk.services.training_service import rollback_active_model  # noqa: PLC0415

    res = rollback_active_model(req.reason)
    reloaded = False
    model_version = model_loaded = None
    # 롤백이 실제로 일어났을 때만 리로드한다 — 복귀 후보가 없어 아무것도 안 바뀐 경우까지
    # 모델을 다시 읽으면 불필요한 지연(수 초)만 생긴다.
    if res.get("rolled_back"):
        from lloydk.services.classify_service import ClassifyService  # noqa: PLC0415
        info = ClassifyService.get_instance().reload_model()
        reloaded = bool(info["reloaded"])
        model_version = str(info.get("model_version"))
        model_loaded = bool(info.get("model_loaded"))
    return RollbackModelResponse(
        rolled_back=bool(res.get("rolled_back")),
        from_version=res.get("from"),
        to_version=res.get("to"),
        reason=str(res.get("reason", "")),
        reloaded=reloaded,
        model_version=model_version,
        model_loaded=model_loaded,
    )


# ── [감사] 감사 로그 조회 ────────────────────────────────────────────────────────
class AuditLogEntry(BaseModel):
    occurred_at: str | None = None
    action: str = ""
    actor_id: str | None = None
    actor_role: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    success: bool = True
    error_code: str | None = None
    ip_address: str | None = None
    request_id: str | None = None


class AuditLogResponse(BaseModel):
    items: list[AuditLogEntry]
    total: int
    limit: int
    offset: int
    # 행위자 신원이 비어 있는 비율 — 감사 추적의 실효성을 화면이 스스로 밝히게 한다.
    # 공유 API 키 모드에서는 개별 신원이 없어 actor_id 가 NULL 로 쌓인다.
    actorless_ratio: float = 0.0
    warnings: list[str] = []


@router.get(
    "/audit-log",
    response_model=AuditLogResponse,
    dependencies=_ADMIN_ONLY,
    summary="감사 로그 조회 — 누가 언제 무엇을 했는가",
    description=(
        "tb_audit_log 를 최근순으로 조회한다. 종전에는 미들웨어가 **쓰기만** 하고 읽는 경로가 "
        "없어(라우트 0건), 35,000행이 쌓여 있어도 화면에서 확인할 방법이 없었다. "
        "action(부분일치)·success·actor_id 로 좁힐 수 있다. "
        "응답의 actorless_ratio 는 행위자 신원이 비어 있는 비율이다 — 공유 API 키 모드에서는 "
        "개별 신원이 남지 않으므로, 감사 추적이 실제로 성립하는지를 화면이 스스로 드러낸다."
    ),
)
def audit_log(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    action: str | None = Query(default=None, description="action 부분일치(대소문자 무시)"),
    success: bool | None = Query(default=None, description="성공/실패만 보기"),
    actor_id: str | None = Query(default=None),
) -> AuditLogResponse:
    from sqlalchemy import func as _func, select  # noqa: PLC0415

    from lloydk.db import session_scope  # noqa: PLC0415
    from lloydk.db.models import AuditLog  # noqa: PLC0415

    warnings: list[str] = []
    try:
        with session_scope() as db:
            conds = []
            if action:
                conds.append(AuditLog.action.ilike(f"%{action}%"))
            if success is not None:
                conds.append(AuditLog.success.is_(success))
            if actor_id:
                conds.append(AuditLog.actor_id == actor_id)
            total = int(
                db.execute(select(_func.count()).select_from(AuditLog).where(*conds)).scalar() or 0
            )
            rows = db.execute(
                select(AuditLog).where(*conds)
                .order_by(AuditLog.occurred_at.desc())
                .limit(limit).offset(offset)
            ).scalars().all()
            items = [
                AuditLogEntry(
                    occurred_at=r.occurred_at.isoformat() if r.occurred_at else None,
                    action=r.action or "",
                    actor_id=r.actor_id,
                    actor_role=r.actor_role,
                    target_type=r.target_type,
                    target_id=r.target_id,
                    success=bool(r.success),
                    error_code=r.error_code,
                    ip_address=str(r.ip_address) if r.ip_address else None,
                    request_id=str(r.request_id) if r.request_id else None,
                )
                for r in rows
            ]
    except Exception:  # noqa: BLE001
        logger.exception("audit_log 조회 실패")
        return AuditLogResponse(
            items=[], total=0, limit=limit, offset=offset,
            warnings=["감사 로그 조회에 실패했습니다(DB 미가용) — 빈 목록과 구분하십시오."],
        )

    n = len(items)
    anon = sum(1 for i in items if not i.actor_id)
    ratio = round(anon / n, 3) if n else 0.0
    if n and anon == n:
        warnings.append(
            "이 페이지의 모든 기록에 행위자(actor_id)가 없습니다 — 공유 API 키 모드에서는 개별 "
            "신원이 남지 않습니다. 사람 단위 추적이 필요하면 AUTH_MODE 를 jwt/both 로 운영하십시오."
        )
    return AuditLogResponse(
        items=items, total=total, limit=limit, offset=offset,
        actorless_ratio=ratio, warnings=warnings,
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
    per_grade: dict[str, int]                    # 사람 서명 확정 = 평가정답(게이트 기준)
    real_per_grade: dict[str, int] = {}          # 그중 실문서 출처(판례 등) — 구성 보고
    synthetic_per_grade: dict[str, int] = {}     # 그중 합성 출처 — 구성 보고
    real_total: int = 0
    synthetic_total: int = 0
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
        real_per_grade=s.get("real_per_grade", {}),
        synthetic_per_grade=s.get("synthetic_per_grade", {}),
        real_total=int(s.get("real_total", 0)),
        synthetic_total=int(s.get("synthetic_total", 0)),
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


# ── [DEMO] 시연 실적재 데이터 초기화 ────────────────────────────────────────────
# parse_demo/admin 콘솔의 '실적재 시연'(POST /documents → /classify)으로 만든 행만 물리삭제한다.
# 스코프가 created_by='demo-console' + RAG collection='demo' 두 상수로 고정돼, 실 운영 데이터
# (다른 created_by)는 매칭 자체가 불가하다 — 이 스코프가 1차 안전장치다. FK 순서:
#   classifications 먼저(evidence·corrections 는 CASCADE 자동) → RESTRICT 참조 테이블 → chunks
#   → documents(labels·factor_scores 는 CASCADE 자동). rag_vectors 는 별도 best-effort 트랜잭션
#   (스토어가 pgvector 아니면 tb_rag_vectors 미존재 — 무해 skip). admin 전용.
DEMO_CREATED_BY = "demo-console"   # 데모 실적재 문서 마커 (parse_demo actor.user_id 와 일치)
DEMO_RAG_COLLECTION = "demo"       # 데모 업로드 RAG 색인 컬렉션 (평가 'docs' 오염 분리)
# [SEC-4] blast-radius 안전캡 — 데모는 소수 문서다. 삭제 대상이 이 수를 크게 넘으면 실 데이터가
# 데모 마커(created_by='demo-console')로 오태깅됐을 신호로 보고 물리삭제를 거부(fail-safe·409).
# created_by 가 클라이언트 제어 값(업로드 actor.user_id)이라 원리상 오염 가능한 데 대한 2차 방어 —
# 스코프(1차)·demo_console_enabled(운영 비활성)에 더해 '대량 오삭제 절대 금지' 하한.
DEMO_PURGE_MAX_DOCS = 1000


class DemoPurgeResponse(BaseModel):
    purged: bool
    documents: int
    classifications: int
    chunks: int
    rag_vectors: int
    warnings: list[str] = []


@router.post(
    "/demo/purge",
    response_model=DemoPurgeResponse,
    dependencies=_ADMIN_ONLY,
    summary="[시연] 데모 실적재 데이터 초기화 (created_by='demo-console' 스코프 물리삭제)",
    description=(
        "parse_demo/admin 콘솔의 '실적재 시연'으로 만든 문서·분류·청크·RAG벡터만 삭제한다. "
        "스코프가 created_by='demo-console' + RAG collection='demo' 로 고정돼 실 운영 데이터는 "
        "건드리지 않는다(다른 created_by 는 매칭 불가). admin 전용·물리삭제."
    ),
)
def purge_demo_data() -> DemoPurgeResponse:
    from sqlalchemy import text as _sql  # noqa: PLC0415

    from lloydk.config import settings  # noqa: PLC0415
    from lloydk.db import session_scope  # noqa: PLC0415

    # defense-in-depth: 운영 프로파일에서 실수 노출 차단(스코프가 1차 안전장치, 이건 2차).
    if not getattr(settings, "demo_console_enabled", True):
        raise HTTPException(status_code=404, detail="demo console disabled")

    warnings: list[str] = []
    counts = {"documents": 0, "classifications": 0, "chunks": 0, "rag_vectors": 0}
    _sub = "SELECT doc_id FROM tb_documents WHERE created_by = :marker"
    _m = {"marker": DEMO_CREATED_BY}

    # [SEC-4] blast-radius 안전캡 — 삭제 전에 스코프 규모만 읽는다. 데모 규모를 크게 넘으면
    # (실 데이터가 데모 마커로 오태깅됐을 신호) 어떤 삭제도 하지 않고 거부한다(fail-safe).
    # count 는 별도 read 블록에서 끝내고, 거부는 context manager 밖에서 raise(예외처리 모호성 제거).
    with session_scope() as db:
        n_scoped = int(
            db.execute(
                _sql("SELECT count(*) FROM tb_documents WHERE created_by = :marker"), _m
            ).scalar()
            or 0
        )
    if n_scoped > DEMO_PURGE_MAX_DOCS:
        logger.warning(
            "demo purge REFUSED: created_by=%s 스코프 %d건 > 안전캡 %d (오태깅 의심)",
            DEMO_CREATED_BY, n_scoped, DEMO_PURGE_MAX_DOCS,
        )
        raise HTTPException(
            status_code=409,
            detail=(
                f"demo purge refused: created_by='{DEMO_CREATED_BY}' 스코프 문서 {n_scoped}건이 "
                f"안전캡 {DEMO_PURGE_MAX_DOCS}건을 초과. 실 데이터가 데모 마커로 오태깅됐을 수 "
                "있어 대량 물리삭제를 거부합니다. 스코프를 확인하거나 DB 에서 수동 처리하세요."
            ),
        )

    # 관계형 물리삭제 — 원자적(하나라도 실패하면 전체 롤백).
    relational = [
        ("classifications", f"DELETE FROM tb_classifications WHERE doc_id IN ({_sub})"),
        (None, f"DELETE FROM tb_training_datasets WHERE doc_id IN ({_sub})"),
        (None, f"DELETE FROM tb_sample_documents WHERE doc_id IN ({_sub})"),
        ("chunks", f"DELETE FROM tb_chunks WHERE doc_id IN ({_sub})"),
        ("documents", "DELETE FROM tb_documents WHERE created_by = :marker"),
    ]
    with session_scope() as db:
        for key, stmt in relational:
            res = db.execute(_sql(stmt), _m)
            if key:
                counts[key] = int(res.rowcount or 0)

    # RAG 벡터 — 별도 best-effort(테이블 미존재/스토어 상이 시 무해 skip, 문서 purge 는 이미 확정).
    try:
        with session_scope() as db2:
            res = db2.execute(
                _sql("DELETE FROM tb_rag_vectors WHERE collection = :c"),
                {"c": DEMO_RAG_COLLECTION},
            )
            counts["rag_vectors"] = int(res.rowcount or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("demo purge: rag vectors skipped: %s", exc)
        warnings.append(f"rag vectors purge skipped: {type(exc).__name__}")

    logger.info("demo purge done: %s", counts)
    return DemoPurgeResponse(purged=True, warnings=warnings, **counts)
