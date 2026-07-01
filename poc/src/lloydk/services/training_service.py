"""Training service — TrainingRun + Celery 트리거 + 상태 폴링.

PoC 흐름:
1. /train POST → TrainingRun(status='queued') + JobStore에 등록
2. classify_async와 달리 실 학습은 무거우니, 본 PoC는 즉시 'queued'만 반환
   (운영에서는 Celery train_classifier_task 발사)
3. /train/jobs/{id} GET → TrainingRun(DB) + JobStore(메모리) 머지
4. /train/jobs GET → TrainingRun 최근 목록
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import uuid
from typing import Any, Optional

from sqlalchemy.exc import SQLAlchemyError

from lloydk.db import session_scope
from lloydk.repositories import TrainingRepo
from lloydk.schemas.training import (
    TrainJobList,
    TrainJobSummary,
    TrainRequest,
    TrainResponse,
    TrainStatus,
)
from lloydk.services.async_classify_service import _celery_dispatch_available
from lloydk.services.job_store import get_default_store

logger = logging.getLogger(__name__)


# 모델 활성 전환 직렬화용 advisory lock 키(임의 고정 64-bit 상수). 같은 키 = 같은 임계영역.
_MODEL_ACTIVATION_LOCK_KEY = 0x10AD_AC71_AC10_0001


def _advisory_xact_lock(db, key: int) -> None:
    """PostgreSQL 트랜잭션 advisory lock 획득(best-effort). PG 외/실패는 무시(degrade)."""
    try:
        from sqlalchemy import text  # noqa: PLC0415
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": int(key)})
    except Exception:  # noqa: BLE001
        logger.debug("advisory lock unavailable (non-PG backend?) — proceeding without serialization")


def _report_metrics(report: Any) -> dict:
    """TrainReport(dataclass)/dict/객체 → metric dict 정규화 (ModelVersion.metrics 저장용)."""
    if isinstance(report, dict):
        return dict(report)
    if dataclasses.is_dataclass(report) and not isinstance(report, type):
        return dataclasses.asdict(report)
    to_dict = getattr(report, "to_dict", None)
    if callable(to_dict):
        try:
            d = to_dict()
            if isinstance(d, dict):
                return d
        except Exception:  # noqa: BLE001
            pass
    d = getattr(report, "__dict__", None)
    return dict(d) if isinstance(d, dict) else {}


def _maybe_anchor_report(settings: Any, *, candidate_model_dir: str | None) -> dict | None:
    """opt-in 시 후보 모델을 외부 사실 앵커 코퍼스로 통과시켜 평가 카드 dict를 반환(게이트 주입용).

    fail-OPEN: 비활성/후보경로 부재/측정 실패는 None(앵커 체크 생략) — 게이트 자체의 fail-secure
    (degenerate·fnr 회귀·locked-eval)는 그대로 유지된다. 앵커 측정은 CPU 추론 비용이 있으므로
    기본 비활성. candidate_model_dir = train_classifier_task가 넘긴 후보 산출 경로(output_dir/version).
    """
    if not bool(getattr(settings, "deploy_gate_anchor_eval_enabled", False)):
        return None
    if not candidate_model_dir:
        logger.info("anchor gate: 후보 모델 경로 없음 — 앵커 측정 생략")
        return None
    from pathlib import Path  # noqa: PLC0415
    if not Path(candidate_model_dir).exists():
        logger.warning("anchor gate: 후보 경로 부재(%s) — 앵커 측정 생략", candidate_model_dir)
        return None
    try:
        from lloydk.modules.m6_evaluation.anchor_eval import run_anchor_cards  # noqa: PLC0415
        out = run_anchor_cards(
            model_dir=str(candidate_model_dir),
            cap_per_cell_n=int(getattr(settings, "deploy_gate_anchor_cap_per_cell", 200)),
        )
        rep = out.get("report")
        logger.warning(
            "anchor gate: 후보 앵커 측정 완료 — 평가 %s건, 카드 판정 %s",
            out.get("evaluated"), (rep or {}).get("counts"),
        )
        return rep
    except Exception as exc:  # noqa: BLE001
        logger.warning("anchor gate: 앵커 측정 실패(%s) → 앵커 체크 생략(fail-open)", type(exc).__name__)
        return None


def _maybe_metamorphic_report(settings: Any) -> dict | None:
    """opt-in 시 사전 산출된 메타모픽 회귀 리포트(build_metamorphic_report.to_dict JSON)를 로드.

    fail-OPEN: 경로 미설정/파일 부재/파싱 실패는 None(메타모픽 체크 생략) — 게이트의 fail-secure는
    유지된다. anchor(후보 라이브 측정)와 달리 메타모픽은 쌍 생성(gen_metamorphic_pairs)이 별도
    파이프라인이라, 그 산출 JSON을 경로로 받아 소비한다. **후보 모델 대상으로 재생성해야 유효**
    (stale 리포트는 오판 위험 — 파이프라인이 재학습 eval 단계에서 갱신하는 것을 전제).
    """
    path = str(getattr(settings, "deploy_gate_metamorphic_report_path", "") or "").strip()
    if not path:
        return None
    from pathlib import Path  # noqa: PLC0415
    p = Path(path)
    if not p.exists():
        logger.warning("metamorphic gate: 리포트 경로 부재(%s) — 메타모픽 체크 생략(fail-open)", path)
        return None
    try:
        import json  # noqa: PLC0415
        rep = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(rep, dict):
            logger.warning("metamorphic gate: 리포트가 dict 아님 — 생략")
            return None
        fwd = (rep.get("forward") or {})
        logger.warning(
            "metamorphic gate: 리포트 로드 — 순방향 위반 %s/%s",
            fwd.get("violations"), fwd.get("n"),
        )
        return rep
    except Exception as exc:  # noqa: BLE001
        logger.warning("metamorphic gate: 리포트 로드 실패(%s) → 생략(fail-open)", type(exc).__name__)
        return None


def register_and_gate_model(
    report: Any,
    *,
    training_run_id: uuid.UUID | None = None,
    training_data_count: int | None = None,
    model_uri: str | None = None,
    mlflow_run_id: str | None = None,
    eval_ready: bool | None = None,
) -> dict:
    """재학습 모델을 ModelVersion으로 **등록(C-ver)** 하고 배포 합격선 **게이트** 평가 (A2-②).

    정책(fail-SECURE):
      - register는 항상 시도한다 — 합격 여부와 무관하게 이력·롤백 대상 보존.
      - activate(운영 전환)는 **게이트 통과 AND settings.retrain_auto_activate=True** 모두
        충족할 때만. 기본 retrain_auto_activate=False라 자동배포는 일어나지 않는다(등록만).
      - baseline = 현재 활성 ModelVersion.metrics. 없으면 최초 배포로 게이트가 처리.

    DB 미가용/오류 → {'registered': False, 'reason': ...} (학습 결과 자체는 호출부가 보존).
    반환 dict: registered/version_id/activated/gate(decision dict)/reason.
    """
    from lloydk.config import settings  # noqa: PLC0415
    from lloydk.modules.m6_evaluation.deploy_gate import evaluate_deploy_gate  # noqa: PLC0415

    metrics = _report_metrics(report)
    version_label = str(metrics.get("model_version") or f"v-{uuid.uuid4().hex[:8]}")
    base_model = str(metrics.get("base_model") or getattr(settings, "classifier_base_model", "unknown"))

    try:
        with session_scope() as db:
            # [A2 동시성 락] 모델 활성 전환을 직렬화 — 두 재학습이 동시에 register/activate하며
            # is_active 부분 UNIQUE 인덱스를 다투거나 서로의 활성을 덮어쓰는 레이스를 막는다.
            # pg_advisory_xact_lock은 트랜잭션 종료(commit/rollback) 시 자동 해제. PG 외 백엔드는 무시.
            _advisory_xact_lock(db, _MODEL_ACTIVATION_LOCK_KEY)
            repo = TrainingRepo(db)
            baseline = repo.get_active()
            baseline_metrics = dict(baseline.metrics) if baseline and baseline.metrics else None
            baseline_label = baseline.version_label if baseline else None

            # [앵커 연결] 후보를 외부 사실 앵커로 측정해 게이트에 실측 미탐 신호를 주입(opt-in).
            # 측정 실패는 fail-OPEN(앵커 체크 생략) — 게이트 자체는 fail-secure 유지되고 자동활성은
            # eval_ready locked 게이트가 별도로 막으므로, 앵커 미측정이 자동배포를 열지 않는다.
            anchor_report = _maybe_anchor_report(settings, candidate_model_dir=model_uri)
            metamorphic_report = _maybe_metamorphic_report(settings)

            decision = evaluate_deploy_gate(
                metrics,
                baseline_metrics,
                fnr_high_tolerance=float(getattr(settings, "retrain_fnr_high_tolerance", 0.02)),
                f1_drop_tolerance=float(getattr(settings, "retrain_f1_drop_tolerance", 0.05)),
                first_deploy_fnr_high_max=getattr(settings, "deploy_gate_first_deploy_fnr_high_max", None),
                anchor_report=anchor_report,
                metamorphic_report=metamorphic_report,
                metamorphic_forward_ceiling=float(getattr(settings, "deploy_gate_metamorphic_forward_ceiling", 0.0)),
                metamorphic_min_n=int(getattr(settings, "deploy_gate_metamorphic_min_n", 5)),
                candidate_version=version_label,
                baseline_version=baseline_label,
            )

            # 동일 label 재등록 방지(멱등) — 이미 있으면 그 버전 사용.
            existing = repo.get_by_label(version_label)
            if existing is not None:
                mv = existing
            else:
                mv = repo.register_model_version(
                    version_label=version_label,
                    base_model=base_model,
                    metrics=metrics,
                    training_run_id=training_run_id,
                    mlflow_run_id=mlflow_run_id,
                    model_uri=model_uri,
                )
                if training_data_count is not None:
                    mv.training_data_count = int(training_data_count)

            auto = bool(getattr(settings, "retrain_auto_activate", False))
            # [죽음의 나선 #4] deploy gate는 합성 test.jsonl에서 fnr를 재 실분포 오염에 맹목이다.
            # 그래서 자동 활성은 '실(locked) 평가' readiness까지 요구한다 — eval_ready가 명시적
            # True가 아니면(무실데이터·미확인) 자동 활성을 막는다(등록은 함; 수동 활성은 별도 경로).
            # deploy_gate_require_locked_eval=False로 끄면 옛 동작(합성 평가만으로 자동 활성).
            require_locked = bool(getattr(settings, "deploy_gate_require_locked_eval", True))
            eval_block = require_locked and (eval_ready is not True)
            activated = False
            if decision.passed and auto and not eval_block:
                repo.activate_model_version(mv.version_id)
                activated = True
                logger.warning(
                    "retrain auto-activated: %s (gate passed, locked-eval ready, baseline=%s)",
                    version_label, baseline_label,
                )
            else:
                logger.info(
                    "retrain registered (not activated): %s gate_passed=%s auto=%s eval_block=%s",
                    version_label, decision.passed, auto, eval_block,
                )

            return {
                "registered": True,
                "version_id": str(mv.version_id),
                "version_label": version_label,
                "activated": activated,
                "auto_activate": auto,
                "eval_block": eval_block,
                "gate": decision.to_dict(),
            }
    except SQLAlchemyError as exc:
        logger.warning("register_and_gate_model skipped (DB unavailable): %s", exc)
        return {"registered": False, "reason": f"db_unavailable:{type(exc).__name__}"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("register_and_gate_model failed")
        return {"registered": False, "reason": f"error:{type(exc).__name__}:{exc}"}


def activate_model_manually(
    version_label: str,
    *,
    force: bool = False,
    actor_id: str | None = None,
    actor_role: str | None = None,
) -> dict:
    """[G1] 등록된 ModelVersion을 사람(admin)이 명시적으로 활성화 — 자동 경로와 별개.

    자동 활성(register_and_gate_model)은 retrain_auto_activate·locked-eval 게이트로 막히지만,
    수동 활성은 지재원 릴리스의 정상 배포 경로다. 다만 **현재 활성본 대비 deploy gate(고등급
    미탐 fnr·f1 회귀)** 는 그대로 적용해 회귀 모델을 무심코 활성하는 것을 막고, 게이트 실패 시
    **force=True(감사됨)** 로만 우회한다. 활성 전환은 advisory lock으로 직렬화(레이스 차단).
    HTTP 요청 자체는 감사 미들웨어가 기록하므로 여기선 별도 audit write 없이 logger.warning만.

    반환: {activated, blocked, forced, version_label, version_id, reason, gate, already_active}.
    DB 미가용/미존재/오류는 예외 대신 activated=False + 명시 reason.
    """
    from lloydk.config import settings  # noqa: PLC0415
    from lloydk.modules.m6_evaluation.deploy_gate import evaluate_deploy_gate  # noqa: PLC0415

    try:
        with session_scope() as db:
            _advisory_xact_lock(db, _MODEL_ACTIVATION_LOCK_KEY)
            repo = TrainingRepo(db)
            target = repo.get_by_label(version_label)
            if target is None:
                return {
                    "activated": False, "blocked": False, "forced": False,
                    "version_label": version_label, "version_id": None,
                    "reason": "version_not_found", "gate": None,
                }
            baseline = repo.get_active()
            baseline_metrics = dict(baseline.metrics) if baseline and baseline.metrics else None
            baseline_label = baseline.version_label if baseline else None
            already_active = bool(getattr(target, "is_active", False))

            # 메타모픽은 파일 로드(저비용)라 수동 활성 경로에도 회귀 hard-signal 적용(앵커는
            # CPU 추론 비용으로 수동 경로에선 생략 — 설계 유지). force=True면 아래에서 우회(감사됨).
            decision = evaluate_deploy_gate(
                dict(target.metrics or {}),
                baseline_metrics,
                fnr_high_tolerance=float(getattr(settings, "retrain_fnr_high_tolerance", 0.02)),
                f1_drop_tolerance=float(getattr(settings, "retrain_f1_drop_tolerance", 0.05)),
                first_deploy_fnr_high_max=getattr(settings, "deploy_gate_first_deploy_fnr_high_max", None),
                metamorphic_report=_maybe_metamorphic_report(settings),
                metamorphic_forward_ceiling=float(getattr(settings, "deploy_gate_metamorphic_forward_ceiling", 0.0)),
                metamorphic_min_n=int(getattr(settings, "deploy_gate_metamorphic_min_n", 5)),
                candidate_version=version_label,
                baseline_version=baseline_label,
            )

            if not decision.passed and not force:
                logger.warning(
                    "manual activate BLOCKED (deploy gate failed, force=False): %s vs baseline=%s "
                    "reason=%s actor=%s", version_label, baseline_label, decision.reason, actor_id,
                )
                return {
                    "activated": False, "blocked": True, "forced": False,
                    "version_label": version_label, "version_id": str(target.version_id),
                    "reason": f"deploy_gate_failed: {decision.reason} — force=true 로만 우회(감사됨)",
                    "gate": decision.to_dict(), "already_active": already_active,
                }

            forced = bool(force and not decision.passed)
            repo.activate_model_version(target.version_id)
            logger.warning(
                "manual activate: version=%s baseline=%s gate_passed=%s forced=%s actor=%s role=%s",
                version_label, baseline_label, decision.passed, forced, actor_id, actor_role,
            )
            return {
                "activated": True, "blocked": False, "forced": forced,
                "version_label": version_label, "version_id": str(target.version_id),
                "reason": ("activated (FORCED override — deploy gate 실패)" if forced else "activated"),
                "gate": decision.to_dict(), "already_active": already_active,
            }
    except SQLAlchemyError as exc:
        logger.warning("activate_model_manually skipped (DB unavailable): %s", exc)
        return {
            "activated": False, "blocked": False, "forced": False,
            "version_label": version_label, "version_id": None,
            "reason": f"db_unavailable:{type(exc).__name__}", "gate": None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("activate_model_manually failed")
        return {
            "activated": False, "blocked": False, "forced": False,
            "version_label": version_label, "version_id": None,
            "reason": f"error:{type(exc).__name__}", "gate": None,
        }


def evaluate_rollback_need(
    *, min_samples: int | None = None, fnr_high_tolerance: float | None = None
) -> dict:
    """활성 모델이 운영에서 미탐 회귀를 보이는지 판정 (C-ver 자동 롤백 고도화).

    등록 시점 baseline(`ModelVersion.metrics.fnr_high`) 대비, **라이브 운영 데이터**
    (classifications + corrections, `compute_metrics_from_db`)로 측정한 방향성 미탐(고등급)이
    tolerance를 넘으면 should_rollback=True. 표본이 min_samples 미만이면 판단 보류(무동작).

    순수 판정만 — 실제 롤백은 호출부(auto_rollback_tick)가 settings.auto_rollback_enabled일 때 수행.
    DB 미가용/활성 모델 없음/baseline·라이브 데이터 부족 → should_rollback=False(안전).
    """
    from lloydk.config import settings  # noqa: PLC0415
    from lloydk.modules.m6_evaluation.metrics import compute_metrics_from_db  # noqa: PLC0415

    min_samples = int(min_samples if min_samples is not None
                      else getattr(settings, "rollback_min_samples", 20))
    tol = float(fnr_high_tolerance if fnr_high_tolerance is not None
                else getattr(settings, "rollback_fnr_high_tolerance", 0.10))
    try:
        with session_scope() as db:
            active = TrainingRepo(db).get_active()
            if active is None:
                return {"should_rollback": False, "reason": "no_active_model"}
            label = active.version_label
            base_fnr = (active.metrics or {}).get("fnr_high")
    except SQLAlchemyError as exc:
        return {"should_rollback": False, "reason": f"db_unavailable:{type(exc).__name__}"}

    if base_fnr is None:
        return {"should_rollback": False, "reason": "no_baseline_fnr_high", "active": label}

    live = compute_metrics_from_db(label)
    if live is None or live.sample_count < min_samples:
        return {
            "should_rollback": False, "reason": "insufficient_live_data",
            "active": label, "samples": (live.sample_count if live else 0), "min_samples": min_samples,
        }

    by = live.fnr_underclass_by_grade or {}
    live_fnr = ((by.get("TS", 0.0) + by.get("S1", 0.0)) / 2) if by else live.fnr_underclass_overall
    regressed = live_fnr > float(base_fnr) + tol
    return {
        "should_rollback": bool(regressed),
        "active": label,
        "baseline_fnr_high": float(base_fnr),
        "live_fnr_high": round(float(live_fnr), 4),
        "tolerance": tol,
        "samples": live.sample_count,
        "reason": "live fnr_high regressed beyond tolerance" if regressed else "within tolerance",
    }


def rollback_active_model(reason: str) -> dict:
    """현재 활성 모델을 직전 활성으로 즉시 복귀 (C-ver 롤백 트리거).

    용도: 새로 활성된 모델이 운영에서 미탐 회귀를 보이거나 잘못 활성됐을 때 한 호출로 복귀.
    활성 전환과 동일한 advisory lock으로 직렬화. DB 미가용/복귀후보 없음 → rolled_back=False.
    """
    try:
        with session_scope() as db:
            _advisory_xact_lock(db, _MODEL_ACTIVATION_LOCK_KEY)
            repo = TrainingRepo(db)
            before = repo.get_active()
            restored = repo.rollback_to_previous(reason=reason)
            if restored is None:
                return {
                    "rolled_back": False,
                    "reason": "no_previous_version",
                    "active": before.version_label if before else None,
                }
            logger.warning(
                "model rollback: %s → %s (reason=%s)",
                before.version_label if before else None, restored.version_label, reason,
            )
            return {
                "rolled_back": True,
                "from": before.version_label if before else None,
                "to": restored.version_label,
                "reason": reason,
            }
    except SQLAlchemyError as exc:
        logger.warning("rollback_active_model skipped (DB unavailable): %s", exc)
        return {"rolled_back": False, "reason": f"db_unavailable:{type(exc).__name__}"}


class TrainingService:
    def __init__(self):
        self.jobs = get_default_store()

    def submit(self, req: TrainRequest) -> TrainResponse:
        """학습 작업 등록. DB에 TrainingRun(queued) 1건 생성."""
        logger.debug(
            "training submit enter: type=%s base_model=%s actor=%s",
            req.training_type, req.base_model, req.actor.user_id,
        )
        run_id = self._create_run(req)
        # JobStore 등록 (DB 미가용 시에도 응답 가능)
        self.jobs.create(
            run_id,
            payload={
                "training_type": req.training_type,
                "base_model": req.base_model,
                "submitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "actor": req.actor.user_id,
            },
        )
        # 운영(브로커 가용)은 여기서 Celery train_classifier_task.delay() 발사.
        # 발사해도 worker가 아직 안 돈 시점이라 TrainingRun/Job 상태는 'queued'로 남는다
        # (status 폴링 정합 보존). 테스트/브로커 미가용/eager는 발사하지 않고 'queued'만
        # 반환 — PoC는 GPU/데이터셋 미확보라 동기 in-process 학습을 트리거하지 않는다.
        if _celery_dispatch_available():
            try:
                from lloydk.workers.tasks import train_classifier_task  # noqa: PLC0415

                spec_kwargs = dict(req.hyperparams) if req.hyperparams else None
                train_classifier_task.delay(spec_kwargs)
                logger.info("training enqueued to celery: train_job_id=%s status=queued", run_id)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "celery enqueue failed for training — left as queued: train_job_id=%s",
                    run_id, exc_info=True,
                )
        else:
            logger.info("training submit done: train_job_id=%s status=queued", run_id)
        return TrainResponse(
            train_job_id=run_id,
            status_url=f"/api/v1/train/jobs/{run_id}",
            websocket_url=None,
        )

    def status(self, train_job_id: uuid.UUID) -> Optional[TrainStatus]:
        logger.debug("training status enter: train_job_id=%s", train_job_id)
        # DB(TrainingRun) 우선, 없으면 JobStore
        run = self._get_run(train_job_id)
        if run is not None:
            return TrainStatus(
                train_job_id=train_job_id,
                status=run.status,
                progress=self._progress_from_status(run.status),
                started_at=run.started_at.isoformat() if run.started_at else None,
                error=run.error_message,
            )
        job = self.jobs.get(train_job_id)
        if job is None:
            return None
        return TrainStatus(
            train_job_id=train_job_id,
            status=job.get("status", "queued"),
            progress=0.0,
        )

    def list_recent(
        self, limit: int = 20, status_filter: Optional[str] = None, offset: int = 0
    ) -> TrainJobList:
        logger.debug(
            "training list_recent enter: limit=%d offset=%d filter=%s",
            limit, offset, status_filter,
        )
        try:
            with session_scope() as db:
                repo = TrainingRepo(db)
                # status_filter·offset를 DB로 내려 페이지네이션 정합 보장
                # (이전엔 파이썬 레벨 필터 + total=페이지크기로 부정확).
                runs = repo.list_recent_runs(
                    limit=limit, offset=offset, status_filter=status_filter
                )
                total = repo.count_runs(status_filter=status_filter)
                items = [
                    TrainJobSummary(
                        train_job_id=r.run_id,
                        status=r.status,
                        started_at=r.started_at.isoformat() if r.started_at else None,
                        completed_at=r.completed_at.isoformat() if r.completed_at else None,
                        duration_sec=r.duration_sec,
                        model_version=None,
                        trigger_type=r.trigger_type,
                    )
                    for r in runs
                ]
                return TrainJobList(total=total, items=items)
        except SQLAlchemyError as exc:
            logger.warning("train list skipped: %s", exc)
            return TrainJobList(total=0, items=[])

    # ------------------------------------------------------------
    # internals
    # ------------------------------------------------------------

    def _create_run(self, req: TrainRequest) -> uuid.UUID:
        try:
            with session_scope() as db:
                repo = TrainingRepo(db)
                run = repo.create_run(
                    total_samples=0,  # PoC: 데이터셋 미확정
                    hyperparameters=req.hyperparams,
                    trigger_type=req.training_type,
                    created_by=req.actor.user_id,
                )
                return run.run_id
        except SQLAlchemyError as exc:
            logger.warning("training run create skipped (DB unavailable): %s", exc)
            return uuid.uuid4()

    def _get_run(self, run_id: uuid.UUID):
        try:
            with session_scope() as db:
                return TrainingRepo(db).get_run(run_id)
        except SQLAlchemyError as exc:
            logger.warning("training run lookup failed: run_id=%s err=%s", run_id, exc)
            return None

    @staticmethod
    def _progress_from_status(status: str) -> float:
        return {
            "queued": 0.0,
            "running": 0.5,
            "completed": 1.0,
            "failed": 0.0,
        }.get(status, 0.0)
