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
    max_retries=3,
    default_retry_delay=10,
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
    max_retries=3,
    default_retry_delay=10,
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


@celery_app.task(name="lloydk.train_classifier")
def train_classifier_task(spec_kwargs: dict | None = None) -> dict:
    from lloydk.modules.m4_training.trainer import TrainSpec, train_classifier

    spec = TrainSpec(**(spec_kwargs or {}))
    report = train_classifier(spec)
    return report.__dict__


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
