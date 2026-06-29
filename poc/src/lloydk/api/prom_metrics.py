"""Prometheus instrumentation — FastAPI 미들웨어 + /metrics-prom 엔드포인트.

설계:
- PrometheusMiddleware: 요청수·지연·in-progress·에러를 자동 수집
- /api/v1/metrics-prom: prometheus 표준 exposition (text/plain)
- /healthz·/metrics-prom 자체는 스크랩 제외 (셀프 카운트 회피)
- route 라벨은 path template (예: /api/v1/classify/{doc_id}) — 카디널리티 폭증 방지
- active_learning 게이지는 endpoint lazy 갱신 + 백그라운드 주기 refresh 병행
  (METRICS_REFRESH_INTERVAL_SEC, 기본 60초). 테스트 환경(TESTING=1)에서는 자동 비활성.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Awaitable, Callable

from fastapi import APIRouter, FastAPI, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware

_logger = logging.getLogger(__name__)

# 자체 레지스트리 — 테스트 격리 + 다중 import 시 중복 등록 방지.
registry = CollectorRegistry()

REQUEST_COUNT = Counter(
    "lloydk_requests_total",
    "Total HTTP requests received",
    ["method", "route", "status"],
    registry=registry,
)

REQUEST_LATENCY = Histogram(
    "lloydk_request_duration_seconds",
    "Request latency in seconds",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    registry=registry,
)

INPROGRESS = Gauge(
    "lloydk_inprogress_requests",
    "In-progress HTTP requests",
    ["method", "route"],
    registry=registry,
)

EXCEPTIONS = Counter(
    "lloydk_request_exceptions_total",
    "Unhandled exceptions raised by route handlers",
    ["method", "route", "type"],
    registry=registry,
)

# 비즈니스 지표
ACTIVE_LEARNING_TOTAL = Gauge(
    "lloydk_active_learning_pending_total",
    "Total unconsumed corrections in queue",
    registry=registry,
)
ACTIVE_LEARNING_UNDERCLASS = Gauge(
    "lloydk_active_learning_pending_underclass",
    "Unconsumed underclass corrections (security misses)",
    registry=registry,
)

# 학습 가중치 미로드 → rule-fallback-v0 사용 횟수.
# 운영에서 이 값이 올라가면 모델 로딩 실패 또는 경로 미설정 신호.
RULE_FALLBACK_TOTAL = Counter(
    "lloydk_rule_fallback_total",
    "Classifications served by rule-fallback-v0 (model weights not loaded)",
    registry=registry,
)

# classify DB 영속화 실패 횟수 — inference_id가 임시 UUID로 대체됨.
# 운영에서 이 값이 올라가면 DB 연결 장애 또는 데이터 정합성 문제 신호.
CLASSIFY_PERSIST_FAILURE_TOTAL = Counter(
    "lloydk_classify_persist_failure_total",
    "Classify persistence failures (DB unavailable or error, inference_id is ephemeral UUID)",
    # tenant 제거: 'no_tenant' reason 라벨 폐기(단일 고객사 엔진, tenant 스코프 없음).
    ["reason"],  # db_error | unexpected | no_doc | no_level | import_error
    registry=registry,
)

# L2: 임베딩 모델 로드 실패 → fallback 사용 (정확도 저하 위험) 카운트
EMBEDDING_FALLBACK_TOTAL = Counter(
    "lloydk_embedding_fallback_total",
    "Embedding provider fallback to HashEmbedding (model load failed)",
    ["original_provider"],
    registry=registry,
)

# RAG context / fetch 실패 — 벡터스토어·임베더·검색 중 오류 발생 시 증가.
# logger.warning과 함께 운영 대시보드에서 retrieval 장애를 가시화.
RAG_CONTEXT_FAILURE_TOTAL = Counter(
    "lloydk_rag_context_failure_total",
    "RAG context build failures (vectorstore/embedder/search error)",
    ["stage"],  # build_rag_context | fetch_hits
    registry=registry,
)

# §7 (2026-05-29): /answer 단계별 latency — retrieve(쿼리 확장 + ES 검색 + reranker)
# vs synthesize(LLM 답안 합성). 운영 SLO 정의 + §1 batch encode 효과 정량 입증.
ANSWER_PHASE_DURATION = Histogram(
    "lloydk_answer_phase_duration_seconds",
    "POST /answer per-phase latency",
    ["phase"],  # retrieve | synthesize
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=registry,
)

# §4 (2026-05-29): CachedEmbedding 적중률 측정 — Redis 캐시 ROI 추적.
# 운영 진입 시 캐시 적중률 = 임베딩 API 비용 절감 근거.
EMBEDDING_CACHE_HIT_TOTAL = Counter(
    "lloydk_embedding_cache_hit_total",
    "Embedding cache hits (LRU or redis)",
    ["layer"],  # lru | redis | disk
    registry=registry,
)
EMBEDDING_CACHE_MISS_TOTAL = Counter(
    "lloydk_embedding_cache_miss_total",
    "Embedding cache misses (forwarded to underlying embedder)",
    registry=registry,
)

# LLM 토큰·비용 관측성 — LLMUsageService.record()가 모든 호출에서 갱신.
# 라벨 카디널리티: provider/model/purpose는 유한 집합.
# tenant 제거: LLM 비용은 전역 집계(단일 고객사 엔진, tenant 태그 없음).
LLM_TOKENS_TOTAL = Counter(
    "lloydk_llm_tokens_total",
    "LLM tokens consumed",
    ["provider", "model", "purpose", "type"],  # type: input | output
    registry=registry,
)
LLM_COST_USD_TOTAL = Counter(
    "lloydk_llm_cost_usd_total",
    "LLM cost in USD (provider 단가표 기준 추정)",
    ["provider", "model", "purpose"],
    registry=registry,
)
LLM_CALLS_TOTAL = Counter(
    "lloydk_llm_calls_total",
    "LLM API calls",
    ["provider", "model", "purpose", "success"],  # success: true | false
    registry=registry,
)

# A4 (2026-05-29): Drift monitor — P1-B4를 운영 신호로 살리기.
# Celery beat가 drift_tick 호출 → compute_drift() → 본 gauge에 set().
DRIFT_KL_DIVERGENCE = Gauge(
    "lloydk_drift_kl_divergence",
    "KL divergence between train and recent prod embedding cosine distributions",
    registry=registry,
)
DRIFT_COSINE_MEAN = Gauge(
    "lloydk_drift_cosine_mean",
    "Mean cosine similarity of recent prod embeddings vs train centroid",
    registry=registry,
)
DRIFT_COSINE_STD = Gauge(
    "lloydk_drift_cosine_std",
    "Std of cosine similarity of recent prod embeddings",
    registry=registry,
)
DRIFT_ALERT = Gauge(
    "lloydk_drift_alert",
    "1 if KL divergence >= threshold (drift suspected), else 0",
    registry=registry,
)
DRIFT_SAMPLE_SIZE = Gauge(
    "lloydk_drift_sample_size",
    "Number of prod embedding samples included in the latest drift report",
    registry=registry,
)

# ── NEW-H2: alert_rules.yml 이 참조하나 정의가 없던 메트릭 (alert 영구 no-data 해소) ──
# 이 7종은 infra/observability/alert_rules.yml 의 P0~P3 룰이 참조하는데 prom_metrics 에
# 정의가 없어 시계열 자체가 생성되지 않았다 → 알람이 영원히 평가 불가(특히 P0 AuditChainBroken).

# C4 감사 hash-chain 검증 실패 — verify_chain()이 broken>0 검출 시 증가(P0 AuditChainBroken).
AUDIT_CHAIN_BROKEN_TOTAL = Counter(
    "lloydk_audit_chain_broken_total",
    "Audit log hash-chain breaks detected by verify_chain (tamper suspected)",
    registry=registry,
)

# 문서 적재 건수 — PiiMaskingMissRate 의 분모(유입은 있는데 PII 마스킹 0이면 masker 누락 의심).
DOCUMENTS_INGESTED_TOTAL = Counter(
    "lloydk_documents_ingested_total",
    "Documents ingested via DocumentIngestionService.ingest()",
    registry=registry,
)

# PII 마스킹 건수(타입별) — mask_pii()가 타입별로 증가. rrn 등 핵심 타입 알람.
PII_MASKED_TOTAL = Counter(
    "lloydk_pii_masked_total",
    "PII tokens masked, by type (rrn, email, phone_mobile, ...)",
    ["pii_type"],
    registry=registry,
)

# Webhook outbox DLQ 진입 — deliver_once()가 max_attempts 소진 시 증가(OutboxDlqGrowing).
OUTBOX_DLQ_TOTAL = Counter(
    "lloydk_outbox_dlq_total",
    "Webhook outbox messages moved to DLQ (max attempts exhausted)",
    registry=registry,
)

# Celery 큐 적체(큐별) — /metrics-prom 스크랩 시 redis LLEN 으로 best-effort 갱신(CeleryQueueBacklog).
CELERY_QUEUE_LENGTH = Gauge(
    "lloydk_celery_queue_length",
    "Pending tasks per Celery queue (redis LLEN, best-effort at scrape time)",
    ["queue"],
    registry=registry,
)

# 실시간 분류 정확도 신호용 — FnrSpikeOverall = 100*(1 - correct/total).
# ⚠️ '정탐' 판정에는 정답(ground truth)이 필요하나 서빙 시점엔 알 수 없다. 두 카운터를
# 함께 증가시키지 않으면(예: total만) expr 가 100%로 거짓 발화하므로, 정답이 확정되는
# 경로(corrections/confirm)에서만 동반 증가시켜야 한다. 현재는 **정의만**(둘 다 0 → expr
# 가 0/0=NaN → 무발화, 안전). 운영 실시간 미탐 신호는 이미 배선된
# lloydk_active_learning_pending_underclass(ActiveLearningUrgent)를 1차로 사용한다.
CLASSIFY_TOTAL = Counter(
    "lloydk_classify_total",
    "Classifications with a later-known ground truth (denominator for live FNR; not yet wired)",
    registry=registry,
)
CLASSIFY_CORRECT_TOTAL = Counter(
    "lloydk_classify_correct_total",
    "Ground-truth-correct classifications (numerator for live FNR; not yet wired)",
    registry=registry,
)

# 교정(correction) 발생률 — direction별(confirm/underclass/overclass/lateral) 실시간 카운터.
# underclass = 모델이 비밀을 낮게 본 것을 사람이 올린 것(보안 미탐 신호) → 자동확정 품질·
# 죽음의 나선(러버스탬프·운영 정확도 저하) 모니터링의 핵심 지표. add_correction(모든 교정의
# 단일 choke point)에서 best-effort 증가. underclass 율 급증 = 모델 품질 저하/오염 경보 근거.
CORRECTION_TOTAL = Counter(
    "lloydk_corrections_total",
    "Corrections recorded by human review, by direction (overturning/confirming a classification)",
    ["direction"],  # confirm | underclass | overclass | lateral
    registry=registry,
)

# Kill-gate 발동 여부 — 1이면 죽음의 나선/품질붕괴 조건(TS/S1 미탐 · 검수 과부하 · overturn율)
# 중 하나 이상 충족(경보·비파괴, 자동 중단 없음). run_kill_gate_check()가 주기 갱신.
KILL_GATE_TRIPPED = Gauge(
    "lloydk_kill_gate_tripped",
    "1 if kill-gate tripped (TS/S1 miss / review fatigue / overturn-rate); else 0 (alert-only)",
    registry=registry,
)

# 관리자 검수 소요시간 — classification.classified_at → correction.corrected_at (초).
# 처리시간 단축(자동확정·선례 효과) / 검수 병목 모니터. add_correction에서 best-effort 관측.
ADMIN_REVIEW_SECONDS = Histogram(
    "lloydk_admin_review_seconds",
    "Seconds from classification to human correction (admin review handling time)",
    buckets=(60, 300, 900, 1800, 3600, 21600, 86400, 259200, 604800),
    registry=registry,
)
# 동일 문서 재등장 재검수 — 같은 doc에 이전 교정이 있던 재교정(문서 churn). 선례·exact-match
# 재사용이 효과적이면 시간이 갈수록 감소해야 하는 지표(재검수 부담 절감 측정).
SAME_DOC_RESURFACE_TOTAL = Counter(
    "lloydk_same_doc_resurface_total",
    "Corrections on a document that was already corrected before (recurring re-review)",
    registry=registry,
)

# ── 번들 A: 메타데이터 게이트 가시성 ──────────────────────────────────────────
# metadata_floor_enabled / source_prior_enabled 를 켜도 입력 메타데이터(보안표시·접근범위·
# 출처)가 실제로 들어오지 않으면 게이트는 **silent no-op** 이다(게이트를 켰는데 아무 일도
# 안 일어나는 무음 실패). 아래 3종으로 (1) 게이트 실제 발동 횟수와 (2) 분류 입력의 메타데이터
# 존재율을 함께 노출해, 켜기 *전에* '켜면 효과가 있을지'를 먼저 보이게 한다.
# 모두 서빙 choke point(ClassifyService.classify)에서만 증가 — 평가(serving_eval)는 제외.

# 메타데이터 floor 게이트 발동(ICD §4.2/§4.4): security_marking 상향 floor / access_scope 충돌.
METADATA_FLOOR_APPLIED_TOTAL = Counter(
    "lloydk_metadata_floor_applied_total",
    "ICD metadata-floor gate firings (security_marking raised grade / access_scope conflict routed to review)",
    ["action"],  # raised | access_conflict
    registry=registry,
)
# source-prior(비공지성) 게이트 발동: 공개출처 등급 cap / cap-conflict 검수 라우팅.
SOURCE_PRIOR_APPLIED_TOTAL = Counter(
    "lloydk_source_prior_applied_total",
    "Source-prior gate firings (public-source grade cap / cap-conflict routed to review)",
    ["action"],  # capped | cap_conflict
    registry=registry,
)
# 분류 입력에 ICD 게이트용 메타데이터 필드가 실제로 존재했는지 — 게이트 활성 여부와 무관하게
# 분류 1건당 증가. field='none' = 게이트 입력 메타데이터가 하나도 없었음(coverage 분모).
# coverage = 1 - (rate(field="none") / sum(rate(*)))  →  ICD 메타가 실제로 들어오는지 가시화.
CLASSIFY_METADATA_PRESENT_TOTAL = Counter(
    "lloydk_classify_metadata_present_total",
    "Per-classification presence of ICD gate-input metadata fields ('none' = no gate-input metadata present)",
    ["field"],  # security_marking | access_scope | source | none
    registry=registry,
)

# ── 번들 B: 고등급 이중검토 보류 가시화 ───────────────────────────────────────
# high_grade_dual_review ON 시 1인 동의 고등급 교정은 status='needs_second_review'로 DB에
# 무음 hold 된다(운영자가 능동 쿼리해야만 발견). 등급별 보류 건수를 게이지로 노출해
# '안 보이는 검수 보류큐'를 가시화한다(주기 refresh가 best-effort 갱신, DB 미가용 시 미설정).
ESCALATION_HELD = Gauge(
    "lloydk_escalation_held",
    "Classifications held in needs_second_review (high-grade dual-review pending), by grade",
    ["grade"],
    registry=registry,
)

# ── 번들 D: locked_gold_eval readiness 가시화 ─────────────────────────────────
# deploy gate는 자동활성 시 등급별 min_locked_per_grade 충족을 요구한다(죽음의 나선 #4).
# '얼마나 쌓였고 배포 가능한지'를 운영자에게 직접 노출 — ready(0/1) + 등급별 보유 수.
LOCKED_EVAL_READY = Gauge(
    "lloydk_locked_eval_ready",
    "1 if locked_gold_eval has >= min_per_grade for every grade (deploy-ready), else 0",
    registry=registry,
)
LOCKED_EVAL_PER_GRADE = Gauge(
    "lloydk_locked_eval_per_grade",
    "Count of locked_gold_eval (human-signed) records per grade",
    ["grade"],
    registry=registry,
)

# ── 번들 E: kill-gate 안전브레이크 발동 ───────────────────────────────────────
# kill_gate_suppress_autoconfirm ON + kill-gate tripped 동안 고등급 자동확정이 needs_review로
# 억제된 횟수. 0 이상이면 '브레이크가 실제로 걸리고 있다'는 운영 신호(조건 해소 시 멈춤).
KILL_GATE_AUTOCONFIRM_SUPPRESSED_TOTAL = Counter(
    "lloydk_kill_gate_autoconfirm_suppressed_total",
    "High-grade auto-confirms downgraded to needs_review because kill-gate brake was active",
    ["grade"],
    registry=registry,
)

# [QW] verified_label(사람검수 완료) 감사 기록 실패 — 결정론 경로의 컴플라이언스 감사가 best-effort라
# DB 실패 시 무음 누락된다. 이 값이 0보다 크면 감사 누락 발생 신호(NFR-SEC-01 추적).
CLASSIFY_VERIFIED_LABEL_AUDIT_SKIP_TOTAL = Counter(
    "lloydk_classify_verified_label_audit_skip_total",
    "verified_label audit-trail writes skipped (best-effort failure) — compliance audit gap signal",
    registry=registry,
)

# 스크랩 제외 경로 — 셀프 카운트 회피 + 노이즈 차단
_EXCLUDED = {
    "/api/v1/metrics-prom",
    "/api/v1/healthz",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/openapi.json",
}


def _route_template(request: Request) -> str:
    """카디널리티 안정 — path template 우선, 라우팅 실패 시 'unknown'으로 collapse.

    O1: raw path 노출하면 /api/v1/classify/{uuid}처럼 ID 포함된 경로가
    수많은 라벨 값을 만들어 Prometheus storage 폭증 위험. 매칭 실패는 unknown으로.
    """
    route = request.scope.get("route")
    if route is not None and getattr(route, "path", None):
        return route.path
    # 라우팅 실패 시 — 카디널리티 폭증 차단
    return "unknown"


class PrometheusMiddleware(BaseHTTPMiddleware):
    """요청별 메트릭 자동 수집. AuditMiddleware보다 안쪽에 두면 안 됨 (라우팅 끝나야 route_template 확보)."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if path in _EXCLUDED:
            return await call_next(request)

        method = request.method
        start = time.perf_counter()
        # O2: route_label은 측정 진입 시점에 unknown으로 초기화. 라우팅 후 갱신.
        # INPROGRESS는 진입 즉시 inc해야 finally에서 짝맞는 dec 가능 (언더플로우 차단).
        route_label = "unknown"
        INPROGRESS.labels(method=method, route=route_label).inc()
        inprogress_inc = True

        try:
            response = await call_next(request)
            route_label = _route_template(request)
            status = str(response.status_code)
            REQUEST_COUNT.labels(method=method, route=route_label, status=status).inc()
            return response
        except Exception as exc:  # noqa: BLE001
            route_label = _route_template(request)
            EXCEPTIONS.labels(method=method, route=route_label, type=type(exc).__name__).inc()
            REQUEST_COUNT.labels(method=method, route=route_label, status="500").inc()
            raise
        finally:
            elapsed = time.perf_counter() - start
            REQUEST_LATENCY.labels(method=method, route=route_label).observe(elapsed)
            # 짝맞춰 dec — O2: 위에서 unknown 라벨로 inc했으므로 동일 라벨로 dec 가능
            if inprogress_inc:
                try:
                    INPROGRESS.labels(method=method, route="unknown").dec()
                except Exception as exc:  # noqa: BLE001
                    # K3: 빈 swallow 제거, warning 기록
                    _logger.warning("INPROGRESS dec failed: %s", exc)


# ============================================================
# /metrics-prom router
# ============================================================

router = APIRouter()


# Celery 큐명 — celery_app.task_routes 와 정합(미구독/미정의 큐는 LLEN 0으로 무해).
_CELERY_QUEUES = ("classify", "synthesis", "learning", "index", "celery")


def _refresh_celery_queue_gauges() -> None:
    """Celery 큐 길이를 redis LLEN 으로 best-effort 갱신(NEW-H2 CeleryQueueBacklog).

    celery(redis 브로커)는 큐를 큐명 list 키로 보관하므로 LLEN 으로 적체를 읽는다.
    redis 미가용/미설치면 skip(게이지 미설정 → alert no-data → 거짓 발화 없음). API
    프로세스에서 브로커를 직접 조회하므로 워커-프로세스 메트릭 분산 문제와 무관.
    테스트(TESTING) 환경에서는 redis 연결 시도 자체를 건너뛴다(CI 지연·플래키 방지).
    """
    if _is_testing():
        return
    try:
        import redis  # noqa: PLC0415

        from lloydk.config import settings  # noqa: PLC0415

        client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        for q in _CELERY_QUEUES:
            try:
                CELERY_QUEUE_LENGTH.labels(queue=q).set(float(client.llen(q)))
            except Exception:  # noqa: BLE001
                continue
    except Exception as exc:  # noqa: BLE001
        _logger.debug("celery queue gauge refresh skipped: %s", exc)


def _refresh_business_gauges() -> None:
    """active_learning gauge를 호출 시점에 lazy 갱신.

    실패해도 메트릭 노출 자체는 중단되지 않게 best-effort.
    K3: 실패 시 debug 로깅 — DB 미가용은 정상 상태(테스트)일 수 있어 warning 대신 debug.
    """
    try:
        from lloydk.modules.m6_evaluation.active_learning import (
            evaluate_retraining_need,
        )

        status = evaluate_retraining_need()
        ACTIVE_LEARNING_TOTAL.set(status.unconsumed_total)
        ACTIVE_LEARNING_UNDERCLASS.set(status.pending_underclass)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("business gauge refresh skipped: %s", exc)
    # Kill-gate 모니터(경보만) 갱신 — 독립 try, best-effort.
    try:
        from lloydk.modules.m6_evaluation.kill_gate import run_kill_gate_check  # noqa: PLC0415

        run_kill_gate_check()
    except Exception as exc:  # noqa: BLE001
        _logger.debug("kill-gate check skipped: %s", exc)
    # [번들 B] 이중검토 보류(needs_second_review) 등급별 게이지 — 독립 try, best-effort.
    _refresh_escalation_held()
    # [번들 D] locked_gold_eval readiness 게이지 — 독립 try, best-effort.
    _refresh_locked_readiness()
    # NEW-H2: Celery 큐 적체도 동반 갱신(best-effort, 독립 try).
    _refresh_celery_queue_gauges()


def _refresh_escalation_held() -> None:
    """[번들 B] needs_second_review 보류 건수를 등급별 게이지에 노출(best-effort).

    사라진 보류가 stale로 남지 않게, 알려진 등급은 0으로 초기화 후 현재 카운트를 set.
    """
    try:
        from lloydk.services.confirm_service import (  # noqa: PLC0415
            count_needs_second_review_by_grade,
        )

        counts = count_needs_second_review_by_grade()
        for grade, n in counts.items():
            ESCALATION_HELD.labels(grade=grade).set(float(n))
    except Exception as exc:  # noqa: BLE001
        _logger.debug("escalation-held gauge refresh skipped: %s", exc)


def _refresh_locked_readiness() -> None:
    """[번들 D] locked_gold_eval readiness(ready·등급별 보유)를 게이지에 노출(best-effort)."""
    try:
        from lloydk.modules.m6_evaluation.locked_readiness import (  # noqa: PLC0415
            locked_eval_readiness,
        )

        summary = locked_eval_readiness()
        LOCKED_EVAL_READY.set(1.0 if summary.get("ready") else 0.0)
        for grade, n in (summary.get("per_grade") or {}).items():
            LOCKED_EVAL_PER_GRADE.labels(grade=grade).set(float(n))
    except Exception as exc:  # noqa: BLE001
        _logger.debug("locked-readiness gauge refresh skipped: %s", exc)


@router.get("/metrics-prom", include_in_schema=False)
def metrics_prom() -> Response:
    """Prometheus exposition. /api/v1 접두 아래 등록되므로 실제 경로는
    /api/v1/metrics-prom (OpenAPI /metrics/* 와 충돌하지 않게 -prom 접미).
    """
    _refresh_business_gauges()
    data = generate_latest(registry)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


# ============================================================
# Background refresh — active_learning gauge 주기 갱신
# ============================================================
#
# /metrics-prom 호출이 없으면 게이지가 stale 상태로 남는 문제 해결.
# Grafana/Prometheus 폴링이 30~60초인 환경에서도 항상 최신 값 노출.


def _resolve_refresh_interval() -> float:
    raw = os.getenv("METRICS_REFRESH_INTERVAL_SEC", "60").strip()
    try:
        v = float(raw)
        return v if v > 0 else 0.0
    except ValueError:
        return 60.0


def _is_testing() -> bool:
    """pytest / TestClient 환경 자동 감지 — background task 비활성."""
    if os.getenv("TESTING", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    return False


async def _gauge_refresh_loop(interval: float) -> None:
    """무한 루프 — interval 마다 _refresh_business_gauges 호출.

    cancel 받으면 즉시 종료. 예외는 삼키고 다음 사이클로 진행.
    """
    while True:
        try:
            _refresh_business_gauges()
        except Exception as exc:  # noqa: BLE001
            _logger.warning("gauge refresh failed: %s", exc)
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break


def register_background_refresh(app: FastAPI) -> None:
    """FastAPI startup/shutdown 훅 등록 — 메인 lifespan 외부에서 호출.

    테스트 환경(TESTING=1 또는 pytest 실행 중)에서는 task 자체를 만들지 않는다.
    interval <= 0 이면 사용자가 명시적으로 disable 한 것으로 간주.
    """
    if _is_testing():
        return

    interval = _resolve_refresh_interval()
    if interval <= 0:
        return

    async def _start_refresh_task() -> None:  # noqa: D401
        loop_task = asyncio.create_task(_gauge_refresh_loop(interval))
        app.state._prom_refresh_task = loop_task

    async def _stop_refresh_task() -> None:  # noqa: D401
        task: asyncio.Task | None = getattr(app.state, "_prom_refresh_task", None)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    app.router.on_startup.append(_start_refresh_task)
    app.router.on_shutdown.append(_stop_refresh_task)
