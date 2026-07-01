"""[obs] 서빙 검수 게이트 fail-open 가시화.

agreement / llm_second_opinion 게이트는 룰엔진·LLM 오류 시 None 반환(fail-safe)으로
자동확정을 그대로 통과시킨다. 무음이면 고등급 자동확정이 게이트 없이 진행됐는지 안 보인다 —
예외 fail-open 시 SERVING_GATE_FAIL_OPEN_TOTAL{gate} 증가 + debug 로그를 고정.
"""

from __future__ import annotations

from types import SimpleNamespace

from lloydk.api import prom_metrics as pm
from lloydk.schemas.common import Grade
from lloydk.services.classify_service import ClassifyService


def _gate_val(gate) -> float:
    return pm.SERVING_GATE_FAIL_OPEN_TOTAL.labels(gate=gate)._value.get()


def test_record_gate_fail_open_increments():
    for g in ("agreement", "llm_second_opinion"):
        before = _gate_val(g)
        ClassifyService._record_gate_fail_open(g)
        assert _gate_val(g) == before + 1


def test_record_grade_increments_distribution():
    # [P1-obs] 서빙 등급 분포 카운터 배선 — enum·str 양쪽 등급을 1회 증가(kpi-v2 등급분포 패널).
    def _gv(g):
        return pm.CLASSIFY_GRADE_TOTAL.labels(grade=g)._value.get()

    before_ts = _gv("TS")
    ClassifyService._record_grade(Grade.TS)   # enum 입력
    ClassifyService._record_grade("S1")        # str 입력(fail-secure top_grade 경로)
    assert _gv("TS") == before_ts + 1
    assert _gv("S1") >= 1


def test_record_grade_best_effort(monkeypatch):
    # 메트릭 import 실패해도 예외 전파 없음(분류 경로 무영향).
    import builtins
    real = builtins.__import__

    def _boom(name, *a, **k):
        if name == "lloydk.api.prom_metrics":
            raise ImportError("x")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _boom)
    ClassifyService._record_grade(Grade.S2)  # 예외 없으면 통과


def test_record_gate_fail_open_best_effort(monkeypatch):
    # 메트릭 레지스트리 import 실패해도 예외 전파 없음(분류 경로 무영향).
    import builtins
    real = builtins.__import__

    def _boom(name, *a, **k):
        if name == "lloydk.api.prom_metrics":
            raise ImportError("x")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _boom)
    ClassifyService._record_gate_fail_open("agreement")  # 예외 없으면 통과


def test_agreement_gate_fail_open_records(monkeypatch):
    from lloydk import config as cfg
    from lloydk.schemas import common as _common

    monkeypatch.setattr(cfg.settings, "agreement_gate_enabled", True, raising=False)
    monkeypatch.setattr(_common.GradeRegistry, "get_codes", lambda *a, **k: ["TS", "S1", "S2", "S3"])

    # 비공개 등급 예측 + rule_grade 없음 → self.inference.labeling 접근에서 예외 → fail-open.
    pred = SimpleNamespace(label=Grade.S1, rule_grade=None)
    fake_self = SimpleNamespace(
        inference=None,
        _record_gate_fail_open=ClassifyService._record_gate_fail_open,
    )
    before = _gate_val("agreement")
    out = ClassifyService._agreement_gate(fake_self, pred, "본문")
    assert out is None  # fail-safe 유지
    assert _gate_val("agreement") == before + 1


def test_kill_gate_brake_fail_open_records(monkeypatch):
    # kill-gate 계통장애(should_suppress_autoconfirm 예외)도 다른 게이트와 동일하게
    # fail-open(동작 보존)하되 SERVING_GATE_FAIL_OPEN_TOTAL{gate="kill_gate"}로 가시화.
    import lloydk.modules.m6_evaluation.kill_gate as kg

    def _boom(*a, **k):
        raise RuntimeError("kill-gate read failed")

    monkeypatch.setattr(kg, "should_suppress_autoconfirm", _boom)
    before = _gate_val("kill_gate")
    out = ClassifyService._kill_gate_brake(Grade.TS)
    assert out is None  # fail-open 유지
    assert _gate_val("kill_gate") == before + 1


def test_llm_second_opinion_fail_open_records(monkeypatch):
    from lloydk import config as cfg
    from lloydk.schemas import common as _common

    monkeypatch.setattr(cfg.settings, "model_secondopinion_llm_enabled", True, raising=False)
    monkeypatch.setattr(
        _common.GradeRegistry, "get_order", lambda *a, **k: {"TS": 0, "S1": 1, "S2": 2, "S3": 3}
    )
    import lloydk.modules.m3_labeling.llm_labeler as llm_mod

    class _Boom:
        def label(self, *a, **k):
            raise RuntimeError("LLM down")

    monkeypatch.setattr(llm_mod, "LLMLabeler", _Boom)
    before = _gate_val("llm_second_opinion")
    out = ClassifyService._llm_second_opinion("본문", "S2")  # 비-최고등급 → LLM 호출 → 예외
    assert out is None
    assert _gate_val("llm_second_opinion") == before + 1
