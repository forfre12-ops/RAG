"""분류측 서빙 게이트 시나리오 — sparse-evidence 발동 + sparse/low-confidence 라우팅.

기존 커버리지 갭:
  - sparse-evidence: 전용 테스트 전무(rule-fallback 빈약 근거 → 검수). 기본
    rule_fallback_min_evidence=0.9 로 활성인데 발동·라우팅 모두 미검증이었다.
  - low-confidence: 경고 문자열이 _factors_source/metrics 픽스처로만 쓰였을 뿐,
    'confidence<임계 → needs_review 라우팅' 결정 자체는 단언되지 않았다.

두 안전장치의 계약(등급을 바꾸지 않고 needs_review로 라우팅)을 모델·DB 없이 고정한다.
(metadata-access-conflict / agreement / llm-second / kill / similarity 게이트는
 test_safety_gates_flag_on.py 등에서 이미 커버 — 여기서 중복하지 않는다.)
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lloydk.schemas.common import Grade


# ---------------------------------------------------------------------------
# 공통 스텁 — DB/모델 없이 in-process
# ---------------------------------------------------------------------------
@pytest.fixture
def _no_db(monkeypatch):
    """GradeRegistry / rule-engine 빌드가 PG에 매달리지 않게 스텁."""
    from lloydk.schemas import common as _common

    monkeypatch.setattr(
        _common.GradeRegistry, "get_codes", lambda *a, **k: ["TS", "S1", "S2", "S3"]
    )
    from lloydk.modules.m3_labeling import pipeline as _m3

    monkeypatch.setattr(_m3, "build_rule_engine_from_db", lambda *a, **k: object())


def _service(monkeypatch):
    """모델 미로드(model_dir=None) ClassifyService — 빠른 구성, rule-fallback."""
    import lloydk.services.classify_service as cs

    monkeypatch.setattr(cs, "_resolve_serving_model_dir", lambda *a, **k: None)
    return cs.ClassifyService()


def _fake_pred(*, label=Grade.S2, confidence=1.0, warnings=None):
    from lloydk.modules.m5_inference.pipeline import InferenceResult

    return InferenceResult(
        label=label,
        confidence=confidence,
        scores={label.value: confidence},
        model_version="rule-fallback-v0",
        warnings=list(warnings or []),
        rule_grade=label.value,
    )


# ---------------------------------------------------------------------------
# 1. sparse-evidence 발동 — rule-fallback 절대 점수 < floor 이면 경고를 남긴다
# ---------------------------------------------------------------------------
class TestSparseEvidenceEmission:
    def _pipe_with_rule_total(self, monkeypatch, total: float):
        from lloydk.modules.m5_inference.pipeline import InferencePipeline

        pipe = InferencePipeline(model_dir=None)

        # engine.label은 raise → torch 청크경로 우회 → 단일패스로 lab 사용. lab.rule_result.
        # grade_scores 합이 sparse-evidence 판정의 입력(0 < 합 < floor 이면 발동).
        class _Eng:
            def label(self, _t):
                raise RuntimeError("no engine — force single-pass")

        lab = SimpleNamespace(
            rule_result=SimpleNamespace(grade_scores={"S2": total}, grade=Grade.S2),
            grade=Grade.S2, confidence=1.0, factors=None, evidence=[],
        )

        class _Labeling:
            engine = _Eng()

            def label(self, _t):
                return lab

        monkeypatch.setattr(pipe, "labeling", _Labeling())
        return pipe

    def test_thin_rule_score_emits_warning(self, monkeypatch, _no_db):
        from lloydk import config as cfg

        monkeypatch.setattr(cfg.settings, "rule_fallback_min_evidence", 0.9, raising=False)
        pipe = self._pipe_with_rule_total(monkeypatch, 0.5)  # 0 < 0.5 < 0.9 → 빈약
        res = pipe._run_rule_fallback("본문", return_evidence=False)
        assert any("sparse-evidence" in w for w in res.warnings), res.warnings

    def test_strong_rule_score_no_warning(self, monkeypatch, _no_db):
        from lloydk import config as cfg

        monkeypatch.setattr(cfg.settings, "rule_fallback_min_evidence", 0.9, raising=False)
        pipe = self._pipe_with_rule_total(monkeypatch, 1.5)  # >= floor → 충분
        res = pipe._run_rule_fallback("본문", return_evidence=False)
        assert not any("sparse-evidence" in w for w in res.warnings), res.warnings

    def test_zero_score_no_warning(self, monkeypatch, _no_db):
        # ev==0(무신호)은 conf=0으로 저신뢰 게이트가 잡으므로 sparse-evidence는 0<ev<floor만.
        from lloydk import config as cfg

        monkeypatch.setattr(cfg.settings, "rule_fallback_min_evidence", 0.9, raising=False)
        pipe = self._pipe_with_rule_total(monkeypatch, 0.0)
        res = pipe._run_rule_fallback("본문", return_evidence=False)
        assert not any("sparse-evidence" in w for w in res.warnings), res.warnings


# ---------------------------------------------------------------------------
# 2. 라우팅 — 신호가 있으면 classify가 등급 무변경·needs_review로 라우팅
# ---------------------------------------------------------------------------
class TestGateEscalationRouting:
    def test_sparse_evidence_routes_to_review(self, monkeypatch, _no_db):
        from lloydk.schemas.classify import ClassifyRequest

        svc = _service(monkeypatch)
        monkeypatch.setattr(
            svc.inference, "run",
            lambda *a, **k: _fake_pred(
                label=Grade.S2, confidence=1.0,
                warnings=["sparse-evidence: rule total score 0.50 < 0.90 — thin"],
            ),
        )
        resp = svc.classify(ClassifyRequest(doc_id="not-a-uuid", content="본문 텍스트"))
        assert resp.status == "needs_review"
        assert resp.label == Grade.S2  # 등급 무변경(FNR-safe)
        assert any("sparse-evidence" in w for w in resp.warnings), resp.warnings

    def test_low_confidence_routes_to_review(self, monkeypatch, _no_db):
        from lloydk import config as cfg
        from lloydk.schemas.classify import ClassifyRequest

        svc = _service(monkeypatch)
        monkeypatch.setattr(cfg.settings, "review_confidence_threshold", 0.7, raising=False)
        monkeypatch.setattr(
            svc.inference, "run",
            lambda *a, **k: _fake_pred(label=Grade.S2, confidence=0.4),  # 0.4 < 0.7
        )
        resp = svc.classify(ClassifyRequest(doc_id="not-a-uuid", content="본문 텍스트"))
        assert resp.status == "needs_review"
        assert any("low-confidence" in w for w in resp.warnings), resp.warnings

    def test_high_confidence_not_flagged_low_confidence(self, monkeypatch, _no_db):
        from lloydk import config as cfg
        from lloydk.schemas.classify import ClassifyRequest

        svc = _service(monkeypatch)
        monkeypatch.setattr(cfg.settings, "review_confidence_threshold", 0.7, raising=False)
        monkeypatch.setattr(
            svc.inference, "run",
            lambda *a, **k: _fake_pred(label=Grade.S2, confidence=0.95),  # 0.95 >= 0.7
        )
        resp = svc.classify(ClassifyRequest(doc_id="not-a-uuid", content="본문 텍스트"))
        # 다른 사유로 needs_review일 수는 있어도 low-confidence 사유로는 라우팅되지 않아야 함
        assert not any("low-confidence" in w for w in resp.warnings), resp.warnings
