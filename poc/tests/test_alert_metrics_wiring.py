"""NEW-H2: alert_rules.yml 이 참조하는 메트릭이 prom_metrics 에 정의·배선됐는지 검증.

배경: 7종 메트릭(audit_chain_broken·classify_correct/total·celery_queue_length·
outbox_dlq_total·pii_masked_total·documents_ingested_total)이 정의 0건이라 알람이
영구 no-data 였다(특히 P0 AuditChainBroken). 본 테스트는 (1) 정의 존재 + alert 가
참조하는 정확한 시계열 이름 생성, (2) 실제 계측 경로 증가를 확인한다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from prometheus_client import generate_latest

from koipa.api.prom_metrics import (
    AUDIT_CHAIN_BROKEN_TOTAL,
    CELERY_QUEUE_LENGTH,
    CLASSIFY_CORRECT_TOTAL,
    CLASSIFY_TOTAL,
    DOCUMENTS_INGESTED_TOTAL,
    OUTBOX_DLQ_TOTAL,
    PII_MASKED_TOTAL,
    registry,
)
from koipa.modules.m2_preprocess.pii_masker import mask_pii


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
    assert "koipa_audit_chain_broken_total" in expo          # P0 AuditChainBroken
    assert "koipa_documents_ingested_total" in expo          # PiiMaskingMissRate 분모
    assert "koipa_outbox_dlq_total" in expo                  # OutboxDlqGrowing
    assert "koipa_classify_total" in expo                    # FnrSpikeOverall 분모
    assert "koipa_classify_correct_total" in expo            # FnrSpikeOverall 분자
    assert 'koipa_pii_masked_total{pii_type="rrn"}' in expo  # PiiMaskingMissRate
    assert 'koipa_celery_queue_length{queue="classify"}' in expo  # CeleryQueueBacklog


def test_correction_total_defined_and_increments():
    """[KPI] 교정 발생률 카운터 — direction별 시계열 노출 + 증가 동작(DB 불요)."""
    from koipa.api.prom_metrics import CORRECTION_TOTAL

    # direction child materialize → 시계열 노출.
    CORRECTION_TOTAL.labels(direction="underclass").inc(0)
    CORRECTION_TOTAL.labels(direction="confirm").inc(0)
    expo = generate_latest(registry).decode()
    assert 'koipa_corrections_total{direction="underclass"}' in expo

    labels = {"direction": "underclass"}
    before = registry.get_sample_value("koipa_corrections_total", labels) or 0.0
    CORRECTION_TOTAL.labels(direction="underclass").inc()
    after = registry.get_sample_value("koipa_corrections_total", labels) or 0.0
    assert after >= before + 1


def test_admin_review_and_resurface_metrics_defined():
    """[KPI] 처리시간 히스토그램 + 동일문서 재등장 카운터 — 시계열 노출(DB 불요)."""
    from koipa.api.prom_metrics import ADMIN_REVIEW_SECONDS, SAME_DOC_RESURFACE_TOTAL

    ADMIN_REVIEW_SECONDS.observe(120)
    SAME_DOC_RESURFACE_TOTAL.inc(0)
    expo = generate_latest(registry).decode()
    assert "koipa_admin_review_seconds" in expo
    assert "koipa_same_doc_resurface_total" in expo


def test_mask_pii_increments_pii_masked_metric():
    # 실제 계측 경로 — rrn 마스킹 시 타입별 카운터 증가.
    labels = {"pii_type": "rrn"}
    before = registry.get_sample_value("koipa_pii_masked_total", labels) or 0.0
    res = mask_pii("주민등록번호 800101-1234567 포함 문서")
    assert res.counts.get("rrn", 0) >= 1                      # 마스킹 자체 동작 확인
    after = registry.get_sample_value("koipa_pii_masked_total", labels) or 0.0
    assert after >= before + 1                                # 메트릭 동반 증가


# ── P0#4: 번들 A–F 안전 게이트 메트릭 → 능동 경보(alert) 배선 ──────────────────
# 배경: serving_gate_fail_open·audit_chain_nil_hash·kill_gate·idempotency_fallback 등
# 안전실패 메트릭이 노출만 되고 alert expr 0건이었다(무인 미탐). koipa-safety-gates 그룹
# 추가로 알람 승격. 아래 테스트는 (1) 그룹의 alert 존재 (2) 참조 시계열 실 노출을 잠근다.

_ALERT_RULES = Path(__file__).resolve().parents[1] / "infra/observability/alert_rules.yml"


def _load_alert_groups() -> dict:
    doc = yaml.safe_load(_ALERT_RULES.read_text(encoding="utf-8"))
    return {g["name"]: g for g in doc["groups"]}


def test_safety_gate_alert_group_present():
    groups = _load_alert_groups()
    assert "koipa-safety-gates" in groups, "안전게이트 경보 그룹 누락"
    names = {r["alert"] for r in groups["koipa-safety-gates"]["rules"]}
    # 안전실패·무결성 핵심 경보가 모두 존재해야 한다(무인 미탐 방지).
    assert {
        "ServingGateFailOpen",
        "AuditChainNilHash",
        "VerifiedLabelAuditSkip",
        "KillGateTripped",
        "KillGateAutoconfirmSuppressed",
        "IdempotencyBackendFallback",
    } <= names


def test_degrade_visibility_alert_group_present():
    """서빙 열화·게이트 발동 신호 경보 그룹 — 정의된 메트릭을 능동 경보로 승격했는지 잠금."""
    groups = _load_alert_groups()
    assert "koipa-degrade-visibility" in groups, "열화 가시화 경보 그룹 누락"
    names = {r["alert"] for r in groups["koipa-degrade-visibility"]["rules"]}
    assert {
        "ServingRuleFallbackActive",
        "ClassifyPersistFailure",
        "SourcePriorCapConflict",
        "MetadataFloorAccessConflict",
    } <= names


def test_degrade_visibility_alert_series_present_in_exposition():
    """열화 경보가 참조하는 정확한 시계열 이름이 실제로 노출되는지 — 라벨 child materialize."""
    from koipa.api.prom_metrics import (
        CLASSIFY_PERSIST_FAILURE_TOTAL,
        METADATA_FLOOR_APPLIED_TOTAL,
        RULE_FALLBACK_TOTAL,
        SOURCE_PRIOR_APPLIED_TOTAL,
    )

    RULE_FALLBACK_TOTAL.inc(0)
    CLASSIFY_PERSIST_FAILURE_TOTAL.labels(reason="db_error").inc(0)
    SOURCE_PRIOR_APPLIED_TOTAL.labels(action="cap_conflict").inc(0)
    METADATA_FLOOR_APPLIED_TOTAL.labels(action="access_conflict").inc(0)

    expo = generate_latest(registry).decode()
    assert "koipa_rule_fallback_total" in expo
    assert 'koipa_classify_persist_failure_total{reason="db_error"}' in expo
    assert 'koipa_source_prior_applied_total{action="cap_conflict"}' in expo
    assert 'koipa_metadata_floor_applied_total{action="access_conflict"}' in expo


def test_safety_gate_alert_series_present_in_exposition():
    """경보가 참조하는 정확한 시계열 이름이 실제로 노출되는지 — 라벨 child materialize."""
    from koipa.api.prom_metrics import (
        AUDIT_CHAIN_NIL_HASH_TOTAL,
        CLASSIFY_VERIFIED_LABEL_AUDIT_SKIP_TOTAL,
        ESCALATION_HELD,
        IDEMPOTENCY_BACKEND_FALLBACK_TOTAL,
        KILL_GATE_AUTOCONFIRM_SUPPRESSED_TOTAL,
        KILL_GATE_TRIPPED,
        SERVING_GATE_FAIL_OPEN_TOTAL,
    )

    SERVING_GATE_FAIL_OPEN_TOTAL.labels(gate="agreement").inc(0)
    AUDIT_CHAIN_NIL_HASH_TOTAL.inc(0)
    CLASSIFY_VERIFIED_LABEL_AUDIT_SKIP_TOTAL.inc(0)
    KILL_GATE_TRIPPED.set(0)
    KILL_GATE_AUTOCONFIRM_SUPPRESSED_TOTAL.labels(grade="TS").inc(0)
    IDEMPOTENCY_BACKEND_FALLBACK_TOTAL.inc(0)
    ESCALATION_HELD.labels(grade="TS").set(0)

    expo = generate_latest(registry).decode()
    assert 'koipa_serving_gate_fail_open_total{gate="agreement"}' in expo
    assert "koipa_audit_chain_nil_hash_total" in expo
    assert "koipa_classify_verified_label_audit_skip_total" in expo
    assert "koipa_kill_gate_tripped" in expo
    assert 'koipa_kill_gate_autoconfirm_suppressed_total{grade="TS"}' in expo
    assert "koipa_idempotency_backend_fallback_total" in expo
    assert 'koipa_escalation_held{grade="TS"}' in expo


def test_no_alert_references_undefined_metric():
    """드리프트 가드: alert_rules.yml 의 모든 koipa_* 메트릭이 prom_metrics 에 정의됐는지.

    Grafana kpi-v2.json 이 없는 메트릭 5종을 참조해 영구 no-data 였던 실패모드를 alert 쪽에서
    사전 차단한다. 미들웨어(요청 카운터/지연)는 PrometheusMiddleware 정의라 allowlist.
    """
    src = (Path(__file__).resolve().parents[1] / "src/koipa/api/prom_metrics.py").read_text(
        encoding="utf-8"
    )
    middleware_defined = {
        "koipa_requests_total",
        "koipa_request_duration_seconds_bucket",
    }
    refs: set[str] = set()
    for g in _load_alert_groups().values():
        for r in g["rules"]:
            refs |= set(re.findall(r"koipa_[a-z_]+", r["expr"]))
    missing = sorted(
        m for m in refs if f'"{m}"' not in src and m not in middleware_defined
    )
    assert not missing, f"alert 가 정의되지 않은 메트릭 참조(no-data 위험): {missing}"


# ── [P1] Grafana 대시보드 ↔ registry 정합(3자 정합의 대시보드 축) ─────────────────
# kpi-v2.json 이 registry에 없는 메트릭 5종(active_learning 오타·classify_grade·outbox_pending_total·
# review_action)을 참조해 5패널 영구 no-data 였다. alert 축(위)과 동일 실패모드를 대시보드 축에서도
# 사전 차단한다 — 대시보드가 정의되지 않은 메트릭을 참조하면 CI에서 fail.
_DASHBOARDS = Path(__file__).resolve().parents[1] / "infra/observability/grafana/dashboards"


def test_grafana_dashboards_reference_only_defined_metrics():
    src = (Path(__file__).resolve().parents[1] / "src/koipa/api/prom_metrics.py").read_text(
        encoding="utf-8"
    )
    middleware_defined = {
        "koipa_requests_total",
        "koipa_request_duration_seconds_bucket",
        "koipa_request_duration_seconds",
        "koipa_requests_in_progress",
    }
    files = sorted(_DASHBOARDS.glob("*.json"))
    assert files, "Grafana 대시보드 JSON 없음"
    problems: dict[str, list[str]] = {}
    for f in files:
        text = f.read_text(encoding="utf-8")
        json.loads(text)  # 유효 JSON 보장
        refs = set(re.findall(r"koipa_[a-z_]+", text))
        missing = sorted(
            m for m in refs if f'"{m}"' not in src and m not in middleware_defined
        )
        if missing:
            problems[f.name] = missing
    assert not problems, f"대시보드가 미정의 메트릭 참조(no-data 위험): {problems}"


# 미들웨어 메트릭의 실제 라벨키(PrometheusMiddleware). 이름은 맞는데 라벨키가 틀리면
# (예: {path=~...} — emitter는 route) 패널이 영구 no-data가 된다. 이름-only 검사는 이를
# 못 잡으므로 라벨키 화이트리스트로 별도 차단한다(kpi-v2 panel1·2 path→route 회귀 봉인).
_MIDDLEWARE_METRIC_LABELS = {
    "koipa_requests_total": {"method", "route", "status"},
    "koipa_request_duration_seconds_bucket": {"method", "route", "le"},
    "koipa_request_duration_seconds": {"method", "route"},
}

# 워커 프로세스에서만 증가하는 _total 카운터는 API scrape에서 항상 0 → 대시보드/알람은
# worker_metrics_bridge가 재노출하는 API-가시 게이지(_pending/_rows)를 써야 한다
# (P0 no-data 함정: outbox_dlq_total→_pending, audit_chain_broken_total→_broken_rows 등).
_WORKER_ONLY_USE_INSTEAD = {
    "koipa_outbox_dlq_total": "koipa_outbox_dlq_pending",
    "koipa_audit_chain_broken_total": "koipa_audit_chain_broken_rows",
    "koipa_audit_chain_nil_hash_total": "koipa_audit_chain_nil_hash_rows",
}


def test_grafana_dashboards_use_valid_middleware_label_keys():
    """[정합 강화] 대시보드 라벨키 검증 — 이름만 맞고 라벨키가 틀린 no-data 패널 차단."""
    files = sorted(_DASHBOARDS.glob("*.json"))
    problems: dict[str, list[str]] = {}
    for f in files:
        text = f.read_text(encoding="utf-8")
        for metric, allowed_keys in _MIDDLEWARE_METRIC_LABELS.items():
            for sel in re.findall(re.escape(metric) + r"\{([^}]*)\}", text):
                keys = set(re.findall(r"(\w+)\s*=~?", sel))
                bad = sorted(keys - allowed_keys)
                if bad:
                    problems.setdefault(f.name, []).append(f"{metric}: {bad}")
    assert not problems, f"대시보드가 미정의 라벨키 참조(no-data 위험): {problems}"


def test_grafana_dashboards_avoid_worker_only_counters():
    """[정합 강화] 대시보드가 워커-only _total 카운터(API scrape서 항상 0)를 쓰지 않는지."""
    files = sorted(_DASHBOARDS.glob("*.json"))
    problems: dict[str, list[str]] = {}
    for f in files:
        refs = set(re.findall(r"koipa_[a-z_]+", f.read_text(encoding="utf-8")))
        for bad, good in _WORKER_ONLY_USE_INSTEAD.items():
            if bad in refs:
                problems.setdefault(f.name, []).append(f"{bad} → {good} 사용")
    assert not problems, f"대시보드가 워커-only 카운터 참조(API scrape서 0=no-data): {problems}"
