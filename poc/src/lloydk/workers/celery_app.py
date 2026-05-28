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

# P1-D3: 큐 분리 — classify/index/synthesis/learning.
# 워커 기동 시 `-Q classify,index,synthesis,learning` 또는 큐별 분리 가능.
celery_app.conf.task_routes = {
    "lloydk.classify_async": {"queue": "classify"},
    "lloydk.synthesize_batch": {"queue": "synthesis"},
    "lloydk.train_classifier": {"queue": "learning"},
    "lloydk.index_documents": {"queue": "index"},
    "lloydk.active_learning_tick": {"queue": "learning"},
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
}
celery_app.conf.timezone = "Asia/Seoul"
