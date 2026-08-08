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
celery_app.conf.task_acks_late = True
celery_app.conf.task_reject_on_worker_lost = True
celery_app.conf.worker_prefetch_multiplier = 1
# ⚠ [배포 함정 · 실측 2026-08-08] 아래 시간제한은 **발행자(publisher) 측 값이 메시지 헤더에
# 실려** 워커로 간다. 워커는 자기 설정보다 메시지에 실린 값을 우선한다.
# 그래서 이 파일을 고친 뒤 **워커만 재시작하면 아무것도 바뀌지 않는다** — /train 을 받아
# 태스크를 발행하는 것은 api 컨테이너이므로, api 가 옛 값을 그대로 메시지에 박아 보낸다.
# 실제로 워커에서 새 값(21600)이 확인되는데도 학습이 정확히 옛 상한(1800)에 잘렸다.
# 시간제한을 바꿀 때는 **api 와 worker 를 함께 재기동**할 것.
celery_app.conf.task_annotations = {
    # [실측 2026-08-02] 종전 min(설정, 120) 은 설정을 올려도 120초로 깎아, 대용량 문서가
    # 완주하지 못하고 3회 재시도 끝에 status=partial 로 끝났다
    # (100페이지 = 180,000자 · 351청크 → 6 CPU 에서 312초 소요).
    # 동기 경로는 청크 상한(analyze_sync_max_chunks)으로 막아 두었으므로 대용량은 이
    # 비동기 경로가 유일한 처리 수단이다 — 여기서 잘리면 처리 방법이 아예 없다.
    # 짧은 상한은 "분류는 빠르다"는 전제였는데 문서 분량에 비례하므로 성립하지 않는다.
    # 전역값을 그대로 쓰되(설정으로 조정 가능해야 한다) 하한만 900 으로 둔다 —
    # CPU 가 낮은 회원사(2코어면 3배 느려 ~900초)에서도 완주하도록.
    "lloydk.classify_async": {
        "soft_time_limit": max(settings.celery_task_soft_time_limit, 900),
        "time_limit": max(settings.celery_task_time_limit, 1200),
    },
    "lloydk.synthesize_batch": {
        "soft_time_limit": min(settings.celery_task_soft_time_limit, 300),
        "time_limit": min(settings.celery_task_time_limit, 420),
    },
    "lloydk.golden_build": {
        "soft_time_limit": max(settings.celery_task_soft_time_limit, 900),
        "time_limit": max(settings.celery_task_time_limit, 1200),
    },
    # [실측 2026-08-08] 종전 하한 1800(30분) 은 학습을 완주시키지 못했다. 실서버(16vCPU,
    # GPU 없음)에서 정본 학습셋(2,042행) 풀 파인튜닝이 29분 30초 지점에서
    # SoftTimeLimitExceeded 로 잘렸다 — 메모리는 정상이었다(피크 13.4GiB/16GiB, OOM 0).
    # 30분이라는 값은 학습 소요를 재지 않고 잡힌 것으로 보인다. 같은 파일 tasks.py:747 이
    # 이 작업을 "야간 재학습(CPU ~1h)" 이라고 적고 있어, 한 시간짜리로 문서화한 작업을
    # 30분에 죽이는 자기모순이었다.
    # 영향 범위는 콘솔 수동 /train 만이 아니다 — 지재원 URGENT 드리프트 자동재학습
    # (tasks.py:713·724)과 고객사 야간 증분 재학습(tasks.py:783)이 모두 이 태스크를 태운다.
    # 즉 고객사 무인 야간 재학습은 구조적으로 완주할 수 없었다.
    # 하한 산정은 추정하지 말고 실측을 쓴다. 같은 실서버에서 학습 진행률 로그가
    # 남긴 값(2026-08-08):
    #     51/1280 [33:33<12:42:44, 37.24s/it]
    # 즉 기본 하이퍼파라미터(epochs=5 · batch_size=8 · max_seq_len=512, 2,042행 →
    # 256스텝/에폭 × 5 = 1,280스텝)로 **총 약 13시간**이 걸린다. 스텝당 37초는 CPU 라
    # 그렇다 — 지재원 A100 이면 이 값이 아니다(GPU 노드에서는 이 하한이 과할 뿐 해롭지 않다).
    # 따라서 하한을 18h/19h 로 둔다: 실측 13h 에 코어 점유 경합·평가·등록 단계를 얹은 여유.
    # soft 에서 graceful 정리, hard 는 그보다 크게 두어 진짜 멈춘 작업은 여전히 SIGKILL 로
    # 회수한다(워커 동시성=코어수라 1슬롯 장기점유가 분류를 굶기지 않는다).
    # ⚠ 13시간은 **감리 현장에서 실시간 시연할 수 없는 길이**다. 시연은 사전에 학습해 둔
    # 결과로 하거나 hyperparams(epochs 등)를 줄여 별도 실행할 것 — API 의 hyperparams 가
    # TrainSpec 으로 전달된다(training_service.py:559).
    "lloydk.train_classifier": {
        "soft_time_limit": max(settings.celery_task_soft_time_limit, 64800),
        "time_limit": max(settings.celery_task_time_limit, 68400),
    },
}

# P1-D3: 큐 분리 — classify/index/synthesis/learning.
# 워커는 `-Q classify,index,synthesis,learning,celery`로 모든 큐를 구독해야 한다
# (docker-compose worker / Makefile worker 참조). 미구독 큐의 작업은 소비되지 않는다.
# 주의: 여기 라우팅하는 task name은 반드시 tasks.py에 실제 정의돼 있어야 한다
# (미정의 name을 라우팅하면 호출 시 NotRegistered). 'index' 큐는 deliver_outbox_tick이 사용.
celery_app.conf.task_routes = {
    "lloydk.classify_async": {"queue": "classify"},
    "lloydk.synthesize_batch": {"queue": "synthesis"},
    "lloydk.train_classifier": {"queue": "learning"},
    "lloydk.golden_build": {"queue": "learning"},  # 빌더 — train과 자원 풀 공유
    "lloydk.active_learning_tick": {"queue": "learning"},
    "lloydk.nightly_incremental_retrain_tick": {"queue": "learning"},  # 고객사 야간 증분 재학습 트리거
    "lloydk.drift_tick": {"queue": "learning"},
    "lloydk.auto_rollback_tick": {"queue": "learning"},
    "lloydk.verify_audit_chain_tick": {"queue": "learning"},
    "lloydk.deliver_outbox_tick": {"queue": "index"},  # I/O-bound, classify와 격리
    "lloydk.ensure_partitions_tick": {"queue": "index"},  # DDL, 경량 I/O
    "lloydk.beat_heartbeat_tick": {"queue": "index"},  # 경량 생존 신호
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
    # [고객사 야간 증분 재학습 2026-07] 매일 02:00 KST — enable_incremental_retrain=True 노드
    # (고객사 onprem-local)에서만 발화. 태스크가 플래그·unconsumed 교정을 자체 게이트하므로
    # 지재원(full-train)·순수 추론 노드에서는 no-op(SKIP_*). 재학습본은 후보 등록만 —
    # 자동 서빙 안 됨(사람서명 locked-eval/감사 force 로만 활성화). partitions(02:10) 직전 off-peak.
    "nightly-incremental-retrain-0200": {
        "task": "lloydk.nightly_incremental_retrain_tick",
        "schedule": crontab(minute=0, hour=2),
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
    # beat 생존 신호 — 매 60초. beat 스케줄러가 죽으면 게시가 멈춰 lloydk_beat_heartbeat_age_seconds
    # 가 증가 → BeatHeartbeatStale 알람. (감사체인검증·drift·파티션 게이지가 beat 사망 시 마지막
    #  안전값에 무음 동결되던 사각지대를 beat-down 자체 알람으로 보완.)
    "beat-heartbeat-every-60s": {
        "task": "lloydk.beat_heartbeat_tick",
        "schedule": 60.0,
    },
    # #5 파티션 롤오버 — 매일 02:10, 향후 3개월 월 파티션 보장(IF NOT EXISTS 멱등).
    # baseline은 정적 파티션만 생성하므로 이게 없으면 _default가 비대해진다.
    "ensure-partitions-daily": {
        "task": "lloydk.ensure_partitions_tick",
        "schedule": crontab(minute=10, hour=2),
        "kwargs": {"months_ahead": 3},
    },
    # NFR-SEC-01 감사체인 무결성 — 매일 03:30. broken>0이면 lloydk_audit_chain_broken_total↑
    # → P0 AuditChainBroken 알람. 과거 row 변조·삭제·재배열 정기 검출.
    "verify-audit-chain-daily": {
        "task": "lloydk.verify_audit_chain_tick",
        "schedule": crontab(minute=30, hour=3),
    },
}
celery_app.conf.timezone = "Asia/Seoul"


# #30: 워커 부팅 시점 구조화(JSON) 로깅 setup — opt-in(LLOYDK_LOG_JSON truthy 시에만).
# worker_process_init은 각 워커 프로세스가 부팅될 때 발생하므로 fork된 자식에도 적용된다.
# 기본(미설정)은 setup_logging() 내부에서 no-op이라 기존 로깅을 보존한다. import/연결
# 실패는 모두 흡수 — 로깅 설정이 워커 부팅을 막지 않는다(동작 보존).
try:
    from celery.signals import worker_process_init  # noqa: E402

    @worker_process_init.connect
    def _init_worker_logging(**_kwargs):  # pragma: no cover - 워커 런타임 시그널
        try:
            from lloydk.obs.otel import setup_logging
            setup_logging()
        except Exception:  # noqa: BLE001
            pass
except Exception:  # noqa: BLE001
    pass


# [P0 수정] 태스크 등록 — 워커는 `-A lloydk.workers.celery_app`로 기동하므로 이 모듈이
# tasks.py를 import 해야 @celery_app.task 데코레이터가 실행돼 11개 태스크가 등록된다.
# (import 누락 시 worker/beat 가 모든 태스크를 NotRegistered로 폐기 — beat 자동화 전멸.)
# tasks.py 는 celery_app 을 import 하므로, celery_app 정의가 끝난 이 위치(파일 말미)에서
# import 해야 순환참조가 안전하게 해소된다.
from lloydk.workers import tasks as _tasks  # noqa: E402,F401
