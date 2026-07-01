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
    ["layer"],  # lru | redis (cache_layer.py가 방출하는 실 계층 — 'disk'층은 미구현이라 제거)
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

# 감사 행에 payload_hash가 NULL/빈값 — 체인 break와 별개의 무결성 신호(P0). nil hash는
# 감사 미들웨어를 거치지 않은 직접 DB 삽입(우회) 또는 데이터 손실을 뜻한다. verify_chain의
# break 카운트는 nil 근원 행을 무음 anchor화할 수 있어, nil 행을 따로 스캔해 노출한다.
AUDIT_CHAIN_NIL_HASH_TOTAL = Counter(
    "lloydk_audit_chain_nil_hash_total",
    "Audit log rows with NULL/empty payload_hash (middleware bypass or data loss; integrity signal)",
    registry=registry,
)

# idempotency 저장소가 redis 미가용으로 in-memory(프로세스-로컬)로 폴백한 횟수. 멀티워커에선
# 워커 간 멱등성이 깨져 중복 처리 위험 — 0보다 크면 운영(멀티워커)에서 redis 미배선 신호.
IDEMPOTENCY_BACKEND_FALLBACK_TOTAL = Counter(
    "lloydk_idempotency_backend_fallback_total",
    "Idempotency store fell back to process-local in-memory (redis unavailable) — multi-worker risk",
    registry=registry,
)

# 문서 적재 건수 — PiiMaskingMissRate 의 분모(유입은 있는데 PII 마스킹 0이면 masker 누락 의심).
DOCUMENTS_INGESTED_TOTAL = Counter(
    "lloydk_documents_ingested_total",
    "Documents ingested via DocumentIngestionService.ingest()",
    registry=registry,
)

# [P0#3] 추출 열화(degrade) — 저품질/OCR/추출오류/빈본문으로 검수 라우팅된 적재 건수(reason별).
# 깨끗한 텍스트와 OCR/스캔본이 동일 입력으로 분류기에 진입하던 무음 오분류를 가시화한다. reason=
# empty(본문0)·extract_error·ocr·low_quality. rate(*{reason!=empty})/documents_ingested = 검수 라우팅율.
EXTRACTION_DEGRADED_TOTAL = Counter(
    "lloydk_extraction_degraded_total",
    "Ingested documents flagged degraded (routed to review): low quality / OCR / extract error / empty, by reason",
    ["reason"],  # empty | extract_error | ocr | low_quality
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

# ── [P0 관측성] 워커-프로세스 신호를 API 레지스트리에서 재파생하는 현재-상태 게이지 ──
# 배경: 아래 신호들은 워커(Celery beat)에서만 계산·기록되는데 Prometheus 는 API(job=
# lloydk-api) 프로세스만 스크랩한다 → 워커 레지스트리의 카운터/게이지는 API /metrics-prom 에
# 영원히 0/미변경으로 노출(거짓 all-clear). 해결(_refresh_celery_queue_gauges 선례와 동일
# 원칙): API 프로세스가 **공유 진실원천**(감사=Postgres 검증결과를 워커가 Redis 게시, outbox=
# Redis stats, drift=워커가 Redis 게시)을 조회해 현재 상태를 게이지로 재노출한다. 카운터가
# 아니라 게이지인 이유: 크로스-프로세스에서 단조 카운터를 재구성할 수 없고, 알람이 필요로 하는
# 것은 '지금 깨졌는가'(현재 상태)이기 때문. broken/nil/dlq 는 기본 0(=안전) → 첫 신호 전까지
# 거짓 경보 없음. integrity_ok 는 정보용(알람 미사용)이라 첫 tick 전 0(=unknown)이어도 무해.
# 이름 주의: 게이지에 _rows 접미 — Counter "lloydk_audit_chain_broken_total"/"..._nil_hash_total"
# 이 prometheus_client 에서 base name("...broken"/"...nil_hash")으로 등록돼 게이지와 충돌하기 때문.
AUDIT_CHAIN_BROKEN = Gauge(
    "lloydk_audit_chain_broken_rows",
    "Current audit hash-chain breaks (re-derived by API from worker tick via Redis; 0=intact)",
    registry=registry,
)
AUDIT_CHAIN_NIL_HASH = Gauge(
    "lloydk_audit_chain_nil_hash_rows",
    "Current audit rows with NULL/empty payload_hash (re-derived by API from worker tick)",
    registry=registry,
)
AUDIT_CHAIN_INTEGRITY_OK = Gauge(
    "lloydk_audit_chain_integrity_ok",
    "1 if audit chain has no breaks and no nil-hash rows (info gauge; 0 before first tick)",
    registry=registry,
)
OUTBOX_DLQ_PENDING = Gauge(
    "lloydk_outbox_dlq_pending",
    "Current webhook outbox DLQ backlog (re-derived by API from shared Redis; 0=empty)",
    registry=registry,
)
OUTBOX_PENDING = Gauge(
    "lloydk_outbox_pending",
    "Current webhook outbox pending backlog (re-derived by API from shared Redis)",
    registry=registry,
)
# 월별 파티션 롤오버(ensure_partitions_tick, 워커 beat) 실패 건수 — 롤오버 지연 시 _default
# 비대화·프루닝 무력화 무음열화. outbox/audit 은 신호가 있는데 파티션만 없던 갭. 카운터가
# 아니라 게이지인 이유: 워커 카운터는 API 무가시(브리지로 재파생), 알람이 필요한 건 '지금
# 롤오버가 막혀 있나'(현재 실패 건수). 0=정상 → 첫 tick 전 거짓경보 없음.
PARTITION_ENSURE_FAILED = Gauge(
    "lloydk_partition_ensure_failed",
    "Current monthly partitions failing to ensure (rollover delayed; re-derived by API)",
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
# [P1-obs] 서빙 등급 분포 — 분류 결과 등급별 카운터(kpi-v2 '분류 등급 분포' 패널 백킹).
# ClassifyService._record_grade가 classify 반환 등급마다 1회 증가(배선 완료). 라이브 FNR
# (CLASSIFY_TOTAL/CORRECT)과 달리 정답 불요한 단순 예측 분포라 서빙 시점에 정직하게 채워진다.
CLASSIFY_GRADE_TOTAL = Counter(
    "lloydk_classify_grade_total",
    "Classifications by resulting grade (serving grade distribution; wired in ClassifyService._record_grade)",
    ["grade"],  # TS | S1 | S2 | S3
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

# [Phase 2] 유사도 escalation — 권위(사람/법적) 출처가 더 높은 등급으로 검증한 *유사* 문서(dense
# 코사인 ≥ τ)에 대한 게이트 동작. 등급은 바뀌지 않는다(검수 라우팅만). label action:
#   · action='ran'    : 게이트가 실제 검색을 수행한 횟수(활성 상태에서 1분류당 1회).
#   · action='routed' : 그중 실제로 needs_review로 라우팅한 횟수.
# ran>0·routed=0 = 활성인데 참조(권위 고등급 유사문서)가 없어 inert(disabled/empty와 구분, 무실데이터 가시화).
SIMILARITY_ESCALATION_TOTAL = Counter(
    "lloydk_similarity_escalation_total",
    "Auto-confirms routed to needs_review because a highly-similar (dense cosine >= tau) doc was human-verified to a higher/more-secret grade than predicted",
    ["action"],
    registry=registry,
)

# [QW] verified_label(사람검수 완료) 감사 기록 실패 — 결정론 경로의 컴플라이언스 감사가 best-effort라
# DB 실패 시 무음 누락된다. 이 값이 0보다 크면 감사 누락 발생 신호(NFR-SEC-01 추적).
CLASSIFY_VERIFIED_LABEL_AUDIT_SKIP_TOTAL = Counter(
    "lloydk_classify_verified_label_audit_skip_total",
    "verified_label audit-trail writes skipped (best-effort failure) — compliance audit gap signal",
    registry=registry,
)

# [P1 가시성] 검증라벨 승급 성공 — promote()가 사람 승인으로 서빙 등급을 override한 건수(등급별).
# 기존엔 실패측(위 AUDIT_SKIP)만 카운터가 있어 '서빙 override 발동률'이 실패면에서만 보였다(가시성
# 비대칭). 성공 발동을 등급별로 노출해 승급 활동을 그래프·경보 가능하게 한다(promote는 API 프로세스라
# 스크랩 가시).
VERIFIED_LABEL_PROMOTED_TOTAL = Counter(
    "lloydk_verified_label_promoted_total",
    "Verified-label promotions that overrode the serving grade (human-approved), by grade",
    ["grade"],
    registry=registry,
)

# [obs] 서빙 검수 게이트가 예외로 fail-open한 횟수(gate별). agreement/llm_second_opinion 게이트는
# 룰엔진·LLM 오류 시 None을 반환해 자동확정을 그대로 통과시킨다(fail-safe). 무음이면 고등급
# 자동확정이 게이트 없이 진행됐는지 안 보인다 — 이 카운터로 'fail-open 발생'을 가시화한다.
SERVING_GATE_FAIL_OPEN_TOTAL = Counter(
    "lloydk_serving_gate_fail_open_total",
    "Serving review gates that failed open (exception) and let auto-confirm proceed ungated, by gate",
    ["gate"],  # agreement | llm_second_opinion | similarity_escalation
    registry=registry,
)

# [P0#1] 합성 검수큐 적재 — synthesize_batch 워커가 생성 문서를 tb_sample_documents 에 적재한 건수.
# label_source 별 노출로 (1) generate→queue→review 운영 루프가 실제로 도는지 (2) noop_fallback/
# llm_nonjson(학습 편입 금지 마커) 비율을 함께 가시화한다. 이전엔 워커가 list[dict]만 반환해
# 검수큐 적재 자체가 0 = 유일 자동화 레버(운영학습)의 입력원이 단절돼 있었다.
SYNTH_SAMPLE_PERSISTED_TOTAL = Counter(
    "lloydk_synth_sample_persisted_total",
    "Synthetic docs persisted to review queue (tb_sample_documents) by synthesize_batch, by body-source marker",
    ["label_source"],  # clean | noop_fallback | llm_nonjson
    registry=registry,
)

# ── 작업(async job) 상태 진입 모니터 ──────────────────────────────────────────
# 비동기 분류·합성·학습·골든빌드 작업의 각 상태 도달 횟수. job_store의 create/update 단일
# choke point에서 best-effort 증가(상태 전이 1회=1 inc, double-count 없음 — Lock/WATCH로 직렬화).
# 기존 CELERY_QUEUE_LENGTH(큐 길이)만으로는 진행/완료/실패율·처리시간을 알 수 없던 사각지대 해소.
# state 값은 코드 실측: queued·running(golden_build)·done·failed·partial.
JOB_STATE_ENTERED_TOTAL = Counter(
    "lloydk_job_state_entered_total",
    "Async job state entries at create/update choke points (queued->running->done/failed/partial)",
    ["state"],
    registry=registry,
)
# 완료 소요시간 — create(_created_at 주입) → 터미널 상태(done/failed/partial) 도달까지 초.
JOB_COMPLETION_DURATION_SECONDS = Histogram(
    "lloydk_job_completion_duration_seconds",
    "Async job latency from queued (create) to terminal state, by terminal state",
    ["state"],
    buckets=(0.1, 0.5, 1, 5, 10, 30, 60, 300, 600, 1800, 3600),
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


# ── [P0 관측성] 워커→API 신호 재노출 (순수 applier + best-effort refresher 분리) ──
# applier 는 데이터→게이지 매핑만 하는 순수 함수(테스트 용이, guard/네트워크 무관).
# refresher 는 공유 매체에서 데이터를 읽어 applier 를 호출(best-effort, 미가용 시 skip).

_DRIFT_GAUGE_BY_NAME = {
    "lloydk_drift_cosine_mean": DRIFT_COSINE_MEAN,
    "lloydk_drift_cosine_std": DRIFT_COSINE_STD,
    "lloydk_drift_kl_divergence": DRIFT_KL_DIVERGENCE,
    "lloydk_drift_alert": DRIFT_ALERT,
    "lloydk_drift_sample_size": DRIFT_SAMPLE_SIZE,
}


def _apply_audit_integrity(sig: dict) -> None:
    """워커 감사체인 검증 신호(dict)를 현재-상태 게이지에 반영(순수)."""
    AUDIT_CHAIN_BROKEN.set(float(sig.get("broken", 0) or 0))
    AUDIT_CHAIN_NIL_HASH.set(float(sig.get("nil_hash_rows", 0) or 0))
    AUDIT_CHAIN_INTEGRITY_OK.set(1.0 if sig.get("integrity_ok") else 0.0)


def _apply_outbox_stats(stats: dict) -> None:
    """outbox stats(dict)를 게이지에 반영(순수)."""
    OUTBOX_DLQ_PENDING.set(float(stats.get("dlq", 0) or 0))
    OUTBOX_PENDING.set(float(stats.get("pending", 0) or 0))


def _apply_drift_report(sig: dict) -> None:
    """워커 drift exposition(dict: 메트릭명→값)을 DRIFT_* 게이지에 반영(순수)."""
    for name, gauge in _DRIFT_GAUGE_BY_NAME.items():
        if name in sig:
            try:
                gauge.set(float(sig[name]))
            except (TypeError, ValueError):
                continue


def _apply_partition_ensure(sig: dict) -> None:
    """워커 파티션 롤오버 신호(dict)를 실패-건수 게이지에 반영(순수)."""
    PARTITION_ENSURE_FAILED.set(float(sig.get("failed", 0) or 0))


def _refresh_audit_integrity_gauges() -> None:
    """감사체인 무결성 — 워커 tick 이 Redis 에 게시한 결과를 게이지로 재노출(P0).

    verify_audit_chain_tick(워커)이 authoritative full-scan 결과를 게시 → 여기서 읽어
    현재-상태 게이지 set. 신호 부재면 미변경(첫 tick 전에는 broken=0 안전 기본값 유지).
    """
    try:
        from lloydk.services.worker_metrics_bridge import (  # noqa: PLC0415
            AUDIT_INTEGRITY,
            load_signal,
        )

        sig = load_signal(AUDIT_INTEGRITY)
        if sig:
            _apply_audit_integrity(sig)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("audit integrity gauge refresh skipped: %s", exc)


def _refresh_outbox_gauges() -> None:
    """Webhook outbox DLQ/pending — API 에서 Redis(공유) stats() 직접 조회로 재노출.

    outbox 카운터는 워커(deliver tick)에서만 inc → API 무가시. Redis STREAM/ZSET 은 워커와
    공유되므로 stats() 가 크로스-프로세스 정확값(_refresh_celery_queue_gauges 동원리).
    테스트에선 redis 연결 플래키 회피 위해 skip(applier 는 직접 단위테스트).
    """
    if _is_testing():
        return
    try:
        from lloydk.services.outbox import get_outbox_store  # noqa: PLC0415

        _apply_outbox_stats(get_outbox_store().stats())
    except Exception as exc:  # noqa: BLE001
        _logger.debug("outbox gauge refresh skipped: %s", exc)


def _refresh_drift_gauges() -> None:
    """임베딩 드리프트 — 워커 drift_tick 이 Redis 에 게시한 exposition 을 게이지로 재노출.

    DRIFT_* 게이지는 워커에서 set → API 무가시. drift_tick 이 export_to_prometheus(report)
    를 게시 → 여기서 메트릭명→값 dict 을 읽어 해당 게이지에 set. 부재면 미변경.
    """
    try:
        from lloydk.services.worker_metrics_bridge import (  # noqa: PLC0415
            DRIFT_REPORT,
            load_signal,
        )

        sig = load_signal(DRIFT_REPORT)
        if sig:
            _apply_drift_report(sig)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("drift gauge refresh skipped: %s", exc)


def _refresh_partition_gauges() -> None:
    """월별 파티션 롤오버 실패 — 워커 ensure_partitions_tick 이 Redis 게시 → 게이지 재노출.

    파티션 롤오버는 워커 beat 에서만 도므로 실패가 API 무가시였다(로그뿐). 부재면 미변경.
    """
    try:
        from lloydk.services.worker_metrics_bridge import (  # noqa: PLC0415
            PARTITION_ENSURE,
            load_signal,
        )

        sig = load_signal(PARTITION_ENSURE)
        if sig:
            _apply_partition_ensure(sig)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("partition gauge refresh skipped: %s", exc)


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
    # [P0 관측성] 워커-프로세스 신호(감사체인·outbox DLQ·drift)를 공유 매체(Postgres/Redis)
    # 에서 API 게이지로 재노출 — 워커 레지스트리 비스크랩으로 인한 '거짓 all-clear' no-data 해소.
    _refresh_audit_integrity_gauges()
    _refresh_outbox_gauges()
    _refresh_drift_gauges()
    _refresh_partition_gauges()


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
