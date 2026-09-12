"""[P0 관측성] 워커→API 메트릭 브리지 — 워커 프로세스 신호가 API 게이지로 재노출되는지 검증.

배경: audit_chain(verify tick)·drift(drift tick)·outbox DLQ 신호는 워커 프로세스에서만
계산되는데 Prometheus 는 API 만 스크랩한다 → API /metrics-prom 에 영구 0(거짓 all-clear).
해결: 워커가 공유 Redis 에 게시, API _refresh_*가 읽어 게이지 재노출. 본 테스트는
(1) 브리지 publish/load round-trip, (2) 순수 applier 의 게이지 매핑, (3) refresher 의
신호→게이지 흐름, (4) alert 가 API-가시 게이지를 참조하는지(no-data 회귀 잠금)를 검증한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from prometheus_client import generate_latest

from koipa.api import prom_metrics as pm
from koipa.api.prom_metrics import registry
from koipa.services import worker_metrics_bridge as bridge


# --------------------------------------------------------------------------- #
# FakeRedis — decode_responses=True 시맨틱(str in/out) 최소 구현.
# --------------------------------------------------------------------------- #
class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, key, value, ex=None):  # noqa: ANN001
        self.store[key] = value
        return True

    def get(self, key):  # noqa: ANN001
        return self.store.get(key)


# --------------------------------------------------------------------------- #
# 1) 브리지 publish/load round-trip
# --------------------------------------------------------------------------- #
def test_publish_load_roundtrip(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(bridge, "_get_redis", lambda: fake)

    ok = bridge.publish_signal(bridge.AUDIT_INTEGRITY, {"broken": 2, "nil_hash_rows": 1})
    assert ok is True
    loaded = bridge.load_signal(bridge.AUDIT_INTEGRITY)
    assert loaded is not None
    assert loaded["broken"] == 2
    assert loaded["nil_hash_rows"] == 1
    assert "checked_at" in loaded  # 신선도 판정용 자동 부가


def test_load_absent_returns_none(monkeypatch):
    monkeypatch.setattr(bridge, "_get_redis", lambda: _FakeRedis())
    assert bridge.load_signal(bridge.DRIFT_REPORT) is None


def test_bridge_best_effort_when_redis_unavailable(monkeypatch):
    monkeypatch.setattr(bridge, "_get_redis", lambda: None)
    assert bridge.publish_signal(bridge.DRIFT_REPORT, {"x": 1}) is False
    assert bridge.load_signal(bridge.DRIFT_REPORT) is None


# --------------------------------------------------------------------------- #
# 2) 순수 applier — 데이터→게이지 매핑
# --------------------------------------------------------------------------- #
def test_apply_audit_integrity_sets_gauges():
    pm._apply_audit_integrity({"broken": 3, "nil_hash_rows": 2, "integrity_ok": False})
    assert registry.get_sample_value("koipa_audit_chain_broken_rows") == 3.0
    assert registry.get_sample_value("koipa_audit_chain_nil_hash_rows") == 2.0
    assert registry.get_sample_value("koipa_audit_chain_integrity_ok") == 0.0

    # 무결성 회복 → broken/nil 0, integrity_ok 1
    pm._apply_audit_integrity({"broken": 0, "nil_hash_rows": 0, "integrity_ok": True})
    assert registry.get_sample_value("koipa_audit_chain_broken_rows") == 0.0
    assert registry.get_sample_value("koipa_audit_chain_integrity_ok") == 1.0


def test_apply_outbox_stats_sets_gauges():
    pm._apply_outbox_stats({"backend": "redis", "pending": 5, "dlq": 4})
    assert registry.get_sample_value("koipa_outbox_dlq_pending") == 4.0
    assert registry.get_sample_value("koipa_outbox_pending") == 5.0


def test_apply_drift_report_sets_gauges():
    pm._apply_drift_report({
        "koipa_drift_kl_divergence": 0.7,
        "koipa_drift_cosine_mean": 0.42,
        "koipa_drift_alert": 1.0,
        "koipa_drift_sample_size": 200.0,
    })
    assert registry.get_sample_value("koipa_drift_kl_divergence") == 0.7
    assert registry.get_sample_value("koipa_drift_alert") == 1.0
    assert registry.get_sample_value("koipa_drift_sample_size") == 200.0


def test_apply_drift_report_ignores_bad_values():
    # 잘못된 값은 조용히 스킵(다른 게이지에 영향 없음).
    pm._apply_drift_report({"koipa_drift_kl_divergence": "n/a"})
    # 예외 없이 통과하면 성공(값은 이전 상태 유지).


def test_apply_partition_ensure_sets_gauge():
    pm._apply_partition_ensure({"failed": 2, "ensured": 7})
    assert registry.get_sample_value("koipa_partition_ensure_failed") == 2.0
    pm._apply_partition_ensure({"failed": 0, "ensured": 9})
    assert registry.get_sample_value("koipa_partition_ensure_failed") == 0.0


# --------------------------------------------------------------------------- #
# 3) refresher — 신호 로드 → applier 흐름
# --------------------------------------------------------------------------- #
def test_refresh_audit_integrity_from_signal(monkeypatch):
    monkeypatch.setattr(
        bridge, "load_signal",
        lambda name: {"broken": 9, "nil_hash_rows": 0, "integrity_ok": False}
        if name == bridge.AUDIT_INTEGRITY else None,
    )
    pm._refresh_audit_integrity_gauges()
    assert registry.get_sample_value("koipa_audit_chain_broken_rows") == 9.0


def test_refresh_audit_integrity_absent_signal_noop(monkeypatch):
    # 신호 부재 → 게이지 미변경(거짓 all-clear 방지: 기본 0 유지, 예외 없음).
    pm._apply_audit_integrity({"broken": 0, "nil_hash_rows": 0, "integrity_ok": True})
    monkeypatch.setattr(bridge, "load_signal", lambda name: None)
    pm._refresh_audit_integrity_gauges()  # 예외 없이 통과
    assert registry.get_sample_value("koipa_audit_chain_broken_rows") == 0.0


def test_refresh_drift_from_signal(monkeypatch):
    monkeypatch.setattr(
        bridge, "load_signal",
        lambda name: {"koipa_drift_kl_divergence": 0.55}
        if name == bridge.DRIFT_REPORT else None,
    )
    pm._refresh_drift_gauges()
    assert registry.get_sample_value("koipa_drift_kl_divergence") == 0.55


def test_refresh_partition_from_signal(monkeypatch):
    monkeypatch.setattr(
        bridge, "load_signal",
        lambda name: {"failed": 3, "ensured": 6}
        if name == bridge.PARTITION_ENSURE else None,
    )
    pm._refresh_partition_gauges()
    assert registry.get_sample_value("koipa_partition_ensure_failed") == 3.0


# --------------------------------------------------------------------------- #
# 4) 노출 + alert 정합 (no-data 회귀 잠금)
# --------------------------------------------------------------------------- #
def test_worker_signal_gauges_exposed():
    pm.AUDIT_CHAIN_BROKEN.set(0)
    pm.AUDIT_CHAIN_NIL_HASH.set(0)
    pm.AUDIT_CHAIN_INTEGRITY_OK.set(1)
    pm.OUTBOX_DLQ_PENDING.set(0)
    pm.OUTBOX_PENDING.set(0)
    pm.PARTITION_ENSURE_FAILED.set(0)
    expo = generate_latest(registry).decode()
    for name in (
        "koipa_audit_chain_broken_rows",
        "koipa_audit_chain_nil_hash_rows",
        "koipa_audit_chain_integrity_ok",
        "koipa_outbox_dlq_pending",
        "koipa_outbox_pending",
        "koipa_partition_ensure_failed",
    ):
        assert name in expo, f"{name} 미노출"


_ALERT_RULES = Path(__file__).resolve().parents[1] / "infra/observability/alert_rules.yml"


def _alert_expr(alert_name: str) -> str:
    doc = yaml.safe_load(_ALERT_RULES.read_text(encoding="utf-8"))
    for g in doc["groups"]:
        for r in g["rules"]:
            if r.get("alert") == alert_name:
                return r["expr"]
    raise AssertionError(f"alert {alert_name} 미존재")


def test_alerts_reference_api_visible_gauges():
    """P0 회귀 잠금: 세 알람이 워커-전용 카운터가 아니라 API-가시 게이지를 참조해야 한다.

    (워커 카운터 koipa_*_total 참조로 되돌아가면 API 스크랩에서 영구 no-data 재발.)
    """
    broken = _alert_expr("AuditChainBroken")
    assert "koipa_audit_chain_broken_rows" in broken
    assert "koipa_audit_chain_broken_total" not in broken

    nil = _alert_expr("AuditChainNilHash")
    assert "koipa_audit_chain_nil_hash_rows" in nil
    assert "koipa_audit_chain_nil_hash_total" not in nil

    dlq = _alert_expr("OutboxDlqGrowing")
    assert "koipa_outbox_dlq_pending" in dlq
    assert "koipa_outbox_dlq_total" not in dlq

    part = _alert_expr("PartitionEnsureFailed")
    assert "koipa_partition_ensure_failed" in part


def test_no_alert_references_undefined_metric_still_holds():
    """기존 드리프트 가드와 동일 원리 — 새 게이지 3종이 prom_metrics 에 정의됐는지 재확인."""
    src = (
        Path(__file__).resolve().parents[1] / "src/koipa/api/prom_metrics.py"
    ).read_text(encoding="utf-8")
    for name in (
        "koipa_audit_chain_broken_rows",
        "koipa_audit_chain_nil_hash_rows",
        "koipa_outbox_dlq_pending",
        "koipa_partition_ensure_failed",
    ):
        assert f'"{name}"' in src, f"{name} 정의 누락(alert no-data 위험)"


def test_alert_metric_names_match_prom_definitions():
    """alert_rules.yml 의 모든 koipa_* 참조가 prom_metrics 에 정의됐는지(전량 재검증)."""
    src = (
        Path(__file__).resolve().parents[1] / "src/koipa/api/prom_metrics.py"
    ).read_text(encoding="utf-8")
    middleware_defined = {
        "koipa_requests_total",
        "koipa_request_duration_seconds_bucket",
    }
    doc = yaml.safe_load(_ALERT_RULES.read_text(encoding="utf-8"))
    refs: set[str] = set()
    for g in doc["groups"]:
        for r in g["rules"]:
            refs |= set(re.findall(r"koipa_[a-z_]+", r["expr"]))
    missing = sorted(m for m in refs if f'"{m}"' not in src and m not in middleware_defined)
    assert not missing, f"정의 안 된 메트릭 참조: {missing}"
