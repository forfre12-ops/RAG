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

def _agreement(pred_label, rule_grade, has_evidence=None, text="본문"):
    from koipa.services.classify_service import ClassifyService

    # pred.rule_grade 를 주면 self.inference 경로(룰엔진 재계산)는 타지 않으므로 self 는 스텁.
    # has_evidence=None 은 run() 이 그 값을 안 실어 준 옛 경로(=판단 보류 없이 기존 동작).
    pred = SimpleNamespace(
        label=pred_label, rule_grade=rule_grade, rule_has_evidence=has_evidence
    )
    return ClassifyService._agreement_gate(SimpleNamespace(inference=None), pred, text)


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


# ── agreement_gate 의 룰 무근거 abstain (커밋 5367c896) ────────────────────────
# 룰엔진은 시드 매칭이 하나도 없으면 **의견이 없는 것**인데 default 로 S3 를 돌려준다.
# 그 S3 를 '불일치'로 읽으면, 근거가 0 건인 쪽 때문에 모델의 정상 판정이 검수로 밀린다.
# hardened42 실측(2026-08-22): 이 구분을 넣자 자동확정 50.00% → 64.29%, 무음 미탐은 1건으로
# 불변이었다(reports/serving_records_hardened42_t203_abstain.json). 수치를 되돌리는 회귀가
# 나면 여기서 먼저 깨져야 한다.

def test_agreement_gate_abstains_when_rule_has_no_evidence(monkeypatch, _stub_grades):
    from koipa import config as cfg

    monkeypatch.setattr(cfg.settings, "agreement_gate_enabled", True, raising=False)
    # 룰이 S3(=default) 를 냈지만 실 근거 0건 → 불일치로 치지 않는다(자동확정 유지).
    assert _agreement(Grade.S1, Grade.S3, has_evidence=False) is None


def test_agreement_gate_routes_when_rule_has_evidence(monkeypatch, _stub_grades):
    from koipa import config as cfg

    monkeypatch.setattr(cfg.settings, "agreement_gate_enabled", True, raising=False)
    # 같은 등급쌍이라도 룰이 실 근거를 들고 S3 라고 하면 그건 진짜 불일치 → 검수 라우팅.
    reason = _agreement(Grade.S1, Grade.S3, has_evidence=True)
    assert reason and "agreement-gate" in reason


def test_agreement_gate_unknown_evidence_keeps_old_behavior(monkeypatch, _stub_grades):
    from koipa import config as cfg

    monkeypatch.setattr(cfg.settings, "agreement_gate_enabled", True, raising=False)
    # 근거 여부를 모르면(None) 라우팅을 유지한다 - abstain 은 False 로 **확인된** 경우만.
    reason = _agreement(Grade.S1, Grade.S3, has_evidence=None)
    assert reason and "agreement-gate" in reason


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


def test_agreement_gate_does_not_abstain_when_text_carries_management_marking(
    monkeypatch, _stub_grades
):
    """시드 0건이어도 본문에 형식적 관리표시가 있으면 룰은 '무의견'이 아니다.

    실측 2026-08-22: 시연 문서 07(비공개 M&A 메모)은 "관계자 외 열람을 제한합니다"를 달고도
    룰 시드 매칭이 0건이었다 - 룰 추출기가 못 잡은 것이지 문서에 신호가 없는 게 아니다.
    그 상태로 abstain 하면 모델 S2 · conf 0.959 가 그대로 자동확정된다(과소분류 방향).
    """
    from koipa import config as cfg

    monkeypatch.setattr(cfg.settings, "agreement_gate_enabled", True, raising=False)
    marked = "본 메모는 관계자 외 열람을 제한합니다. 발표 전까지 외부에 알리지 않는다."
    reason = _agreement(Grade.S2, Grade.S3, has_evidence=False, text=marked)
    assert reason and "agreement-gate" in reason
    # 관리표시가 없는 같은 조건은 종전대로 abstain(자동확정 유지).
    assert _agreement(Grade.S2, Grade.S3, has_evidence=False, text="분기 매출 계획 공유") is None
