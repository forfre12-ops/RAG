"""Celery tasks — 비동기 분류·합성·학습 트리거.

API → Redis 큐 → Worker가 무거운 작업을 수행. Redis 없으면 task는 동기 호출용 함수로도 사용 가능.

부분 실패 처리 (2026-05 추가):
- classify_async / synthesize_batch: bind=True + max_retries=3 + 지수 백오프
- 모든 retry 실패 시 보상 트랜잭션: 이미 처리된 결과를 status="partial"로 JobStore에 기록
"""

from __future__ import annotations

import logging
from typing import Any

from lloydk.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _record_compensation(job_id: str | None, partial_results: list[dict], reason: str) -> None:
    """모든 retry 실패 시 보상 트랜잭션 — JobStore에 partial 기록.

    job_id가 없으면 (단발 호출) JobStore 갱신 생략 — 호출자 책임.
    JobStore 접근 실패는 워커를 죽이지 않고 로깅만.
    """
    if not job_id:
        return
    try:
        import uuid as _uuid

        from lloydk.services.job_store import get_default_store
        store = get_default_store()
        store.update(
            _uuid.UUID(job_id),
            status="partial",
            results=partial_results,
            error=reason,
            failed=1,
        )
        logger.warning(
            "compensation recorded: job_id=%s partial_count=%d reason=%s",
            job_id, len(partial_results), reason,
        )
    except Exception:  # noqa: BLE001
        logger.exception("compensation record failed: job_id=%s", job_id)


@celery_app.task(
    name="lloydk.classify_async",
    bind=True,
    max_retries=2,
    default_retry_delay=1,
)
def classify_async(self: Any, payload: dict, job_id: str | None = None) -> dict:
    """단일 문서 분류 비동기 task.

    Retry: 일시 예외 → self.retry(countdown=2**attempts).
    모든 retry 실패 → 보상 트랜잭션 (JobStore에 partial 상태 기록) 후 예외 재발생.
    """
    from lloydk.schemas.classify import ClassifyRequest
    from lloydk.services.classify_service import ClassifyService

    try:
        req = ClassifyRequest(**payload)
        svc = ClassifyService.get_instance()
        result = svc.classify(req)
        return result.model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        attempts = self.request.retries
        max_r = self.max_retries or 0
        if attempts < max_r:
            countdown = 2 ** attempts
            logger.warning(
                "classify_async retry: attempts=%d/%d countdown=%ds err=%s",
                attempts + 1, max_r, countdown, type(exc).__name__,
            )
            raise self.retry(exc=exc, countdown=countdown) from exc
        # 모든 retry 실패 — 보상 트랜잭션.
        _record_compensation(
            job_id,
            partial_results=[],
            reason=f"classify_async exhausted: {type(exc).__name__}: {exc}",
        )
        raise


@celery_app.task(
    name="lloydk.synthesize_batch",
    bind=True,
    max_retries=2,
    default_retry_delay=1,
)
def synthesize_batch(
    self: Any,
    grade: str,
    count: int,
    domain: str = "mixed",
    job_id: str | None = None,
) -> list[dict]:
    """합성 문서 N건 생성.

    SyntheticDocGenerator 내부도 best-effort지만, 전체 호출 실패 시 retry.
    부분 결과가 있으면 보상 트랜잭션으로 partial 기록.
    """
    from lloydk.modules.m1_synthesis.generator import SynthRequest, SyntheticDocGenerator

    partial: list[dict] = []
    try:
        gen = SyntheticDocGenerator()
        docs = gen.generate(SynthRequest(target_grade=grade, domain=domain, count=count))
        partial = [
            {
                "title": d.title,
                "body": d.body,
                "target_grade": d.target_grade,
                "domain": d.domain,
                "llm_provider": d.llm_provider,
                "cost_usd": d.usage.cost_usd if d.usage else 0.0,
            }
            for d in docs
        ]
        return partial
    except Exception as exc:  # noqa: BLE001
        attempts = self.request.retries
        max_r = self.max_retries or 0
        if attempts < max_r:
            countdown = 2 ** attempts
            logger.warning(
                "synthesize_batch retry: attempts=%d/%d countdown=%ds err=%s",
                attempts + 1, max_r, countdown, type(exc).__name__,
            )
            raise self.retry(exc=exc, countdown=countdown) from exc
        # 모든 retry 실패 — 보상 트랜잭션 (partial은 빈 리스트일 수 있음).
        _record_compensation(
            job_id,
            partial_results=partial,
            reason=f"synthesize_batch exhausted: {type(exc).__name__}: {exc}",
        )
        raise


def _create_training_run_guarded(
    spec_kwargs: dict, *, total_samples: int, trigger: str = "active_learning"
):
    """실제 TrainingRun 1건 생성 → run_id 반환(소비 FK·감사용). DB 미가용 시 None.

    [A2-① 정합] 기존엔 report.run_id(부재) → 랜덤 UUID로 소비를 시도했으나, 그 UUID는
    training_runs에 없어 consumed_in_run FK가 깨졌다(소비 실패 → 교정 무한누적). 실제
    TrainingRun을 만들어 그 run_id로만 소비해 FK 무결성과 감사추적을 보장한다.
    """
    try:
        import uuid as _uuid  # noqa: PLC0415
        from lloydk.db import session_scope  # noqa: PLC0415
        from lloydk.repositories import TrainingRepo  # noqa: PLC0415
        with session_scope() as db:
            run = TrainingRepo(db).create_run(
                total_samples=total_samples,
                hyperparameters=spec_kwargs,
                trigger_type=trigger,
            )
            rid = run.run_id
            return rid if hasattr(rid, "hex") else _uuid.UUID(str(rid))
    except Exception:  # noqa: BLE001
        logger.warning("training run create skipped (DB unavailable) — 소비/등록 best-effort 진행")
        return None


@celery_app.task(name="lloydk.train_classifier")
def train_classifier_task(spec_kwargs: dict | None = None) -> dict:
    """재학습 — 교정 반영 → 학습 → 등록 → 게이트 → (조건부)활성 → 반영분만 소비.

    doc/36 본개발 #1 (A2-①②·C-ver):
      A2-①: unconsumed corrections를 {text,label}로 재빌드해 학습셋에 병합(반영). 그리고
             **실제 학습에 반영된 correction_id만** 소비한다(반영 없이 소비 = 유실, 차단).
      A2-②/C-ver: 학습 모델을 ModelVersion으로 등록하고 배포 합격선 게이트(fnr_high·f1)를
             평가. 활성(activate)은 게이트 통과 + settings.retrain_auto_activate(기본 False)
             둘 다일 때만 — 미검증 모델 자동배포 차단.
    """
    from lloydk.config import settings  # noqa: PLC0415
    from lloydk.modules.m4_training.trainer import TrainSpec, train_classifier  # noqa: PLC0415
    from lloydk.modules.m6_evaluation.active_learning import consume_corrections_for_run  # noqa: PLC0415
    from lloydk.modules.m6_evaluation.corrections_rebuild import (  # noqa: PLC0415
        build_labeled_rows_from_corrections,
        merge_into_train_jsonl,
    )
    from lloydk.services.training_service import register_and_gate_model  # noqa: PLC0415

    spec_kwargs = dict(spec_kwargs or {})

    # ── [A2-①] 교정→라벨 재빌드 + 학습셋 병합(train만 증강, 홀드아웃 불변) ──────────
    rebuild = build_labeled_rows_from_corrections()
    incorporated_ids: list[int] = []
    if getattr(rebuild, "rows", None):
        try:
            import uuid as _uuid  # noqa: PLC0415
            base_train = spec_kwargs.get("train_path") or TrainSpec().train_path
            out_path = f"{settings.retrain_dataset_dir}/train_corr_{_uuid.uuid4().hex[:8]}.jsonl"
            merged = merge_into_train_jsonl(base_train, rebuild, out_path)
            if merged:
                spec_kwargs["train_path"] = merged
                incorporated_ids = list(rebuild.correction_ids)
                logger.info(
                    "train_classifier: %d corrections(%d docs) merged into train set",
                    len(incorporated_ids), rebuild.row_count,
                )
        except Exception:  # noqa: BLE001
            logger.exception("corrections merge failed — 기본 학습셋으로 진행")
            incorporated_ids = []

    # 소비 FK·감사용 실제 TrainingRun (학습 전 생성).
    run_uuid = _create_training_run_guarded(
        spec_kwargs, total_samples=getattr(rebuild, "row_count", 0)
    )

    # ── 학습 ────────────────────────────────────────────────────────────────────
    spec = TrainSpec(**spec_kwargs)
    report = train_classifier(spec)
    out = dict(report.__dict__)
    out["corrections_incorporated"] = len(incorporated_ids)

    # ── [C-ver/A2-②] 등록 + 배포 게이트(활성은 게이트 통과 + opt-in 시에만) ────────
    model_uri = None
    try:
        _mv = getattr(report, "model_version", None)
        _od = getattr(spec, "output_dir", None)
        if _mv and _od:
            model_uri = f"{_od}/{_mv}"
    except Exception:  # noqa: BLE001
        model_uri = None
    try:
        out["deploy"] = register_and_gate_model(
            report,
            training_run_id=run_uuid,
            training_data_count=getattr(rebuild, "row_count", None) or None,
            model_uri=model_uri,
        )
    except Exception:  # noqa: BLE001
        logger.exception("register_and_gate_model failed — 등록/게이트 생략")
        out["deploy"] = {"registered": False, "reason": "exception"}

    # ── [A2-①] 소비 — 실제 반영된 교정만, 실제 run_id로 (반영 없으면 소비 안 함=무손실) ──
    out["corrections_run_id"] = str(run_uuid) if run_uuid else None
    if not incorporated_ids or run_uuid is None:
        out["corrections_consumed"] = 0
        if rebuild.correction_ids and run_uuid is None:
            logger.info("교정 반영했으나 TrainingRun 미생성(DB) — 소비 보류(다음 tick 재시도, 무손실)")
        return out
    try:
        n = consume_corrections_for_run(run_uuid, correction_ids=incorporated_ids)
        out["corrections_consumed"] = n
        if n > 0:
            logger.info("train_classifier: consumed %d incorporated corrections under run_id=%s", n, run_uuid)
    except Exception:  # noqa: BLE001
        logger.exception("consume_corrections_for_run failed (run_id=%s) — corrections 누적 가능", run_uuid)
        out["corrections_consumed"] = -1
    return out


@celery_app.task(name="lloydk.deliver_outbox_tick")
def deliver_outbox_tick(limit: int = 50) -> dict:
    """Outbox webhook 배송 주기 트리거.

    enqueue된 webhook을 실제로 송신하는 워커. beat가 60초마다 호출.
    배송 실패는 outbox 내부에서 지수 백오프 후 재시도, max_attempts 초과 시 DLQ stream으로.
    """
    from lloydk.services.outbox import deliver_once, get_outbox_store, http_send_via_httpx
    store = get_outbox_store()
    out = deliver_once(store, http_send=http_send_via_httpx, limit=limit)
    if out["dlq"] > 0:
        logger.warning("outbox tick: %s — DLQ 발생", out)
    elif out["sent"] > 0 or out["failed"] > 0:
        logger.info("outbox tick: %s", out)
    return out


@celery_app.task(name="lloydk.drift_tick")
def drift_tick(limit: int = 200, threshold: float = 0.5) -> dict:
    """A4: 운영 임베딩 drift 주기 점검 — Celery beat가 호출.

    drift_monitor.run_drift_check가 train centroid + 최근 운영 표본을 비교하고
    Prometheus gauge에 직접 set. alert=True면 알람 룰이 페이지.
    """
    from lloydk.services.drift_monitor import run_drift_check
    report = run_drift_check(limit=limit, threshold=threshold)
    out = report.to_dict()
    logger.info(
        "drift_tick: sample=%d kl=%.4f cosine_mean=%.4f alert=%s",
        report.sample_size, report.kl_divergence, report.cosine_mean, report.alert,
    )
    return out


@celery_app.task(name="lloydk.active_learning_tick")
def active_learning_tick(mode: str = "auto") -> dict:
    """P1-A5: Active learning 주기 트리거.

    mode="auto":     beat 30분 주기. URGENT 도달 시 train_classifier 자동 enqueue.
    mode="snapshot": 일별 스냅샷. 학습 트리거 없이 status만 기록.
    mode="dry":      평가만 수행. 호출용.

    재학습 트리거 정책:
    - URGENT_RETRAIN: 즉시 train_classifier_task enqueue (보안 미탐 누적)
    - RETRAIN_RECOMMENDED: 누적 ≥ recommended_threshold 일 때만 weekly window에 큐잉
      (간단히는 weekday=월요일 새벽에 한번)
    - OK: nothing
    """
    from datetime import datetime
    from lloydk.modules.m6_evaluation.active_learning import evaluate_retraining_need

    status = evaluate_retraining_need()
    payload = status.to_dict()
    payload["mode"] = mode
    payload["ts"] = datetime.utcnow().isoformat() + "Z"

    if mode == "snapshot":
        logger.info("active-learning daily snapshot: %s", payload)
        return payload

    if mode == "dry":
        return payload

    # auto mode
    if status.retrain_status == "URGENT_RETRAIN":
        logger.warning("URGENT_RETRAIN triggered: %s", status.reason)
        try:
            train_classifier_task.apply_async(kwargs={"spec_kwargs": None})
            payload["triggered"] = "URGENT_RETRAIN"
        except Exception:  # noqa: BLE001
            logger.exception("train_classifier_task enqueue failed")
            payload["triggered"] = "ENQUEUE_FAILED"
    elif status.retrain_status == "RETRAIN_RECOMMENDED":
        now = datetime.utcnow()
        # 월요일 03~05시 범위에만 weekly 트리거 (beat tz 한국 기준)
        if now.weekday() == 0 and 3 <= now.hour < 5:
            logger.info("weekly RETRAIN_RECOMMENDED triggered: %s", status.reason)
            try:
                train_classifier_task.apply_async(kwargs={"spec_kwargs": None})
                payload["triggered"] = "RETRAIN_RECOMMENDED"
            except Exception:  # noqa: BLE001
                logger.exception("train_classifier_task enqueue failed")
                payload["triggered"] = "ENQUEUE_FAILED"
        else:
            payload["triggered"] = "SKIP_WAIT_WEEKLY_WINDOW"
    else:
        payload["triggered"] = "NONE"

    return payload
