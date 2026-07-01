"""Fix 2 — Gate-1(출처 게이트, source-prior) 회귀.

공개 출처(court_decision 등) metadata가 있으면 콘텐츠 모델이 고등급을 내도 S3로 cap.
rule-fallback 경로를 monkeypatch로 고정해 게이트 로직만 결정적으로 검증(모델·DB 불요).
"""

from __future__ import annotations

import pytest

from lloydk.modules.m5_inference.pipeline import InferencePipeline, InferenceResult
from lloydk.schemas.common import Grade


@pytest.fixture(autouse=True)
def _stub_pipeline_db(monkeypatch):
    """InferencePipeline/LabelingPipeline 생성이 PG에 매달리지 않게 DB 의존을 스텁(lite 실행).

    PG 미가용 시 GradeRegistry.get_codes()·build_rule_engine_from_db()가 연결 대기로
    블록되므로, 게이트 단위 검증에서는 기본 등급 + 스텁 엔진으로 대체한다(실제 라벨링
    경로는 _run_rule_fallback monkeypatch로 우회 — 엔진은 호출되지 않음).
    """
    from lloydk.schemas import common as _common

    monkeypatch.setattr(
        _common.GradeRegistry, "get_codes", lambda *a, **k: ["TS", "S1", "S2", "S3"]
    )
    from lloydk.modules.m3_labeling import pipeline as _m3

    monkeypatch.setattr(_m3, "build_rule_engine_from_db", lambda *a, **k: object())


def _force_ts(pipe, monkeypatch):
    """rule-fallback이 항상 TS를 내도록 고정 — 게이트 cap 동작만 분리 검증."""
    res = InferenceResult(
        label=Grade.TS,
        confidence=0.95,
        scores={"TS": 0.95, "S1": 0.03, "S2": 0.01, "S3": 0.01},
    )
    monkeypatch.setattr(pipe, "_run_rule_fallback", lambda *a, **k: res)


@pytest.fixture
def gate_on(monkeypatch):
    from lloydk import config as cfg

    monkeypatch.setattr(cfg.settings, "source_prior_enabled", True, raising=False)
    monkeypatch.setattr(cfg.settings, "source_prior_cap_grade", "S3", raising=False)


def test_public_source_caps_to_s3(monkeypatch, gate_on):
    pipe = InferencePipeline()  # model_dir None → rule fallback 경로
    _force_ts(pipe, monkeypatch)
    res = pipe.run("핵심 기술 설계도와 단가표", metadata={"source_type": "court_decision"})
    assert res.label == Grade.S3
    assert any("source-prior" in w for w in res.warnings)
    # ②·③ 충돌: TS 상향 신호를 덮었으므로 cap-conflict 경고(→ needs_review 라우팅 신호).
    assert any("cap-conflict" in w for w in res.warnings)


def test_s2_cap_no_conflict(monkeypatch, gate_on):
    """S2(비고등급)는 공개출처면 S3로 cap되지만, 고등급 충돌이 아니므로 cap-conflict 없음."""
    pipe = InferencePipeline()
    res_s2 = InferenceResult(
        label=Grade.S2, confidence=0.9, scores={"TS": 0.0, "S1": 0.0, "S2": 0.9, "S3": 0.1}
    )
    monkeypatch.setattr(pipe, "_run_rule_fallback", lambda *a, **k: res_s2)
    res = pipe.run("내부 대외비 문서", metadata={"source_type": "court_decision"})
    assert res.label == Grade.S3
    assert any("source-prior" in w for w in res.warnings)
    assert not any("cap-conflict" in w for w in res.warnings)


def test_no_source_keeps_high_grade(monkeypatch, gate_on):
    pipe = InferencePipeline()
    _force_ts(pipe, monkeypatch)
    res = pipe.run("핵심 기술 설계도와 단가표", metadata={})
    assert res.label == Grade.TS


def test_internal_source_not_capped(monkeypatch, gate_on):
    pipe = InferencePipeline()
    _force_ts(pipe, monkeypatch)
    # 비공개(내부) 출처는 비공지성 미충족이 아니므로 cap 안 함 → 고등급 유지(미탐 트레이드오프 없음).
    res = pipe.run("핵심 기술 설계도와 단가표", metadata={"source_type": "internal_memo"})
    assert res.label == Grade.TS
