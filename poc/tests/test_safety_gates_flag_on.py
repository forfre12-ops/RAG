"""[NEW-S1] 안전 게이트 flag-ON 경로 단위 테스트.

agreement_gate / model_secondopinion_llm / metadata_floor 는 전부 구현·배선됐으나
flag=True 경로 전용 테스트가 없어 '운영에서 켤 근거'가 부재했다(OFF 폴백만 커버).
monkeypatch 로 settings flag 를 켜고 엔진/LLM 을 스텁해 데이터·DB 없이 게이트 동작을 고정한다.
(source_prior 게이트는 test_source_prior_gate.py 가 동일 패턴으로 이미 커버.)
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from koipa.schemas.common import Grade


@pytest.fixture
def _stub_grades(monkeypatch):
    """GradeRegistry.get_codes 를 DB 없이 고정(get_order 는 이로부터 파생)."""
    from koipa.schemas import common as _common

    monkeypatch.setattr(
        _common.GradeRegistry, "get_codes", lambda *a, **k: ["TS", "S1", "S2", "S3"]
    )


# ── agreement_gate (model vs rule 합의) ───────────────────────────────────────

def _agreement(pred_label, rule_grade):
    from koipa.services.classify_service import ClassifyService

    # pred.rule_grade 를 주면 self.inference 경로(룰엔진 재계산)는 타지 않으므로 self 는 스텁.
    pred = SimpleNamespace(label=pred_label, rule_grade=rule_grade)
    return ClassifyService._agreement_gate(SimpleNamespace(inference=None), pred, "본문")


def test_agreement_gate_on_disagreement_routes_review(monkeypatch, _stub_grades):
    from koipa import config as cfg

    monkeypatch.setattr(cfg.settings, "agreement_gate_enabled", True, raising=False)
    reason = _agreement(Grade.S1, Grade.S2)   # 비공개 등급에서 model≠rule
    assert reason and "agreement-gate" in reason


def test_agreement_gate_on_public_grade_no_route(monkeypatch, _stub_grades):
    from koipa import config as cfg

    monkeypatch.setattr(cfg.settings, "agreement_gate_enabled", True, raising=False)
    assert _agreement(Grade.S3, Grade.S2) is None   # 공개등급(S3)은 conf 단독 신뢰 → 합의 불요


def test_agreement_gate_off_default_no_route(monkeypatch, _stub_grades):
    from koipa import config as cfg

    monkeypatch.setattr(cfg.settings, "agreement_gate_enabled", False, raising=False)
    assert _agreement(Grade.S1, Grade.S2) is None   # 기본 OFF → 비파괴(불일치여도 라우팅 안 함)


# ── llm_second_opinion (모델 비-TS 자동확정에 대한 LLM 2차의견) ───────────────

def test_llm_second_opinion_on_higher_grade_routes_review(monkeypatch, _stub_grades):
    from koipa import config as cfg
    from koipa.services.classify_service import ClassifyService

    monkeypatch.setattr(cfg.settings, "model_secondopinion_llm_enabled", True, raising=False)

    import koipa.modules.m3_labeling.llm_labeler as llm_mod

    class _StubLLM:
        def label(self, text, **k):
            return SimpleNamespace(grade="S1", confidence=0.8)   # 모델 S2보다 높은 S1 제시

    monkeypatch.setattr(llm_mod, "LLMLabeler", _StubLLM)

    reason = ClassifyService._llm_second_opinion("본문", "S2")
    assert reason and "llm-secondopinion" in reason


def test_llm_second_opinion_off_default_no_route(monkeypatch):
    from koipa import config as cfg
    from koipa.services.classify_service import ClassifyService

    monkeypatch.setattr(cfg.settings, "model_secondopinion_llm_enabled", False, raising=False)
    assert ClassifyService._llm_second_opinion("본문", "S2") is None   # 기본 OFF → 비파괴


# ── metadata_floor (ICD 보안표시·접근범위 상향/충돌, pipeline) ────────────────

@pytest.fixture
def _stub_pipeline_db(monkeypatch):
    """InferencePipeline/LabelingPipeline 생성이 PG에 매달리지 않게 스텁(lite 실행)."""
    from koipa.schemas import common as _common

    monkeypatch.setattr(
        _common.GradeRegistry, "get_codes", lambda *a, **k: ["TS", "S1", "S2", "S3"]
    )
    from koipa.modules.m3_labeling import pipeline as _m3

    monkeypatch.setattr(_m3, "build_rule_engine_from_db", lambda *a, **k: object())


def _force_rule(pipe, monkeypatch, grade):
    from koipa.modules.m5_inference.pipeline import InferenceResult

    res = InferenceResult(
        label=grade, confidence=0.9,
        scores={"TS": 0.0, "S1": 0.0, "S2": 0.9, "S3": 0.1},
    )
    monkeypatch.setattr(pipe, "_run_rule_fallback", lambda *a, **k: res)


def test_metadata_floor_on_security_marking_raises_grade(monkeypatch, _stub_pipeline_db):
    from koipa import config as cfg
    from koipa.modules.m5_inference.pipeline import InferencePipeline

    monkeypatch.setattr(cfg.settings, "metadata_floor_enabled", True, raising=False)
    pipe = InferencePipeline()
    _force_rule(pipe, monkeypatch, Grade.S2)
    res = pipe.run("내부 문서", metadata={"security_marking": "secret"})   # secret → S1 상향
    assert res.label == Grade.S1
    assert any("metadata-floor" in w for w in res.warnings)


def test_metadata_floor_on_access_scope_conflict_routes_review(monkeypatch, _stub_pipeline_db):
    from koipa import config as cfg
    from koipa.modules.m5_inference.pipeline import InferencePipeline

    monkeypatch.setattr(cfg.settings, "metadata_floor_enabled", True, raising=False)
    pipe = InferencePipeline()
    _force_rule(pipe, monkeypatch, Grade.S3)   # 예측 낮은데 접근범위 제한 → 충돌
    res = pipe.run("내부 문서", metadata={"access_scope": "approved_only"})
    assert any("metadata-access-conflict" in w for w in res.warnings)


def test_metadata_floor_off_default_no_change(monkeypatch, _stub_pipeline_db):
    from koipa import config as cfg
    from koipa.modules.m5_inference.pipeline import InferencePipeline

    monkeypatch.setattr(cfg.settings, "metadata_floor_enabled", False, raising=False)
    pipe = InferencePipeline()
    _force_rule(pipe, monkeypatch, Grade.S2)
    res = pipe.run("내부 문서", metadata={"security_marking": "secret"})
    assert res.label == Grade.S2   # 기본 OFF → 상향 없음(비파괴)
    assert not any("metadata-floor" in w for w in res.warnings)
