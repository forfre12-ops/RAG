from celery import Celery
from celery.schedules import crontab

from koipa.config import settings

celery_app = Celery(
    "koipa",
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

# [실측 2026-08-08] Redis 브로커의 visibility_timeout 기본값은 **3600초(1시간)** 다.
# 이 시간 안에 ack 되지 않은 메시지를 브로커가 "워커가 죽었다"고 보고 **다시 배달**한다.
# task_acks_late=True 라 ack 는 작업이 끝나야 나가므로, 1시간을 넘기는 작업은 아직 정상
# 실행 중인데도 재배달되어 **같은 작업이 중복 실행**된다.
# 실서버에서 그대로 재현했다 — 학습 시작 10:07:34 → 재배달 11:08:34(정확히 1시간 뒤) →
# 학습 두 개가 동시에 돌며 anon-rss 9.26GB + 3.43GB → 11:09:39 커널 cgroup OOM 으로 둘 다 사망.
# 시간제한을 아무리 올려도 소용없다: 1시간마다 재배달·중복 실행되어 영원히 완주하지 못한다.
# (실제로 완주에 13시간이 걸리는 train_classifier 가 여기 걸린다. classify_async·golden_build 는
#  하드 상한이 1,200초라 1시간 안에 끝나므로 영향이 없었고, 그래서 여태 드러나지 않았다.)
# 규칙: visibility_timeout 은 **가장 긴 하드 시간제한보다 커야 한다**. 아래 train_classifier
# 하한이 19h(68,400s)이므로 24h 로 둔다. 결과 백엔드도 같은 Redis 라 함께 지정한다.
_VISIBILITY_TIMEOUT = 86400
celery_app.conf.broker_transport_options = {"visibility_timeout": _VISIBILITY_TIMEOUT}
celery_app.conf.result_backend_transport_options = {"visibility_timeout": _VISIBILITY_TIMEOUT}
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
    "koipa.classify_async": {
        "soft_time_limit": max(settings.celery_task_soft_time_limit, 900),
        "time_limit": max(settings.celery_task_time_limit, 1200),
    },
    "koipa.synthesize_batch": {
        "soft_time_limit": min(settings.celery_task_soft_time_limit, 300),
        "time_limit": min(settings.celery_task_time_limit, 420),
    },
    "koipa.golden_build": {
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
    # 256스텝/에폭 × 5 = 1,280스텝)로 **총 약 13시간**이 걸렸다.
    # ⚠ 이 13시간은 **보편값이 아니다**. 측정한 서버가 QEMU Virtual CPU 라 AVX/AVX2/AVX-512·FMA 가
    # 전부 없고(torch capability=DEFAULT) 스레드도 8코어 중 4개만 썼다. 같은 작업이 AVX-512 를
    # 갖춘 일반 CPU 에서는 약 6배 빨라 2시간 남짓이다(≈1시간/1,000행). GPU 면 더 짧다.
    # 즉 하드웨어만으로 13시간 ↔ 2시간이 갈린다 — 아래 하한은 **가장 느린 쪽**에 맞춘 값이다.
    # 따라서 하한을 18h/19h 로 둔다: 실측 13h 에 코어 점유 경합·평가·등록 단계를 얹은 여유.
    # soft 에서 graceful 정리, hard 는 그보다 크게 두어 진짜 멈춘 작업은 여전히 SIGKILL 로
    # 회수한다(워커 동시성=코어수라 1슬롯 장기점유가 분류를 굶기지 않는다).
    # ⚠ 13시간은 **감리 현장에서 실시간 시연할 수 없는 길이**다. 시연은 사전에 학습해 둔
    # 결과로 하거나 hyperparams(epochs 등)를 줄여 별도 실행할 것 — API 의 hyperparams 가
    # TrainSpec 으로 전달된다(training_service.py:559).
    "koipa.train_classifier": {
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
    "koipa.classify_async": {"queue": "classify"},
    "koipa.synthesize_batch": {"queue": "synthesis"},
    "koipa.train_classifier": {"queue": "learning"},
    "koipa.golden_build": {"queue": "learning"},  # 빌더 — train과 자원 풀 공유
    "koipa.active_learning_tick": {"queue": "learning"},
    "koipa.nightly_incremental_retrain_tick": {"queue": "learning"},  # 고객사 야간 증분 재학습 트리거
    "koipa.drift_tick": {"queue": "learning"},
    "koipa.auto_rollback_tick": {"queue": "learning"},
    "koipa.verify_audit_chain_tick": {"queue": "learning"},
    "koipa.deliver_outbox_tick": {"queue": "index"},  # I/O-bound, classify와 격리
    "koipa.ensure_partitions_tick": {"queue": "index"},  # DDL, 경량 I/O
    "koipa.beat_heartbeat_tick": {"queue": "index"},  # 경량 생존 신호
}

# P1-A5: Active Learning 주기 트리거 (Celery beat).
# - 매 30분: 임계 도달 시 URGENT_RETRAIN을 자동 큐잉
# - 매일 03:00: 일별 status 스냅샷 (관측성)
celery_app.conf.beat_schedule = {
    "active-learning-check-every-30min": {
        "task": "koipa.active_learning_tick",
        "schedule": 30 * 60.0,
        "kwargs": {"mode": "auto"},
    },
    "active-learning-daily-snapshot": {
        "task": "koipa.active_learning_tick",
        "schedule": crontab(minute=0, hour=3),
        "kwargs": {"mode": "snapshot"},
    },
    # 야간 무인 재학습 스케줄은 아래에서 **설정으로만** 등록한다(기본 미등록) — beat_schedule
    # 정의부에 두면 플래그와 무관하게 항상 발화한다. 근거는 그 블록 주석 참조.
    # A4 (2026-05-29): 매 15분 — 운영 임베딩 drift 점검.
    # alert=True면 koipa_drift_alert gauge=1 → Grafana 알람 룰 트리거.
    "drift-check-every-15min": {
        "task": "koipa.drift_tick",
        "schedule": 15 * 60.0,
        "kwargs": {"limit": 200, "threshold": 0.5},
    },
    # C-ver 자동 롤백 (2026-06-19): 매 60분 — 활성 모델 라이브 미탐 회귀 점검.
    # settings.auto_rollback_enabled=True일 때만 실제 롤백, 기본은 판정·로깅만(동작 보존).
    "auto-rollback-check-hourly": {
        "task": "koipa.auto_rollback_tick",
        "schedule": 60 * 60.0,
    },
    # 표적 7 (2026-05-29): 매 60초 — webhook outbox 배송.
    # enqueue된 KL 콜백을 실제로 송신. 실패는 outbox 내부에서 지수 백오프, max_attempts 후 DLQ.
    "outbox-deliver-every-60s": {
        "task": "koipa.deliver_outbox_tick",
        "schedule": 60.0,
        "kwargs": {"limit": 50},
    },
    # beat 생존 신호 — 매 60초. beat 스케줄러가 죽으면 게시가 멈춰 koipa_beat_heartbeat_age_seconds
    # 가 증가 → BeatHeartbeatStale 알람. (감사체인검증·drift·파티션 게이지가 beat 사망 시 마지막
    #  안전값에 무음 동결되던 사각지대를 beat-down 자체 알람으로 보완.)
    "beat-heartbeat-every-60s": {
        "task": "koipa.beat_heartbeat_tick",
        "schedule": 60.0,
    },
    # #5 파티션 롤오버 — 매일 02:10, 향후 3개월 월 파티션 보장(IF NOT EXISTS 멱등).
    # baseline은 정적 파티션만 생성하므로 이게 없으면 _default가 비대해진다.
    "ensure-partitions-daily": {
        "task": "koipa.ensure_partitions_tick",
        "schedule": crontab(minute=10, hour=2),
        "kwargs": {"months_ahead": 3},
    },
    # NFR-SEC-01 감사체인 무결성 — 매일 03:30. broken>0이면 koipa_audit_chain_broken_total↑
    # → P0 AuditChainBroken 알람. 과거 row 변조·삭제·재배열 정기 검출.
    "verify-audit-chain-daily": {
        "task": "koipa.verify_audit_chain_tick",
        "schedule": crontab(minute=30, hour=3),
    },
}

# ── 고객사 야간 무인 재학습 — 기본 미등록(수동 트리거) ────────────────────────────
# [정정 2026-08-08] 2026-07 결정으로 매일 02:00 KST 무인 발화하도록 걸려 있었다. 실측이
# 그 전제를 부정한다:
#   ① 이 틱이 태우는 것은 train_classifier_task(spec_kwargs=None) = **기본 TrainSpec**,
#      즉 5에폭 풀 파인튜닝이다. 실측 약 13시간(CPU) → 02:00 에 시작하면 15:00 에 끝난다.
#      "야간에 조용히 끝나 있다"가 성립하지 않고 업무시간 내내 고객사 CPU 를 점유한다.
#      (설계 근거였던 "~1시간/1,000행"과도 어긋난다 — 2,042행이 13시간이었다.)
#   ② 고객사 프로파일 워커는 4GiB 다. 같은 태스크를 그 워커에서 실제로 발화해 보니
#      **40초 만에 OOMKilled**(2.805GiB → kill). enable_training=False 여도 막히지 않는다 —
#      enable_incremental_retrain=True 가 학습 가드를 열어 준다.
#   ③ 새벽 2시에 죽으면 아무도 모른다. 화면에 뜨는 신호가 없다.
# 야간 배치를 넣은 원래 목적은 "고객사 교정을 **반출하지 않고** 현장에서 반영"이다. 그 목적은
# 관리자가 버튼을 눌러도 그대로 달성된다 — 반출은 여전히 0이다. 무인 실행으로 얻는 것은 편의
# 하나인데 대가가 위 셋이다. 그래서 **기본은 등록하지 않고** 수동 트리거로 둔다.
# 기능 자체는 남긴다(태스크·플래그 불변) — 사양이 충분한 회원사는 이 설정만 켜면 복원된다.
if bool(getattr(settings, "enable_nightly_retrain_schedule", False)):
    celery_app.conf.beat_schedule["nightly-incremental-retrain-0200"] = {
        "task": "koipa.nightly_incremental_retrain_tick",
        "schedule": crontab(minute=0, hour=2),
    }

celery_app.conf.timezone = "Asia/Seoul"


# #30: 워커 부팅 시점 구조화(JSON) 로깅 setup — opt-in(KOIPA_LOG_JSON truthy 시에만).
# worker_process_init은 각 워커 프로세스가 부팅될 때 발생하므로 fork된 자식에도 적용된다.
# 기본(미설정)은 setup_logging() 내부에서 no-op이라 기존 로깅을 보존한다. import/연결
# 실패는 모두 흡수 — 로깅 설정이 워커 부팅을 막지 않는다(동작 보존).
try:
    from celery.signals import worker_process_init  # noqa: E402

    @worker_process_init.connect
    def _init_worker_logging(**_kwargs):  # pragma: no cover - 워커 런타임 시그널
        try:
            from koipa.obs.otel import setup_logging
            setup_logging()
        except Exception:  # noqa: BLE001
            pass
except Exception:  # noqa: BLE001
    pass


# [P0 수정] 태스크 등록 — 워커는 `-A koipa.workers.celery_app`로 기동하므로 이 모듈이
# tasks.py를 import 해야 @celery_app.task 데코레이터가 실행돼 11개 태스크가 등록된다.
# (import 누락 시 worker/beat 가 모든 태스크를 NotRegistered로 폐기 — beat 자동화 전멸.)
# tasks.py 는 celery_app 을 import 하므로, celery_app 정의가 끝난 이 위치(파일 말미)에서
# import 해야 순환참조가 안전하게 해소된다.
from koipa.workers import tasks as _tasks  # noqa: E402,F401
