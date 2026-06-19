from celery import Celery
from celery.schedules import crontab

from lloydk.config import settings

celery_app = Celery(
    "lloydk",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.task_track_started = True
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]

# 작업 수명 제한 — Redis 백엔드 결과 무한 축적 + 멈춘 작업 무한 점유 방지.
# result_expires: 완료 결과 TTL(초). 24h 후 자동 정리.
# task_soft_time_limit: SoftTimeLimitExceeded 예외로 graceful 정리 유도(5분).
# task_time_limit: 강제 SIGKILL(10분) — soft보다 항상 크게.
# worker_max_tasks_per_child: N작업마다 워커 재생성 — 메모리 누수(모델 로드) 차단.
celery_app.conf.result_expires = settings.celery_result_expires
celery_app.conf.task_soft_time_limit = settings.celery_task_soft_time_limit
celery_app.conf.task_time_limit = settings.celery_task_time_limit
celery_app.conf.worker_max_tasks_per_child = settings.celery_worker_max_tasks_per_child

# P1-D3: 큐 분리 — classify/index/synthesis/learning.
# 워커 기동 시 `-Q classify,index,synthesis,learning` 또는 큐별 분리 가능.
celery_app.conf.task_routes = {
    "lloydk.classify_async": {"queue": "classify"},
    "lloydk.synthesize_batch": {"queue": "synthesis"},
    "lloydk.train_classifier": {"queue": "learning"},
    "lloydk.index_documents": {"queue": "index"},
    "lloydk.active_learning_tick": {"queue": "learning"},
    "lloydk.drift_tick": {"queue": "learning"},
    "lloydk.auto_rollback_tick": {"queue": "learning"},
    "lloydk.deliver_outbox_tick": {"queue": "index"},  # I/O-bound, classify와 격리
}

# P1-A5: Active Learning 주기 트리거 (Celery beat).
# - 매 30분: 임계 도달 시 URGENT_RETRAIN을 자동 큐잉
# - 매일 03:00: 일별 status 스냅샷 (관측성)
celery_app.conf.beat_schedule = {
    "active-learning-check-every-30min": {
        "task": "lloydk.active_learning_tick",
        "schedule": 30 * 60.0,
        "kwargs": {"mode": "auto"},
    },
    "active-learning-daily-snapshot": {
        "task": "lloydk.active_learning_tick",
        "schedule": crontab(minute=0, hour=3),
        "kwargs": {"mode": "snapshot"},
    },
    # A4 (2026-05-29): 매 15분 — 운영 임베딩 drift 점검.
    # alert=True면 lloydk_drift_alert gauge=1 → Grafana 알람 룰 트리거.
    "drift-check-every-15min": {
        "task": "lloydk.drift_tick",
        "schedule": 15 * 60.0,
        "kwargs": {"limit": 200, "threshold": 0.5},
    },
    # C-ver 자동 롤백 (2026-06-19): 매 60분 — 활성 모델 라이브 미탐 회귀 점검.
    # settings.auto_rollback_enabled=True일 때만 실제 롤백, 기본은 판정·로깅만(동작 보존).
    "auto-rollback-check-hourly": {
        "task": "lloydk.auto_rollback_tick",
        "schedule": 60 * 60.0,
    },
    # 표적 7 (2026-05-29): 매 60초 — webhook outbox 배송.
    # enqueue된 KL 콜백을 실제로 송신. 실패는 outbox 내부에서 지수 백오프, max_attempts 후 DLQ.
    "outbox-deliver-every-60s": {
        "task": "lloydk.deliver_outbox_tick",
        "schedule": 60.0,
        "kwargs": {"limit": 50},
    },
}
celery_app.conf.timezone = "Asia/Seoul"
