"""NEW-H2: alert_rules.yml 이 참조하는 메트릭이 prom_metrics 에 정의·배선됐는지 검증.

배경: 7종 메트릭(audit_chain_broken·classify_correct/total·celery_queue_length·
outbox_dlq_total·pii_masked_total·documents_ingested_total)이 정의 0건이라 알람이
영구 no-data 였다(특히 P0 AuditChainBroken). 본 테스트는 (1) 정의 존재 + alert 가
참조하는 정확한 시계열 이름 생성, (2) 실제 계측 경로 증가를 확인한다.
"""

from __future__ import annotations

from prometheus_client import generate_latest

from lloydk.api.prom_metrics import (
    AUDIT_CHAIN_BROKEN_TOTAL,
    CELERY_QUEUE_LENGTH,
    CLASSIFY_CORRECT_TOTAL,
    CLASSIFY_TOTAL,
    DOCUMENTS_INGESTED_TOTAL,
    OUTBOX_DLQ_TOTAL,
    PII_MASKED_TOTAL,
    registry,
)
from lloydk.modules.m2_preprocess.pii_masker import mask_pii


def test_alert_referenced_series_present_in_exposition():
    # 라벨 메트릭은 child 를 materialize 해야 시계열로 노출 → inc(0)/set(0) 으로 생성.
    AUDIT_CHAIN_BROKEN_TOTAL.inc(0)
    DOCUMENTS_INGESTED_TOTAL.inc(0)
    OUTBOX_DLQ_TOTAL.inc(0)
    CLASSIFY_TOTAL.inc(0)
    CLASSIFY_CORRECT_TOTAL.inc(0)
    PII_MASKED_TOTAL.labels(pii_type="rrn").inc(0)
    CELERY_QUEUE_LENGTH.labels(queue="classify").set(0)

    expo = generate_latest(registry).decode()
    # alert_rules.yml 이 참조하는 정확한 시계열 이름이 노출되는지.
    assert "lloydk_audit_chain_broken_total" in expo          # P0 AuditChainBroken
    assert "lloydk_documents_ingested_total" in expo          # PiiMaskingMissRate 분모
    assert "lloydk_outbox_dlq_total" in expo                  # OutboxDlqGrowing
    assert "lloydk_classify_total" in expo                    # FnrSpikeOverall 분모
    assert "lloydk_classify_correct_total" in expo            # FnrSpikeOverall 분자
    assert 'lloydk_pii_masked_total{pii_type="rrn"}' in expo  # PiiMaskingMissRate
    assert 'lloydk_celery_queue_length{queue="classify"}' in expo  # CeleryQueueBacklog


def test_correction_total_defined_and_increments():
    """[KPI] 교정 발생률 카운터 — direction별 시계열 노출 + 증가 동작(DB 불요)."""
    from lloydk.api.prom_metrics import CORRECTION_TOTAL

    # direction child materialize → 시계열 노출.
    CORRECTION_TOTAL.labels(direction="underclass").inc(0)
    CORRECTION_TOTAL.labels(direction="confirm").inc(0)
    expo = generate_latest(registry).decode()
    assert 'lloydk_corrections_total{direction="underclass"}' in expo

    labels = {"direction": "underclass"}
    before = registry.get_sample_value("lloydk_corrections_total", labels) or 0.0
    CORRECTION_TOTAL.labels(direction="underclass").inc()
    after = registry.get_sample_value("lloydk_corrections_total", labels) or 0.0
    assert after >= before + 1


def test_admin_review_and_resurface_metrics_defined():
    """[KPI] 처리시간 히스토그램 + 동일문서 재등장 카운터 — 시계열 노출(DB 불요)."""
    from lloydk.api.prom_metrics import ADMIN_REVIEW_SECONDS, SAME_DOC_RESURFACE_TOTAL

    ADMIN_REVIEW_SECONDS.observe(120)
    SAME_DOC_RESURFACE_TOTAL.inc(0)
    expo = generate_latest(registry).decode()
    assert "lloydk_admin_review_seconds" in expo
    assert "lloydk_same_doc_resurface_total" in expo


def test_mask_pii_increments_pii_masked_metric():
    # 실제 계측 경로 — rrn 마스킹 시 타입별 카운터 증가.
    labels = {"pii_type": "rrn"}
    before = registry.get_sample_value("lloydk_pii_masked_total", labels) or 0.0
    res = mask_pii("주민등록번호 800101-1234567 포함 문서")
    assert res.counts.get("rrn", 0) >= 1                      # 마스킹 자체 동작 확인
    after = registry.get_sample_value("lloydk_pii_masked_total", labels) or 0.0
    assert after >= before + 1                                # 메트릭 동반 증가
