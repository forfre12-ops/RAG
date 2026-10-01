import os

from celery import Celery
from celery.schedules import crontab

from koipa.config import settings


def _autodetect_worker_scaling() -> tuple[int, int, str]:
    """고객사마다 CPU 가 다른데 동시성·스레드를 자동으로 못 맞추던 문제(2026-09-30).

    [2026-09-30] docker-compose 기본값(WORKER_CPU_LIMIT=4·동시성=2·프로세스당
    스레드=4 그대로)이 그 자체로 2배 초과구독이었다([[worker-cpu-oversubscription-fixed]]
    — 이 파일 수정 이력 참조). 그건 "기본값끼리 서로 안 맞다"를 고친 것이고, 이건
    "고객사 서버 실제 코어 수를 사람이 안 넣어도 되게" 한 걸음 더 간 것이다.

    사람이 CELERY_WORKER_CONCURRENCY 를 **명시적으로** 환경변수에 넣었으면 그 값을
    그대로 존중한다(자동 계산은 아무 설정도 없을 때만). 자동 계산 기준:
      - CPU: resource_detect.effective_cpu_count() (cgroup 한도 우선, 없으면 호스트).
      - 메모리: 워커 프로세스마다 분류 모델을 독립적으로 올린다(prefork — fork 후에도
        각자 가중치를 들고 있다) — 코어 수만 보고 동시성을 올리면 메모리가 먼저
        바닥날 수 있다. 실측(2026-09-30, `docker stats`): 워커 컨테이너 전체(모델
        로드+요청 처리 뒤) 840MiB. 여유를 두어 프로세스당 1.5GiB 로 잡는다 — 기본
        1개 몫(~1GiB)을 빼고 나머지를 프로세스당 1.5GiB 로 나눈 값과 CPU 기준 중
        작은 쪽을 쓴다(2GiB로 과대평가하면 4GiB 컨테이너에서 동시성이 1로 떨어져
        완전 직렬이 된다).
      - 상한 8 — 더 큰 서버에서의 검증 없이 무제한 확장하지 않는다.
      - 스레드/프로세스 = 유효 CPU ÷ 동시성 (내림, 최소 1) — 둘을 곱해도 CPU 한도를
        넘지 않는다(이게 바로 위 오버서브스크립션 버그가 어기던 불변식).

    반환: (concurrency, threads_per_process, 근거 한 줄) — 근거는 기동 로그에 남겨
    "왜 이 숫자인지"를 지재원·고객사 설치 로그에서 바로 보이게 한다.
    """
    from koipa.resource_detect import detect_cpu_quota, detect_memory_limit_gb, effective_cpu_count

    host_cpus = os.cpu_count() or 1
    eff_cpu = effective_cpu_count(host_cpus)
    mem_gb = detect_memory_limit_gb()

    by_cpu = max(1, eff_cpu)
    if mem_gb is not None:
        by_mem = max(1, int((mem_gb - 1.0) // 1.5))
        concurrency = max(1, min(by_cpu, by_mem, 8))
    else:
        concurrency = max(1, min(by_cpu, 8))
    threads = max(1, eff_cpu // concurrency)
    quota = detect_cpu_quota()
    reason = (
        f"auto: cgroup_cpu={quota if quota else 'N/A'} host_cpus={host_cpus} "
        f"eff_cpu={eff_cpu} mem_limit_gb={mem_gb if mem_gb else 'N/A'} "
        f"-> concurrency={concurrency} threads/process={threads}"
    )
    return concurrency, threads, reason


def _resolve_worker_scaling() -> tuple[int, int]:
    """CELERY_WORKER_CONCURRENCY 를 사람이 명시했으면 그대로, 아니면 자동 계산.

    OMP_NUM_THREADS/MKL_NUM_THREADS 는 torch 가 import 되기 전에 환경변수로 있어야
    적용된다 — tasks.py 는 torch 를 함수 안에서 지연 임포트하므로(이 파일 맨 아래
    `from koipa.workers import tasks` 시점에도 아직 안 불러짐) 여기서 os.environ 에
    쓰면 시점상 안전하다.
    """
    import logging

    log = logging.getLogger(__name__)
    if "CELERY_WORKER_CONCURRENCY" in os.environ:
        concurrency = settings.celery_worker_concurrency
        threads = int(os.environ.get("OMP_NUM_THREADS") or os.environ.get("MKL_NUM_THREADS") or concurrency)
        log.info("worker scaling: 명시 설정 사용 concurrency=%d threads=%s (자동계산 안 함)", concurrency, threads)
        return concurrency, threads
    try:
        concurrency, threads, reason = _autodetect_worker_scaling()
    except Exception as exc:  # noqa: BLE001 — 감지 실패는 기존 기본값으로(동작 보존)
        log.warning("worker scaling 자동감지 실패 — 기존 기본값 사용: %s: %s", type(exc).__name__, exc)
        return settings.celery_worker_concurrency, settings.celery_worker_concurrency
    log.info("worker scaling 자동감지: %s", reason)
    if "OMP_NUM_THREADS" not in os.environ:
        os.environ["OMP_NUM_THREADS"] = str(threads)
    if "MKL_NUM_THREADS" not in os.environ:
        os.environ["MKL_NUM_THREADS"] = str(threads)
    return concurrency, threads


_AUTO_CONCURRENCY, _AUTO_THREADS = _resolve_worker_scaling()

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
# [2026-08-28] 전역 True 는 워커 소실(cgroup OOM SIGKILL) 시 메시지를 큐 **머리**로 되돌린다.
# 이 되돌림은 retry 가 아니라 redelivery 라 max_retries 가 세지 않는다 — 상한이 없다.
# 메모리를 넘겨 죽은 작업이 되살아나 또 죽는 순환이 실측됐다(182 서버 재학습 워커).
# False 로 두면 소실된 작업은 유실되지만, 유실은 로그·지표에 남고 사람이 다시 걸 수 있다.
# 무한 순환은 워커를 통째로 못 쓰게 만든다 — 둘 중에는 유실이 낫다.
celery_app.conf.task_reject_on_worker_lost = False
celery_app.conf.worker_prefetch_multiplier = 1
# prefork 기본 동시성 = 호스트 코어 수. 컨테이너 한도를 못 보므로 명시한다(config 주석 참조).
# [2026-09-30] CELERY_WORKER_CONCURRENCY 를 사람이 안 넣으면 cgroup CPU·메모리 한도로
# 자동 계산한다(_resolve_worker_scaling, 위) — 고객사마다 서버 사양이 달라도 설치
# 스크립트가 숫자를 안 넣어도 되게. 명시하면 그 값을 그대로 쓴다(자동계산 안 함).
celery_app.conf.worker_concurrency = _AUTO_CONCURRENCY

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
    # [실측 2026-09-30] classify_batch 는 신설 당시(이 주석이 생기기 전) 이 표에 없어 전역
    # 기본값(900/1200초)을 그대로 물려받았다 — 고객사가 "1000건까지 되냐"고 물어 재보니,
    # 건당(이 PC·배포모델·캐시 후 steady state) 0.233초(806자 1청크 샘플) → 1000건 233초로는
    # 안전하지만, 문서 하나가 과대문서 가드(analyze_sync_max_chunks=34청크) 바로 아래 크기면
    # 건당 최대 약 34청크×0.24초/청크≈8.2초 — 느린 회원사 CPU(2코어, 단건 경로 실측 기준
    # 약 3배 느림)까지 고려하면 건당 최대 ≈24.5초, 1000건이면 최악 ≈6.8시간(24,480초)이다.
    # **건별 isolation+retry 로 이미 처리된 결과는 task 자체가 시간제한에 걸려 죽으면
    # 전부 버려진다**(배치는 끝에 한 번만 JobStore 에 쓴다) — 상한을 넉넉히 둔다.
    "koipa.classify_batch": {
        "soft_time_limit": max(settings.celery_task_soft_time_limit, 25200),
        "time_limit": max(settings.celery_task_time_limit, 28800),
    },
    "koipa.synthesize_batch": {
        "soft_time_limit": min(settings.celery_task_soft_time_limit, 300),
        "time_limit": min(settings.celery_task_time_limit, 420),
    },
    "koipa.golden_build": {
        "soft_time_limit": max(settings.celery_task_soft_time_limit, 900),
        "time_limit": max(settings.celery_task_time_limit, 1200),
    },
    # [실측 2026-08-08] 학습 태스크 시간 하한 — 30분으로는 완주하지 못한다.
    # 실서버(16vCPU·GPU 없음)에서 정본 학습셋(2,042행) 풀 파인튜닝이 29분 30초에
    # SoftTimeLimitExceeded 로 잘렸다(메모리 정상·OOM 0). 지재원 드리프트 자동재학습과
    # 고객사 야간 증분 재학습이 모두 이 태스크를 태우므로 무인 재학습이 구조적으로 실패했다.
    # 하한은 실측으로 잡는다 — 같은 서버 로그 `51/1280 [33:33<12:42:44, 37.24s/it]` = 약 13시간
    # (epochs=5 · batch 8 · max_seq 512 → 1,280스텝).
    # ⚠ 13시간은 보편값이 아니다. 그 서버는 QEMU Virtual CPU 라 AVX/FMA 가 없다. AVX-512 CPU 면
    #   약 6배 빨라 2시간 남짓(≈1시간/1,000행)이고 GPU 면 더 짧다. 하한은 가장 느린 쪽 기준이다.
    "koipa.train_classifier": {
        "soft_time_limit": max(settings.celery_task_soft_time_limit, 64800),
        "time_limit": max(settings.celery_task_time_limit, 68400),
    },
    # [2026-09-25] 규정 색인은 (표시 대상 조항 + 서두 아닌 문장) × 임베딩 시간이다. 실측(KURE-v1, CPU 8스레드,
    # scripts/measure_regulation_runtime.py): 시연 규정 203건에 56.4초(건당 0.278초). 문장 상한
    # (regulation_max_sentences=3,000)까지 외삽하면 약 14분 — 전역 한도(900/1200초)에 걸리는 크기다.
    # 외삽이라 느린 CPU·조항 임베딩 몫을 넉넉히 두어 하한을 1시간으로 둔다(상한 규모 규정은 재지 않았다).
    # ⚠ 위 학습 태스크와 같이 시간제한은 발행자 값이 메시지에 실려 가므로 api 와 worker 를 함께 재기동한다.
    "koipa.index_regulation": {
        "soft_time_limit": max(settings.celery_task_soft_time_limit, 3300),
        "time_limit": max(settings.celery_task_time_limit, 3600),
    },
}

# P1-D3: 큐 분리 — classify/index/synthesis/learning.
# 워커는 `-Q classify,index,synthesis,learning,celery`로 모든 큐를 구독해야 한다
# (docker-compose worker / Makefile worker 참조). 미구독 큐의 작업은 소비되지 않는다.
# 주의: 여기 라우팅하는 task name은 반드시 tasks.py에 실제 정의돼 있어야 한다
# (미정의 name을 라우팅하면 호출 시 NotRegistered). 'index' 큐는 deliver_outbox_tick이 사용.
celery_app.conf.task_routes = {
    "koipa.classify_async": {"queue": "classify"},
    # [2026-09-30] classify_batch 신설 당시 빠져 있었다 — 라우팅이 없으면 기본 'celery' 큐로
    # 가 classify_async 와 다른 큐에서 돈다(둘 다 같은 워커가 구독하므로 지금 당장 동작은
    # 하지만, 의도치 않게 분리돼 있었다). 같은 family 라 같은 큐로 맞춘다.
    "koipa.classify_batch": {"queue": "classify"},
    "koipa.synthesize_batch": {"queue": "synthesis"},
    "koipa.train_classifier": {"queue": "learning"},
    "koipa.golden_build": {"queue": "learning"},  # 빌더 — train과 자원 풀 공유
    "koipa.active_learning_tick": {"queue": "learning"},
    "koipa.nightly_incremental_retrain_tick": {"queue": "learning"},  # 고객사 야간 증분 재학습 트리거
    "koipa.drift_tick": {"queue": "learning"},
    "koipa.auto_rollback_tick": {"queue": "learning"},
    "koipa.verify_audit_chain_tick": {"queue": "learning"},
    # [2026-09-09] 문서 벡터 색인 — 유사 문서 조회의 재료. classify 와 격리하는 것이
    #   요점이다(임베딩은 청크당 0.51초라 분류 큐에 섞이면 검수 화면이 밀린다).
    "koipa.index_document_vector": {"queue": "index"},
    # [2026-09-25] 규정 참고 표시 — 규정 문장 임베딩. 같은 index 큐를 쓴다(새 큐 이름은 워커 기동 명령 넷을 고쳐야 한다).
    "koipa.index_regulation": {"queue": "index"},
    "koipa.deliver_outbox_tick": {"queue": "index"},  # I/O-bound, classify와 격리
    "koipa.ensure_partitions_tick": {"queue": "index"},  # DDL, 경량 I/O
    "koipa.retention_purge_tick": {"queue": "index"},  # DELETE, 경량 I/O
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
    # 보존기간 삭제 — 매일 02:40(파티션 롤오버 30분 뒤). 기본 OFF 라 켜기 전엔 no-op.
    # 오래된 파티션을 떼는 코드가 없어 감사로그·LLM 사용량이 무한히 자라는 것을 막는다.
    "retention-purge-daily": {
        "task": "koipa.retention_purge_tick",
        "schedule": crontab(minute=40, hour=2),
    },
    # NFR-SEC-01 감사체인 무결성 — 매일 03:30. broken>0이면 koipa_audit_chain_broken_total↑
    # → P0 AuditChainBroken 알람. 과거 row 변조·삭제·재배열 정기 검출.
    "verify-audit-chain-daily": {
        "task": "koipa.verify_audit_chain_tick",
        "schedule": crontab(minute=30, hour=3),
    },
}

# ── 고객사 야간 무인 재학습 — 기본 미등록(수동 트리거) ────────────────────────────
# 무인 자동발화를 기본으로 켜지 않는 이유(실측 2026-08-08):
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
